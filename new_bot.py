from telethon import TelegramClient, events, Button
import asyncio
import aiohttp
import aiofiles
import os
import time
import json
import re
from datetime import datetime

# ── Configuration ────────────────────────────────────────────────
API_ID       = 21124241
API_HASH     = 'b7ddce3d3683f54be788fddae73fa468'
BOT_TOKEN    = '8914967757:AAG_SqyEghOD8Zr_2Tzskw8qbD6VWgFoGCI'
OWNER_ID     = 5895386985
API_BASE_URL = 'http://127.0.0.1:5000'  # where API_SRC.PY is running

PREMIUM_FILE = 'premium.txt'
SITES_FILE   = 'sites.txt'
PROXY_FILE   = 'proxy.txt'

# ── Live response codes (card valid, bank declined) ───────────────
_LIVE_CODES = (
    'insufficient_funds', 'do_not_honor', 'card_velocity_exceeded',
    'incorrect_cvc', 'incorrect_zip', 'invalid_cvc',
    'incorrect_number', 'stolen_card', 'lost_card',
    'pickup_card', 'restricted_card', 'security_violation',
    'transaction_not_allowed', 'card_not_supported',
    'mismatched_bill', 'otp_required',
)

bot = TelegramClient('new_checker_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
active_sessions = {}


# ── File helpers ─────────────────────────────────────────────────

def read_lines(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return [l.strip() for l in f if l.strip()]
    except Exception:
        return []

def load_premium():  return read_lines(PREMIUM_FILE)
def load_sites():    return read_lines(SITES_FILE)
def load_proxies():  return read_lines(PROXY_FILE)

def is_premium(user_id):
    return user_id == OWNER_ID or str(user_id) in load_premium()

def extract_cc(text):
    matches = re.findall(r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})', text)
    cards = []
    for card, month, year, cvv in matches:
        if len(year) == 2:
            year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards


# ── BIN lookup ───────────────────────────────────────────────────

async def get_bin_info(card_number):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(f'https://bins.antipublic.cc/bins/{card_number[:6]}') as r:
                if r.status != 200:
                    return '-', '-', '-', '-', '-', ''
                d = await r.json(content_type=None)
                return (
                    d.get('brand', '-'), d.get('type', '-'), d.get('level', '-'),
                    d.get('bank', '-'), d.get('country_name', '-'), d.get('country_flag', '')
                )
    except Exception:
        return '-', '-', '-', '-', '-', ''


# ── API calls ────────────────────────────────────────────────────

async def check_single(card, site, proxy):
    params = {'site': site, 'cc': card, 'proxy': proxy}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
            async with s.get(f'{API_BASE_URL}/shopify', params=params) as r:
                return await r.json(content_type=None)
    except asyncio.TimeoutError:
        return {'Status': False, 'Response': 'Request timed out', 'Gateway': 'UNKNOWN', 'Price': 0.0}
    except Exception as e:
        return {'Status': False, 'Response': str(e), 'Gateway': 'UNKNOWN', 'Price': 0.0}

async def check_batch(cards, site, proxies):
    payload = {'site': site, 'cards': cards, 'proxies': proxies}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as s:
            async with s.post(f'{API_BASE_URL}/batch', json=payload) as r:
                return await r.json(content_type=None)
    except Exception as e:
        return [{'Status': False, 'Response': str(e), 'Gateway': 'UNKNOWN', 'Price': 0.0, 'cc': c} for c in cards]


# ── Status classifier ─────────────────────────────────────────────

def classify(result):
    response = str(result.get('Response', '')).lower()
    status   = result.get('Status', False)

    if not status:
        return 'Dead'
    if 'order_placed' in response or 'payment_successful' in response:
        return 'Charged'
    if any(code in response for code in _LIVE_CODES):
        return 'Approved'
    return 'Dead'


# ── Message builders ─────────────────────────────────────────────

def result_msg(result, brand, bin_type, level, bank, country, flag):
    status = classify(result)
    emoji  = '✅' if status == 'Charged' else ('🔥' if status == 'Approved' else '❌')
    label  = {'Charged': 'Charged', 'Approved': 'Live', 'Dead': 'Dead'}[status]
    price  = result.get('Price', '-')
    price_str = f"${price:.2f}" if isinstance(price, (int, float)) else str(price)

    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} <b>{label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 <code>{result.get('cc', '-')}</code>\n"
        f"📝 <i>{result.get('Response', '-')[:150]}</i>\n"
        f"🌐 {result.get('Gateway', 'UNKNOWN')}  ·  💰 {price_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>BIN Info</b>\n"
        f"<blockquote>{brand} · {bin_type} · {level}\n"
        f"{bank}\n"
        f"{country} {flag}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Bot By:</b> <a href=\"tg://user?id={OWNER_ID}\">Aizen</a>"
    )

def hit_msg(result, brand, bin_type, level, bank, country, flag):
    status = classify(result)
    emoji  = '✅' if status == 'Charged' else '🔥'
    label  = 'Charged' if status == 'Charged' else 'Live'
    price  = result.get('Price', '-')
    price_str = f"${price:.2f}" if isinstance(price, (int, float)) else str(price)

    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} <b>Hit Found — {label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 <code>{result.get('cc', '-')}</code>\n"
        f"📝 <i>{result.get('Response', '-')[:150]}</i>\n"
        f"🌐 {result.get('Gateway', 'UNKNOWN')}  ·  💰 {price_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💠 <b>BIN Info</b>\n"
        f"<blockquote>{brand} · {bin_type} · {level}\n"
        f"{bank}\n"
        f"{country} {flag}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Bot By:</b> <a href=\"tg://user?id={OWNER_ID}\">Aizen</a>"
    )

def progress_msg(results, checked, total, elapsed):
    h, rem = divmod(int(elapsed), 3600)
    m, s   = divmod(rem, 60)
    gw = (
        results['charged'][0]['Gateway'] if results['charged'] else
        results['approved'][0]['Gateway'] if results['approved'] else 'UNKNOWN'
    )
    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Progress</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>"
        f"✅ Charged: {len(results['charged'])}  🔥 Live: {len(results['approved'])}  ❌ Dead: {len(results['dead'])}\n"
        f"📊 Checked: {checked}/{total}\n"
        f"🌐 Gateway: {gw}\n"
        f"⏱️ Time: {h}h {m}m {s}s"
        f"</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ── /start ───────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply(
        "💠 <b>Shopiix Checker</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "💳 <b>Cards</b>\n"
        "<blockquote>/cc <code>card|mm|yy|cvv</code> — Single check\n"
        "/chk — Bulk check (reply to .txt)</blockquote>\n\n"
        "🌐 <b>Sites</b>\n"
        "<blockquote>/site — Check & clean dead sites\n"
        "/rm <code>url</code> — Remove a site</blockquote>\n\n"
        "🔄 <b>Proxies</b>\n"
        "<blockquote>/proxy — Check & clean dead proxies\n"
        "/addproxy — Add proxies (one per line)\n"
        "/chkproxy <code>proxy</code> — Test single proxy\n"
        "/rmproxy <code>proxy</code> — Remove a proxy\n"
        "/rmproxyindex <code>1,2,3</code> — Remove by index\n"
        "/clearproxy — Clear all proxies\n"
        "/getproxy — List all proxies</blockquote>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>Premium access required.</i>",
        parse_mode='html'
    )


# ── /cc single check ─────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def single_cc(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply("❌ <b>Access Denied</b>\n\n<i>Premium access required.</i>", parse_mode='html')
        return

    sites   = load_sites()
    proxies = load_proxies()
    if not sites:
        await event.reply("❌ No sites configured. Contact admin.", parse_mode='html')
        return
    if not proxies:
        await event.reply("❌ No proxies configured. Use /addproxy.", parse_mode='html')
        return

    cards = extract_cc(event.message.text.split(' ', 1)[1])
    if not cards:
        await event.reply("❌ Invalid format. Use: <code>/cc card|mm|yy|cvv</code>", parse_mode='html')
        return

    card     = cards[0]
    site     = sites[0]
    proxy    = proxies[0]

    status_msg = await event.reply(
        f"⏳ <b>Checking...</b>\n<code>{card}</code>",
        parse_mode='html'
    )

    try:
        result = await check_single(card, site, proxy)
        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])
        await status_msg.edit(result_msg(result, brand, bin_type, level, bank, country, flag), parse_mode='html')
    except Exception as e:
        await status_msg.edit(f"❌ Error: {e}", parse_mode='html')


# ── /chk bulk check ──────────────────────────────────────────────

@bot.on(events.NewMessage(pattern='/chk'))
async def bulk_check(event):
    user_id = event.sender_id
    if not is_premium(user_id):
        await event.reply("❌ <b>Access Denied</b>\n\n<i>Premium access required.</i>", parse_mode='html')
        return
    if not event.reply_to_msg_id:
        await event.reply("❌ Reply to a <code>.txt</code> file containing cards.", parse_mode='html')
        return

    reply = await event.get_reply_message()
    if not reply.file or not reply.file.name.endswith('.txt'):
        await event.reply("❌ Please reply to a <code>.txt</code> file.", parse_mode='html')
        return

    sites   = load_sites()
    proxies = load_proxies()
    if not sites:
        await event.reply("❌ No sites configured. Contact admin.", parse_mode='html')
        return
    if not proxies:
        await event.reply("❌ No proxies configured. Use /addproxy.", parse_mode='html')
        return

    status_msg = await event.reply("⏳ <b>Processing file...</b>", parse_mode='html')
    file_path  = await reply.download_media()

    async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = await f.read()
    cards = extract_cc(content)
    os.remove(file_path)

    if not cards:
        await status_msg.edit("❌ No valid cards found in file.", parse_mode='html')
        return
    if len(cards) > 50000:
        cards = cards[:50000]

    total = len(cards)
    await status_msg.edit(f"⏳ <b>Starting check for {total} cards...</b>", parse_mode='html')

    session_key = f"{user_id}_{status_msg.id}"
    active_sessions[session_key] = {'paused': False}

    all_results = {'charged': [], 'approved': [], 'dead': [], 'total': total, 'start_time': time.time()}

    try:
        BATCH_SIZE = 50
        checked    = 0
        last_edit  = time.time()

        for i in range(0, total, BATCH_SIZE):
            if session_key not in active_sessions:
                break

            # Honour pause
            while active_sessions.get(session_key, {}).get('paused'):
                await asyncio.sleep(1)
                if session_key not in active_sessions:
                    break

            batch   = cards[i:i + BATCH_SIZE]
            site    = sites[0]
            results = await check_batch(batch, site, proxies)

            if not isinstance(results, list):
                continue

            for res in results:
                status = classify(res)
                if status == 'Charged':
                    all_results['charged'].append(res)
                    bin_info = await get_bin_info(res.get('cc', '').split('|')[0])
                    await bot.send_message(user_id, hit_msg(res, *bin_info), parse_mode='html')
                elif status == 'Approved':
                    all_results['approved'].append(res)
                    bin_info = await get_bin_info(res.get('cc', '').split('|')[0])
                    await bot.send_message(user_id, hit_msg(res, *bin_info), parse_mode='html')
                else:
                    all_results['dead'].append(res)

            checked = min(i + BATCH_SIZE, total)
            elapsed = time.time() - all_results['start_time']

            if time.time() - last_edit >= 2.0:
                last_edit = time.time()
                buttons = [
                    [Button.inline("⏸️ Pause", b"pause"), Button.inline("▶️ Resume", b"resume")],
                    [Button.inline("🛑 Stop", b"stop")]
                ]
                try:
                    await bot.edit_message(
                        user_id, status_msg.id,
                        progress_msg(all_results, checked, total, elapsed),
                        buttons=buttons, parse_mode='html'
                    )
                except Exception:
                    pass

    except Exception as e:
        await bot.send_message(user_id, f"❌ Error: {e}", parse_mode='html')
    finally:
        active_sessions.pop(session_key, None)
        try:
            await status_msg.delete()
        except Exception:
            pass
        await send_final(user_id, all_results)


# ── Final summary ─────────────────────────────────────────────────

async def send_final(user_id, results):
    elapsed = int(time.time() - results['start_time'])
    h, rem  = divmod(elapsed, 3600)
    m, s    = divmod(rem, 60)

    hits_text = ""
    for r in results['charged'][:5]:
        hits_text += f"✅ <code>{r.get('cc', '-')}</code>\n"
    for r in results['approved'][:5]:
        hits_text += f"🔥 <code>{r.get('cc', '-')}</code>\n"
    if not hits_text:
        hits_text = "<i>No hits found</i>"

    gw = (
        results['charged'][0]['Gateway'] if results['charged'] else
        results['approved'][0]['Gateway'] if results['approved'] else 'UNKNOWN'
    )

    summary = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Final Results</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"<blockquote>"
        f"✅ Charged: {len(results['charged'])}  🔥 Live: {len(results['approved'])}  ❌ Dead: {len(results['dead'])}\n"
        f"📊 Total: {results['total']}\n"
        f"🌐 Gateway: {gw}\n"
        f"⏱️ Time: {h}h {m}m {s}s"
        f"</blockquote>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Hits</b>\n"
        f"<blockquote>{hits_text}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Bot By:</b> <a href=\"tg://user?id={OWNER_ID}\">Aizen</a>"
    )

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"results_{user_id}_{ts}.txt"

    async with aiofiles.open(filename, 'w') as f:
        await f.write("=" * 60 + "\n")
        await f.write("SHOPIIX CHECKER RESULTS\n")
        await f.write("=" * 60 + "\n\n")
        await f.write(f"CHARGED ({len(results['charged'])}):\n" + "-" * 60 + "\n")
        for r in results['charged']:
            await f.write(f"{r.get('cc')} | {r.get('Gateway')} | ${r.get('Price')} | {r.get('Response')}\n")
        await f.write(f"\nLIVE ({len(results['approved'])}):\n" + "-" * 60 + "\n")
        for r in results['approved']:
            await f.write(f"{r.get('cc')} | {r.get('Gateway')} | ${r.get('Price')} | {r.get('Response')}\n")
        await f.write(f"\nDEAD ({len(results['dead'])}):\n" + "-" * 60 + "\n")
        for r in results['dead']:
            await f.write(f"{r.get('cc')} | {r.get('Gateway')} | {r.get('Response')}\n")

    await bot.send_message(user_id, summary, file=filename, parse_mode='html')
    try:
        os.remove(filename)
    except Exception:
        pass


# ── Pause / Resume / Stop callbacks ──────────────────────────────

@bot.on(events.CallbackQuery(pattern=b"pause"))
async def pause_cb(event):
    key = f"{event.sender_id}_{event.message_id}"
    if key in active_sessions:
        active_sessions[key]['paused'] = True
        await event.answer("⏸️ Paused")

@bot.on(events.CallbackQuery(pattern=b"resume"))
async def resume_cb(event):
    key = f"{event.sender_id}_{event.message_id}"
    if key in active_sessions:
        active_sessions[key]['paused'] = False
        await event.answer("▶️ Resumed")

@bot.on(events.CallbackQuery(pattern=b"stop"))
async def stop_cb(event):
    key = f"{event.sender_id}_{event.message_id}"
    if key in active_sessions:
        del active_sessions[key]
        await event.answer("🛑 Stopped")
        await event.edit("🛑 <b>Checking stopped.</b>", parse_mode='html')


# ── Proxy commands ────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r'^/chkproxy\s+'))
async def chk_proxy(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    proxy = event.message.text.split(' ', 1)[1].strip()
    msg   = await event.reply(f"🔄 Testing proxy...", parse_mode='html')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(f'{API_BASE_URL}/health', proxy=f"http://{proxy}") as r:
                ok = r.status == 200
    except Exception:
        ok = False
    status = "✅ <b>Alive</b>" if ok else "❌ <b>Dead</b>"
    await msg.edit(f"{status}\n<code>{proxy}</code>", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def add_proxy(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    lines = event.message.text.split('\n')[1:]
    new_p = [l.strip() for l in lines if l.strip()]
    if not new_p:
        await event.reply("❌ Usage: <code>/addproxy</code> followed by proxies, one per line.", parse_mode='html'); return
    existing = set(load_proxies())
    added    = [p for p in new_p if p not in existing]
    if not added:
        await event.reply("⚠️ All proxies already exist.", parse_mode='html'); return
    async with aiofiles.open(PROXY_FILE, 'a') as f:
        for p in added:
            await f.write(f"{p}\n")
    await event.reply(f"✅ Added <b>{len(added)}</b> proxies.", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmproxy\s+'))
async def rm_proxy(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    target   = event.message.text.split(' ', 1)[1].strip()
    proxies  = load_proxies()
    if target not in proxies:
        await event.reply(f"❌ Proxy not found: <code>{target}</code>", parse_mode='html'); return
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        for p in proxies:
            if p != target:
                await f.write(f"{p}\n")
    await event.reply(f"✅ Removed: <code>{target}</code>", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmproxyindex\s+'))
async def rm_proxy_index(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    try:
        indices = [int(i.strip()) - 1 for i in event.message.text.split(' ', 1)[1].split(',')]
    except ValueError:
        await event.reply("❌ Usage: <code>/rmproxyindex 1,2,3</code>", parse_mode='html'); return
    proxies  = load_proxies()
    kept     = [p for i, p in enumerate(proxies) if i not in indices]
    removed  = len(proxies) - len(kept)
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        for p in kept:
            await f.write(f"{p}\n")
    await event.reply(f"✅ Removed <b>{removed}</b> proxies.", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/clearproxy$'))
async def clear_proxy(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    proxies = load_proxies()
    if not proxies:
        await event.reply("❌ <code>proxy.txt</code> is already empty.", parse_mode='html'); return
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup   = f"proxy_backup_{event.sender_id}_{ts}.txt"
    async with aiofiles.open(backup, 'w') as f:
        for p in proxies:
            await f.write(f"{p}\n")
    await event.reply(f"📦 Backup of {len(proxies)} proxies attached.", file=backup, parse_mode='html')
    try:
        os.remove(backup)
    except Exception:
        pass
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        await f.write("")
    await event.reply(f"✅ Cleared <b>{len(proxies)}</b> proxies.", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/getproxy$'))
async def get_proxy(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    proxies = load_proxies()
    if not proxies:
        await event.reply("❌ <code>proxy.txt</code> is empty.", parse_mode='html'); return
    if len(proxies) <= 50:
        lines = "\n".join(f"{i+1}. <code>{p}</code>" for i, p in enumerate(proxies))
        await event.reply(f"<b>Proxies ({len(proxies)}):</b>\n\n{lines}", parse_mode='html')
    else:
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"proxies_{event.sender_id}_{ts}.txt"
        async with aiofiles.open(name, 'w') as f:
            for i, p in enumerate(proxies):
                await f.write(f"{i+1}. {p}\n")
        await event.reply(f"<b>Proxies ({len(proxies)}):</b> File attached.", file=name, parse_mode='html')
        try:
            os.remove(name)
        except Exception:
            pass

@bot.on(events.NewMessage(pattern=r'^/proxy$'))
async def check_proxies(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    proxies = load_proxies()
    if not proxies:
        await event.reply("❌ <code>proxy.txt</code> is empty.", parse_mode='html'); return
    msg = await event.reply(f"🔄 Checking {len(proxies)} proxies...", parse_mode='html')
    alive, dead = [], []
    for proxy in proxies:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(f'{API_BASE_URL}/health', proxy=f"http://{proxy}") as r:
                    (alive if r.status == 200 else dead).append(proxy)
        except Exception:
            dead.append(proxy)
    async with aiofiles.open(PROXY_FILE, 'w') as f:
        for p in alive:
            await f.write(f"{p}\n")
    await msg.edit(
        f"✅ <b>Proxy check complete</b>\n\n"
        f"<blockquote>Alive: {len(alive)}\nRemoved: {len(dead)}</blockquote>",
        parse_mode='html'
    )


# ── Site commands ─────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r'^/rm\s+'))
async def rm_site(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    url   = event.message.text.split(' ', 1)[1].strip()
    sites = load_sites()
    if url not in sites:
        await event.reply(f"❌ Site not found: <code>{url}</code>", parse_mode='html'); return
    async with aiofiles.open(SITES_FILE, 'w') as f:
        for s in sites:
            if s != url:
                await f.write(f"{s}\n")
    await event.reply(f"✅ Removed: <code>{url}</code>", parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/site$'))
async def check_sites(event):
    if not is_premium(event.sender_id):
        await event.reply("❌ <b>Access Denied</b>", parse_mode='html'); return
    sites   = load_sites()
    proxies = load_proxies()
    if not sites:
        await event.reply("❌ <code>sites.txt</code> is empty.", parse_mode='html'); return
    if not proxies:
        await event.reply("❌ No proxies available.", parse_mode='html'); return

    msg = await event.reply(f"🔄 Checking {len(sites)} sites...", parse_mode='html')
    alive, dead = [], []

    for site in sites:
        result = await check_single("4111111111111111|12|2030|123", site, proxies[0])
        resp   = str(result.get('Response', '')).lower()
        if any(e in resp for e in ['timeout', 'proxy error', 'connection', 'invalid proxy']):
            dead.append(site)
        else:
            alive.append(site)

    async with aiofiles.open(SITES_FILE, 'w') as f:
        for s in alive:
            await f.write(f"{s}\n")

    await msg.edit(
        f"✅ <b>Site check complete</b>\n\n"
        f"<blockquote>Alive: {len(alive)}\nRemoved: {len(dead)}</blockquote>",
        parse_mode='html'
    )


print("✅ New bot started!")
bot.run_until_disconnected()
