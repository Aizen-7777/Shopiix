from telethon import TelegramClient, events, Button
import asyncio
import aiofiles
import aiohttp
import os
import random
import time
import json
import re
from datetime import datetime

# ── Shopify checkout engine ────────────────────────────────────
from api import (
    process_card,
    fetch_products,
    parse_cc_string,
    extract_clean_response,
)

# ── Premium Custom Emoji IDs ───────────────────────────────────
PREMIUM_EMOJI_IDS = {
    "✅": "6023660820544623088",
    "🔥": "5999340396432333728",
    "❌": "6037570896766438989",
    "⚡": "6026367225466720832",
    "💳": "5971944878815317190",
    "💠": "5971837723676249096",
    "📝": "6023660820544623088",
    "🌐": "6026367225466720832",
    "🎯": "5974235702701853774",
    "🤖": "6057466460886799210",
    "💰": "5971944878815317190",
    "⏸️": "6001440193058444284",
    "▶️": "6285315214673975495",
    "🛑": "5420323339723881652",
    "📊": "5971837723676249096",
    "📦": "6066395745139824604",
    "📋": "5974235702701853774",
    "🔄": "5971837723676249096",
    "🚀": "6282977077427702833",
    "⚠️": "5420323339723881652",
    "💎": "6023660820544623088",
}

def premium_emoji(text):
    if not text:
        return text
    placeholders = []
    result = text
    for i, (emoji, doc_id) in enumerate(PREMIUM_EMOJI_IDS.items()):
        placeholder = f"\x00PE{i:02d}\x00"
        placeholders.append((placeholder, doc_id, emoji))
        result = result.replace(emoji, placeholder)
    for placeholder, doc_id, emoji in placeholders:
        result = result.replace(placeholder, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return result

# ── UI constants ───────────────────────────────────────────────
SEP    = "━━━━━━━━━━━━━━━━━━━━━━━"
HDR    = "<b>⚡ 𝗦𝗛𝗢𝗣𝗜𝗜𝗫 ⚡</b>"
FOOTER = '🤖 <b>Bot By</b>: <a href="tg://user?id=5895386985">Aizen</a>'

# ── Bot config ─────────────────────────────────────────────────
API_ID      = int(os.environ.get('TG_API_ID', '21124241'))
API_HASH    = os.environ.get('TG_API_HASH', '')
BOT_TOKEN   = os.environ.get('TG_BOT_TOKEN', '')
OWNER_ID    = int(os.environ.get('TG_OWNER_ID', '5895386985'))
PREMIUM_FILE = 'premium.txt'
SITES_FILE   = 'sites.txt'
PROXY_FILE   = 'proxy.txt'

bot = TelegramClient('checker_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

active_sessions: dict = {}

# ── File helpers ───────────────────────────────────────────────
def get_file_lines(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

load_premium = lambda: get_file_lines(PREMIUM_FILE)
load_sites   = lambda: get_file_lines(SITES_FILE)
load_proxies = lambda: get_file_lines(PROXY_FILE)

def is_premium(user_id):
    return user_id == OWNER_ID or str(user_id) in load_premium()

def extract_cc(text):
    pattern = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    cards = []
    for card, month, year, cvv in re.findall(pattern, text):
        if len(year) == 2:
            year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards

# ── Card classification ────────────────────────────────────────
def classify(success: bool, msg: str) -> tuple[str, str]:
    """Map engine output to (status_label, emoji)."""
    if not success:
        return 'Dead', '❌'
    if 'ORDER_PLACED' in msg.upper():
        return 'Charged', '✅'
    return 'Approved', '🔥'

# ── Core card check ────────────────────────────────────────────
async def check_card_direct(card_str: str, site: str, proxy_str: str) -> dict:
    try:
        parts = parse_cc_string(card_str)
    except ValueError:
        return {'status': 'Dead', 'emoji': '❌', 'message': 'Invalid card format',
                'card': card_str, 'gateway': 'UNKNOWN', 'price': '0.00'}
    try:
        success, msg, gw, price, _ = await process_card(
            parts['cc'], parts['mes'], parts['ano'], parts['cvv'],
            site, None, proxy_str
        )
        status, emoji = classify(success, msg)
        clean = extract_clean_response(msg)
        return {'status': status, 'emoji': emoji, 'message': clean,
                'card': card_str, 'gateway': gw, 'price': price}
    except Exception as e:
        return {'status': 'Dead', 'emoji': '❌', 'message': str(e)[:120],
                'card': card_str, 'gateway': 'UNKNOWN', 'price': '0.00'}

# ── Site / proxy testers ───────────────────────────────────────
async def test_site(site: str, proxy_str: str) -> dict:
    info = await fetch_products(site, proxy_str)
    if isinstance(info, dict) and info.get('variant_id'):
        return {'site': site, 'status': 'alive'}
    return {'site': site, 'status': 'dead'}

async def test_proxy(proxy_str: str) -> dict:
    probe = "https://riverbendhomedev.myshopify.com"
    info = await fetch_products(probe, proxy_str)
    if isinstance(info, dict) and info.get('variant_id'):
        return {'proxy': proxy_str, 'status': 'alive'}
    return {'proxy': proxy_str, 'status': 'dead'}

# ── BIN lookup ─────────────────────────────────────────────────
async def get_bin_info(card_number: str):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f'https://bins.antipublic.cc/bins/{card_number[:6]}') as r:
                if r.status != 200:
                    return '-', '-', '-', '-', '-', ''
                d = json.loads(await r.text())
                return (d.get('brand', '-'), d.get('type', '-'), d.get('level', '-'),
                        d.get('bank', '-'), d.get('country_name', '-'), d.get('country_flag', ''))
    except Exception:
        return '-', '-', '-', '-', '-', ''

# ── Progress / result messages ─────────────────────────────────
async def send_hit(user_id, result: dict):
    brand, bin_type, level, bank, country, flag = await get_bin_info(result['card'].split('|')[0])
    msg = f"""{HDR}
<b>{SEP}</b>
{result['emoji']} <b>Hit Found — {result['status']}</b>

💳 <b>Card</b>
<blockquote><code>{result['card']}</code></blockquote>
📝 <b>Response</b>
<blockquote>{result['message']}</blockquote>
🌐 <b>Gateway</b>   🔥 {result['gateway']} | 💰 {result['price']}

<b>{SEP}</b>
🏦 <b>BIN Info</b>
<pre>Brand   : {brand} — {bin_type} — {level}
Bank    : {bank}
Country : {country} {flag}</pre>
<b>{SEP}</b>
{FOOTER}"""
    try:
        await bot.send_message(user_id, premium_emoji(msg), parse_mode='html')
    except Exception:
        pass

async def update_progress(user_id, message_id, results: dict, checked: int):
    elapsed = int(time.time() - results['start_time'])
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    gw = (results['charged'] or results['approved'] or [{}])[0].get('gateway', 'Unknown')
    text = f"""{HDR}
<b>{SEP}</b>
📊 <b>Live Progress</b>
<blockquote>✅ Charged   : {len(results['charged'])}
🔥 Approved  : {len(results['approved'])}
❌ Dead       : {len(results['dead'])}
📋 Checked   : {checked} / {results['total']}
🌐 Gateway   : {gw}
⏱️ Time      : {h}h {m}m {s}s</blockquote>
<b>{SEP}</b>"""
    buttons = [
        [Button.inline("⏸️ Pause", b"pause"), Button.inline("▶️ Resume", b"resume")],
        [Button.inline("🛑 Stop", b"stop")]
    ]
    try:
        await bot.edit_message(user_id, message_id, premium_emoji(text), buttons=buttons, parse_mode='html')
    except Exception:
        pass

async def send_final_results(user_id, results: dict):
    elapsed = int(time.time() - results['start_time'])
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    gw = (results['charged'] or results['approved'] or [{}])[0].get('gateway', 'Unknown')

    hits = ''.join(f"✅ <code>{r['card']}</code>\n" for r in results['charged'][:5])
    hits += ''.join(f"🔥 <code>{r['card']}</code>\n" for r in results['approved'][:5])
    hits = hits.strip() or 'No hits found.'

    summary = f"""{HDR}
<b>{SEP}</b>
📊 <b>Final Results</b>
<blockquote>✅ Charged   : {len(results['charged'])}
🔥 Approved  : {len(results['approved'])}
❌ Dead       : {len(results['dead'])}
📋 Total     : {results['total']}
🌐 Gateway   : {gw}
⏱️ Time      : {h}h {m}m {s}s</blockquote>
<b>{SEP}</b>
🎯 <b>Hits</b>
<blockquote>{hits}</blockquote>
<b>{SEP}</b>
{FOOTER}"""

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"shopiix_{user_id}_{ts}.txt"
    async with aiofiles.open(filename, 'w') as f:
        await f.write("=" * 70 + "\n")
        await f.write("⚡ SHOPIIX — CC CHECKER RESULTS ⚡\n")
        await f.write("Format: CC | Gateway | Price | Message | Site\n")
        await f.write("=" * 70 + "\n\n")
        for label, key in [("✅ CHARGED", 'charged'), ("🔥 APPROVED", 'approved'), ("❌ DEAD", 'dead')]:
            await f.write(f"{label} ({len(results[key])}):\n" + "-" * 70 + "\n")
            for r in results[key]:
                await f.write(f"{r['card']} | {r.get('gateway','?')} | {r.get('price','?')} | {r['message'][:100]}\n")
            await f.write("\n")

    await bot.send_message(user_id, premium_emoji(summary), file=filename, parse_mode='html')
    try:
        os.remove(filename)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════

@bot.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    await event.reply(
        premium_emoji(
            f"{HDR}\n"
            f"<b>{SEP}</b>\n"
            "\n"
            "<b>💳 CC Commands</b>\n"
            "<blockquote>• /cc <code>card|mm|yy|cvv</code> — Check single CC\n"
            "• /chk — Reply to .txt file to bulk check</blockquote>\n"
            "\n"
            "<b>🌐 Site Commands</b>\n"
            "<blockquote>• /site — Check & remove dead sites\n"
            "• /rm <code>url</code> — Remove a specific site</blockquote>\n"
            "\n"
            "<b>🔒 Proxy Commands</b>\n"
            "<blockquote>• /proxy — Check & remove dead proxies\n"
            "• /addproxy — Add proxies (one per line)\n"
            "• /chkproxy <code>proxy</code> — Test single proxy\n"
            "• /rmproxy <code>proxy</code> — Remove single proxy\n"
            "• /rmproxyindex <code>1,2,3</code> — Remove by index\n"
            "• /clearproxy — Clear all proxies\n"
            "• /getproxy — List all proxies</blockquote>\n"
            "\n"
            f"<b>{SEP}</b>\n"
            "<b>⚠️ Premium access only.</b>"
        ),
        parse_mode='html'
    )


@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def cmd_cc(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>\n\nOnly premium users can use this bot."), parse_mode='html')
        return

    sites, proxies = load_sites(), load_proxies()
    if not sites:
        await event.reply(premium_emoji("❌ No sites configured. Contact admin."), parse_mode='html')
        return
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies configured. Use /addproxy to add some."), parse_mode='html')
        return

    cards = extract_cc(event.message.text.split(' ', 1)[1].strip())
    if not cards:
        await event.reply(premium_emoji("❌ Invalid format. Use: <code>/cc card|mm|yy|cvv</code>"), parse_mode='html')
        return

    card = cards[0]
    status_msg = await event.reply(
        premium_emoji(
            f"{HDR}\n<b>{SEP}</b>\n"
            f"🔄 <b>Checking card...</b>\n"
            f"<blockquote>💳 <code>{card}</code></blockquote>\n"
            f"<b>{SEP}</b>"
        ),
        parse_mode='html'
    )

    result = await check_card_direct(card, random.choice(sites), random.choice(proxies))
    brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])

    resp = f"""{HDR}
<b>{SEP}</b>
{result['emoji']} <b>Result — {result['status']}</b>

💳 <b>Card</b>
<blockquote><code>{result['card']}</code></blockquote>
📝 <b>Response</b>
<blockquote>{result['message']}</blockquote>
🌐 <b>Gateway</b>   🔥 {result['gateway']} | 💰 {result['price']}

<b>{SEP}</b>
🏦 <b>BIN Info</b>
<pre>Brand   : {brand} — {bin_type} — {level}
Bank    : {bank}
Country : {country} {flag}</pre>
<b>{SEP}</b>
{FOOTER}"""

    await status_msg.edit(premium_emoji(resp), parse_mode='html')


@bot.on(events.NewMessage(pattern='/chk'))
async def cmd_chk(event):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        username = sender.username or f"user_{user_id}"
    except Exception:
        username = f"user_{user_id}"

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>\n\nOnly premium users can use this bot."), parse_mode='html')
        return
    if not event.reply_to_msg_id:
        await event.reply(premium_emoji("❌ Reply to a <code>.txt</code> file containing cards."), parse_mode='html')
        return
    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
        await event.reply(premium_emoji("❌ Please reply to a <code>.txt</code> file."), parse_mode='html')
        return
    if not load_sites():
        await event.reply(premium_emoji("❌ No sites available. Contact admin."), parse_mode='html')
        return
    if not load_proxies():
        await event.reply(premium_emoji("❌ No proxies available. Use /addproxy."), parse_mode='html')
        return

    status_msg = await event.reply(premium_emoji("🔄 Processing your file..."), parse_mode='html')
    file_path = await reply_msg.download_media()

    async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = await f.read()
    cards = extract_cc(content)
    os.remove(file_path)

    if not cards:
        await status_msg.edit(premium_emoji("❌ No valid cards found in file."), parse_mode='html')
        return
    if len(cards) > 500_000:
        cards = cards[:500_000]
        await status_msg.edit(premium_emoji(f"⚠️ Capped at 500,000 cards."), parse_mode='html')

    await status_msg.edit(premium_emoji(f"🚀 Starting check — <b>{len(cards)} cards</b>"), parse_mode='html')

    session_key = f"{user_id}_{status_msg.id}"
    active_sessions[session_key] = {'paused': False}
    results = {'charged': [], 'approved': [], 'dead': [], 'total': len(cards),
               'checked': 0, 'start_time': time.time()}
    sem = asyncio.Semaphore(10)
    last_update = [time.time()]

    async def worker(card):
        if session_key not in active_sessions:
            return
        while active_sessions.get(session_key, {}).get('paused'):
            await asyncio.sleep(1)
            if session_key not in active_sessions:
                return

        current_sites    = load_sites()
        current_proxies  = load_proxies()
        if not current_sites or not current_proxies:
            return

        async with sem:
            res = await check_card_direct(card, random.choice(current_sites), random.choice(current_proxies))

        results['checked'] += 1
        if res['status'] == 'Charged':
            results['charged'].append(res)
            await send_hit(user_id, res)
        elif res['status'] == 'Approved':
            results['approved'].append(res)
            await send_hit(user_id, res)
        else:
            results['dead'].append(res)

        now = time.time()
        if now - last_update[0] >= 1.5 and session_key in active_sessions:
            last_update[0] = now
            try:
                await update_progress(user_id, status_msg.id, results, results['checked'])
            except Exception:
                pass

    try:
        tasks = [asyncio.create_task(worker(c)) for c in cards]
        for task in asyncio.as_completed(tasks):
            if session_key not in active_sessions:
                for t in tasks:
                    t.cancel()
                break
            try:
                await task
            except Exception:
                pass
    except Exception as e:
        await bot.send_message(user_id, premium_emoji(f"❌ Error: {e}"), parse_mode='html')
    finally:
        active_sessions.pop(session_key, None)
        try:
            await status_msg.delete()
        except Exception:
            pass
        await send_final_results(user_id, results)


# ── Proxy management ───────────────────────────────────────────

@bot.on(events.NewMessage(pattern='/proxy'))
async def cmd_proxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ <code>proxy.txt</code> is empty."), parse_mode='html')
        return
    status_msg = await event.reply(premium_emoji(f"🔄 Checking {len(proxies)} proxies..."), parse_mode='html')
    alive, dead = [], []
    for i in range(0, len(proxies), 50):
        batch = proxies[i:i + 50]
        for res in await asyncio.gather(*[test_proxy(p) for p in batch]):
            (alive if res['status'] == 'alive' else dead).append(res['proxy'])
        checked = len(alive) + len(dead)
        await status_msg.edit(
            premium_emoji(
                f"🔄 <b>Checking proxies...</b>\n\n"
                f"<blockquote>📋 Checked : {checked} / {len(proxies)}\n"
                f"✅ Alive   : {len(alive)}\n"
                f"❌ Dead    : {len(dead)}</blockquote>"
            ), parse_mode='html'
        )
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write('\n'.join(alive) + ('\n' if alive else ''))
    await status_msg.edit(
        premium_emoji(
            f"✅ <b>Proxy Check Complete</b>\n\n"
            f"<blockquote>📋 Total   : {len(proxies)}\n"
            f"✅ Alive   : {len(alive)}\n"
            f"❌ Removed : {len(dead)}</blockquote>\n\n"
            f"<code>proxy.txt</code> updated."
        ), parse_mode='html'
    )


@bot.on(events.NewMessage(pattern=r'^/chkproxy\s+'))
async def cmd_chkproxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    proxy = event.message.text.split(' ', 1)[1].strip()
    status_msg = await event.reply(premium_emoji(f"🔄 Checking: <code>{proxy}</code>"), parse_mode='html')
    res = await test_proxy(proxy)
    if res['status'] == 'alive':
        await status_msg.edit(premium_emoji(f"✅ <b>Proxy Alive</b>\n\n<code>{proxy}</code>"), parse_mode='html')
    else:
        await status_msg.edit(premium_emoji(f"❌ <b>Proxy Dead</b>\n\n<code>{proxy}</code>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def cmd_addproxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    lines = event.message.text.split('\n')
    new_proxies = [l.strip() for l in lines[1:] if l.strip()]
    if not new_proxies:
        await event.reply(premium_emoji("❌ Usage: <code>/addproxy</code> followed by proxies, one per line."), parse_mode='html')
        return
    existing = load_proxies()
    to_add = [p for p in new_proxies if p not in existing]
    if not to_add:
        await event.reply(premium_emoji("⚠️ All provided proxies already exist."), parse_mode='html')
        return
    async with aiofiles.open(PROXY_FILE, 'a') as f:
        await f.write('\n'.join(to_add) + '\n')
    await event.reply(premium_emoji(f"✅ <b>Added {len(to_add)} proxies</b>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/rmproxy\s+'))
async def cmd_rmproxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    target = event.message.text.split(' ', 1)[1].strip()
    proxies = load_proxies()
    if target not in proxies:
        await event.reply(premium_emoji(f"❌ Proxy not found: <code>{target}</code>"), parse_mode='html')
        return
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write('\n'.join(p for p in proxies if p != target) + '\n')
    await event.reply(premium_emoji(f"✅ <b>Removed:</b> <code>{target}</code>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/rmproxyindex\s+'))
async def cmd_rmproxyindex(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    try:
        indices = {int(i.strip()) - 1 for i in event.message.text.split(' ', 1)[1].split(',')}
    except ValueError:
        await event.reply(premium_emoji("❌ Usage: <code>/rmproxyindex 1,2,3</code>"), parse_mode='html')
        return
    proxies = load_proxies()
    removed   = [p for i, p in enumerate(proxies) if i in indices]
    remaining = [p for i, p in enumerate(proxies) if i not in indices]
    if not removed:
        await event.reply(premium_emoji("❌ No valid indices found."), parse_mode='html')
        return
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write('\n'.join(remaining) + '\n')
    preview = '\n'.join(removed[:10]) + ('...' if len(removed) > 10 else '')
    await event.reply(premium_emoji(f"✅ <b>Removed {len(removed)} proxies</b>\n\n<code>{preview}</code>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/clearproxy$'))
async def cmd_clearproxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ <code>proxy.txt</code> is already empty."), parse_mode='html')
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"proxy_backup_{user_id}_{ts}.txt"
    async with aiofiles.open(backup, 'w') as f:
        await f.write('\n'.join(proxies) + '\n')
    await event.reply(premium_emoji(f"📦 <b>Backup ({len(proxies)} proxies)</b>"), file=backup, parse_mode='html')
    try:
        os.remove(backup)
    except Exception:
        pass
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write('')
    await event.reply(premium_emoji(f"✅ <b>Cleared {len(proxies)} proxies</b>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/getproxy$'))
async def cmd_getproxy(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies in <code>proxy.txt</code>"), parse_mode='html')
        return
    if len(proxies) <= 50:
        body = '\n'.join(f"{i+1}. <code>{p}</code>" for i, p in enumerate(proxies))
        await event.reply(premium_emoji(f"<b>📋 Proxies ({len(proxies)})</b>\n\n{body}"), parse_mode='html')
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"proxies_{user_id}_{ts}.txt"
        async with aiofiles.open(fname, 'w') as f:
            await f.write('\n'.join(f"{i+1}. {p}" for i, p in enumerate(proxies)))
        await event.reply(premium_emoji(f"<b>📋 Proxies ({len(proxies)})</b>\n\nFile attached."), file=fname, parse_mode='html')
        try:
            os.remove(fname)
        except Exception:
            pass


# ── Site management ────────────────────────────────────────────

@bot.on(events.NewMessage(pattern='/site'))
async def cmd_site(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    sites, proxies = load_sites(), load_proxies()
    if not sites:
        await event.reply(premium_emoji("❌ <code>sites.txt</code> is empty."), parse_mode='html')
        return
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies available."), parse_mode='html')
        return
    status_msg = await event.reply(premium_emoji(f"🔄 Checking {len(sites)} sites..."), parse_mode='html')
    alive, dead = [], []
    for i in range(0, len(sites), 10):
        batch = sites[i:i + 10]
        fresh = load_proxies() or proxies
        for res in await asyncio.gather(*[test_site(s, random.choice(fresh)) for s in batch]):
            (alive if res['status'] == 'alive' else dead).append(res['site'])
        checked = len(alive) + len(dead)
        await status_msg.edit(
            premium_emoji(
                f"🔄 <b>Checking sites...</b>\n\n"
                f"<blockquote>📋 Checked : {checked} / {len(sites)}\n"
                f"✅ Alive   : {len(alive)}\n"
                f"❌ Dead    : {len(dead)}</blockquote>"
            ), parse_mode='html'
        )
    async with aiofiles.open(SITES_FILE, 'w') as f:
        await f.write('\n'.join(alive) + ('\n' if alive else ''))
    await status_msg.edit(
        premium_emoji(
            f"✅ <b>Site Check Complete</b>\n\n"
            f"<blockquote>📋 Total   : {len(sites)}\n"
            f"✅ Alive   : {len(alive)}\n"
            f"❌ Removed : {len(dead)}</blockquote>\n\n"
            f"<code>sites.txt</code> updated."
        ), parse_mode='html'
    )


@bot.on(events.NewMessage(pattern=r'^/rm\s+'))
async def cmd_rm(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>"), parse_mode='html')
        return
    url = event.message.text.split(' ', 1)[1].strip()
    sites = load_sites()
    if url not in sites:
        await event.reply(premium_emoji(f"❌ Site not found: <code>{url}</code>"), parse_mode='html')
        return
    async with aiofiles.open(SITES_FILE, 'w') as f:
        await f.write('\n'.join(s for s in sites if s != url) + '\n')
    await event.reply(premium_emoji(f"✅ <b>Site Removed</b>\n\n<code>{url}</code>"), parse_mode='html')


# ── Inline button callbacks ────────────────────────────────────

@bot.on(events.CallbackQuery(pattern=b"pause"))
async def cb_pause(event):
    key = f"{event.sender_id}_{event.message_id}"
    if key in active_sessions:
        active_sessions[key]['paused'] = True
    await event.answer("⏸️ Paused")

@bot.on(events.CallbackQuery(pattern=b"resume"))
async def cb_resume(event):
    key = f"{event.sender_id}_{event.message_id}"
    if key in active_sessions:
        active_sessions[key]['paused'] = False
    await event.answer("▶️ Resumed")

@bot.on(events.CallbackQuery(pattern=b"stop"))
async def cb_stop(event):
    key = f"{event.sender_id}_{event.message_id}"
    active_sessions.pop(key, None)
    await event.answer("🛑 Stopped")
    await event.edit(premium_emoji("🛑 <b>Checking stopped.</b>"), parse_mode='html')


print("✅ SHOPIIX bot started.")
bot.run_until_disconnected()
