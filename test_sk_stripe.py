"""
Stripe SK-based card checker — uses your own Stripe Secret Key.
Usage: python3 test_sk_stripe.py
"""
import asyncio, aiohttp, json

# ── CHANGE THESE ─────────────────────────────────────────────────────────────
SK       = "YOUR_STRIPE_SECRET_KEY_HERE"   # paste your sk_live_xxx here
CURRENCY = "usd"                                # usd / inr / gbp etc
AMOUNT   = 100                                  # in smallest unit (100 = $1 / ₹1)

CARDS = [
    "4111111111111111|12|2028|123",
    # add more cards here
]
# ─────────────────────────────────────────────────────────────────────────────

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
    "invalid_cvc", "expired_card", "card_declined",
    "incorrect_number",
}


def classify(resp: dict) -> tuple[str, str]:
    """Returns (status, message)"""
    if resp.get("status") in ("succeeded", "requires_capture"):
        return "CHARGED", "Payment succeeded"

    err = resp.get("error") or {}
    if not err and resp.get("last_payment_error"):
        err = resp["last_payment_error"]

    code    = err.get("decline_code") or err.get("code") or ""
    message = err.get("message", "Unknown error")

    # 3DS required → card is real (live)
    if resp.get("status") == "requires_action" or "authentication" in message.lower():
        return "LIVE (3DS)", message

    if code in _LIVE_CODES:
        return "LIVE", f"{code} — {message}"

    if code in _DEAD_CODES:
        return "DEAD", f"{code} — {message}"

    return f"UNKNOWN ({code})", message


async def check_card(session: aiohttp.ClientSession, card_str: str):
    parts = card_str.strip().split("|")
    if len(parts) != 4:
        print(f"[SKIP] Bad format: {card_str}")
        return

    number, month, year, cvv = parts
    headers = {
        "Authorization": f"Bearer {SK}",
        "Content-Type":  "application/x-www-form-urlencoded",
        "User-Agent":    UA,
    }

    # ── Step 1: Create PaymentMethod ─────────────────────────────────────────
    pm_data = {
        "type":              "card",
        "card[number]":      number,
        "card[exp_month]":   month,
        "card[exp_year]":    year,
        "card[cvc]":         cvv,
        "billing_details[name]": "John Doe",
    }
    async with session.post("https://api.stripe.com/v1/payment_methods",
                            headers=headers, data=pm_data) as r:
        pm_resp = await r.json()

    if "error" in pm_resp:
        code = pm_resp["error"].get("code", "")
        msg  = pm_resp["error"].get("message", "")
        status = "DEAD" if code in _DEAD_CODES else f"ERROR ({code})"
        print(f"[{status}] {number}|{month}|{year}|{cvv}  →  {msg}")
        return

    pm_id = pm_resp["id"]

    # ── Step 2: Create PaymentIntent ─────────────────────────────────────────
    pi_data = {
        "amount":               str(AMOUNT),
        "currency":             CURRENCY,
        "payment_method":       pm_id,
        "confirmation_method":  "manual",
        "confirm":              "true",
        "capture_method":       "automatic",
    }
    async with session.post("https://api.stripe.com/v1/payment_intents",
                            headers=headers, data=pi_data) as r:
        pi_resp = await r.json()

    print(f"\n─── {number}|{month}|{year}|{cvv} ───")
    print(f"PaymentIntent status : {pi_resp.get('status', 'N/A')}")
    if pi_resp.get("last_payment_error"):
        e = pi_resp["last_payment_error"]
        print(f"Decline code         : {e.get('decline_code') or e.get('code', '')}")
        print(f"Message              : {e.get('message', '')}")
    elif "error" in pi_resp:
        e = pi_resp["error"]
        print(f"Error code           : {e.get('decline_code') or e.get('code', '')}")
        print(f"Message              : {e.get('message', '')}")

    status, msg = classify(pi_resp)
    print(f"Result               : {status}")
    print(f"Detail               : {msg}")


async def main():
    if SK.startswith("sk_live_XXXX"):
        print("[!] Set your SK in the SK variable first.")
        return

    print(f"SK       : {SK[:20]}...")
    print(f"Currency : {CURRENCY}  Amount: {AMOUNT}")
    print(f"Cards    : {len(CARDS)}")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
        for card in CARDS:
            await check_card(session, card)
            await asyncio.sleep(0.5)   # small delay between cards

asyncio.run(main())
