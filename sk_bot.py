"""
Standalone SK Stripe Card Checker Bot
Commands:
  /start       — welcome
  /setsk <key> — set your Stripe secret key (owner only)
  /chk <card>  — check card: 4111111111111111|12|2028|123
  /info        — show current SK (masked) and settings
"""
import asyncio, aiohttp, json, os
from telethon import TelegramClient, events

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_ID    = int(os.environ.get('TG_API_ID',   '32253547'))
API_HASH  = os.environ.get('TG_API_HASH',     '868242502bea6a1e41b2ce46001d0580')
BOT_TOKEN = os.environ.get('SK_BOT_TOKEN',    '8748861237:AAHmW5CGflPCj1NCJQkN3W4gEIVS4yGDvgU')
OWNER_ID  = int(os.environ.get('TG_OWNER_ID', '5895386985'))
CURRENCY  = "usd"
AMOUNT    = 50   # cents — $0.50
# ─────────────────────────────────────────────────────────────────────────────

_SK = ""   # set via /setsk at runtime

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


async def check_card_api(card_str: str):
    global _SK
    parts = card_str.strip().split("|")
    if len(parts) != 4:
        return None, "Bad format. Use: number|MM|YYYY|CVV"

    number, month, year, cvv = parts
    headers = {
        "Authorization": f"Bearer {_SK}",
        "Content-Type":  "application/x-www-form-urlencoded",
        "User-Agent":    UA,
    }

    async with aiohttp.ClientSession() as s:
        # Step 1 — Create PaymentMethod
        pm_data = {
            "type":              "card",
            "card[number]":      number,
            "card[exp_month]":   month,
            "card[exp_year]":    year,
            "card[cvc]":         cvv,
            "billing_details[name]": "John Doe",
        }
        async with s.post("https://api.stripe.com/v1/payment_methods",
                          headers=headers, data=pm_data) as r:
            pm_resp = await r.json()

        if "error" in pm_resp:
            code = pm_resp["error"].get("code", "")
            msg  = pm_resp["error"].get("message", "Unknown")
            status = "DEAD ❌" if code in _DEAD_CODES else f"ERROR ⚠️ ({code})"
            return status, msg

        pm_id = pm_resp["id"]

        # Step 2 — Create + Confirm PaymentIntent
        pi_data = {
            "amount":              str(AMOUNT),
            "currency":            CURRENCY,
            "payment_method":      pm_id,
            "confirmation_method": "manual",
            "confirm":             "true",
            "capture_method":      "automatic",
        }
        async with s.post("https://api.stripe.com/v1/payment_intents",
                          headers=headers, data=pi_data) as r:
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
        "`/chk 4111111111111111|12|2028|123` — check card\n"
        "`/info` — show current settings"
    )


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
    _SK = key
    await e.respond(f"✅ SK set: `{key[:20]}...`")


@bot.on(events.NewMessage(pattern=r'^/info'))
async def on_info(e):
    if e.sender_id != OWNER_ID:
        await e.respond("❌ Owner only.")
        return
    sk_display = f"`{_SK[:20]}...`" if _SK else "Not set"
    await e.respond(
        f"**SK Checker Info**\n\n"
        f"SK       : {sk_display}\n"
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


print("SK Checker Bot running...")
bot.run_until_disconnected()
