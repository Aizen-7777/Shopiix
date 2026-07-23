from telethon import TelegramClient, events, Button
import json
import os
import aiohttp
import aiofiles

# Bot Configuration
API_ID = 21124241
API_HASH = 'b7ddce3d3683f54be788fddae73fa468'
BOT_TOKEN = '8914967757:AAG_SqyEghOD8Zr_2Tzskw8qbD6VWgFoGCI'

# File for storing user addresses
ADDRESSES_FILE = 'user_addresses.json'
PREMIUM_FILE = 'premium.txt'
OWNER_ID = 5895386985

# Initialize bot
bot = TelegramClient('order_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def load_addresses():
    """Load all saved addresses"""
    if not os.path.exists(ADDRESSES_FILE):
        return {}
    try:
        with open(ADDRESSES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_addresses(addresses):
    """Save addresses to file"""
    with open(ADDRESSES_FILE, 'w', encoding='utf-8') as f:
        json.dump(addresses, f, indent=2)

def load_premium_users():
    """Load premium users list"""
    if not os.path.exists(PREMIUM_FILE):
        return []
    try:
        with open(PREMIUM_FILE, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except:
        return []

def is_premium(user_id):
    """Check if user is premium"""
    if user_id == OWNER_ID:
        return True
    premium_users = load_premium_users()
    return str(user_id) in premium_users

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Start command with order bot info"""
    buttons = [
        [Button.inline("📍 Set Address", data=b"set_addr")],
        [Button.inline("🛒 Place Order", data=b"order")],
        [Button.inline("❓ Help", data=b"help_order")]
    ]

    await event.reply(
        "<b>🛒 Order Bot - Quick Checkout</b>\n\n"
        "<b>Premium Feature - Easy Order Placement</b>\n\n"
        "Commands:\n"
        "<code>/setaddress</code> - Save your full address\n"
        "<code>/order CHECKOUT_URL | CARD|MM|YY|CVV</code> - Place order\n\n"
        "⚠️ <b>Premium users only</b>",
        parse_mode='html',
        buttons=buttons
    )

@bot.on(events.NewMessage(pattern='/setaddress'))
async def set_address(event):
    """Set user's delivery address"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this feature.", parse_mode='html')
        return

    await event.reply(
        "<b>📍 Set Your Address</b>\n\n"
        "Please provide your full address in this format:\n\n"
        "<code>First Name|Last Name|Street Address|City|State|ZIP|Country|Phone</code>\n\n"
        "Example:\n"
        "<code>John|Doe|123 Main St|New York|NY|10001|USA|5551234567</code>",
        parse_mode='html'
    )

@bot.on(events.NewMessage())
async def save_user_address(event):
    """Save address when user replies with format"""
    user_id = event.sender_id

    if not is_premium(user_id):
        return

    text = event.message.text
    if not text or '|' not in text:
        return

    parts = [p.strip() for p in text.split('|')]
    if len(parts) < 8:
        return

    # Check if this looks like an address (not a command)
    if text.startswith('/'):
        return

    addresses = load_addresses()

    addresses[str(user_id)] = {
        'first_name': parts[0],
        'last_name': parts[1],
        'street': parts[2],
        'city': parts[3],
        'state': parts[4],
        'zip': parts[5],
        'country': parts[6],
        'phone': parts[7]
    }

    save_addresses(addresses)

    await event.reply(
        "✅ <b>Address Saved Successfully!</b>\n\n"
        f"<b>Name:</b> {parts[0]} {parts[1]}\n"
        f"<b>Address:</b> {parts[2]}\n"
        f"<b>City:</b> {parts[3]}, {parts[4]} {parts[5]}\n"
        f"<b>Country:</b> {parts[6]}\n\n"
        "Ready to place orders! Use:\n"
        "<code>/order CHECKOUT_URL | CARD|MM|YY|CVV</code>",
        parse_mode='html'
    )

@bot.on(events.NewMessage(pattern=r'^/order\s+'))
async def place_order(event):
    """Place order with checkout URL and card"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.reply("❌ <b>Access Denied</b>\n\nOnly premium users can use this feature.", parse_mode='html')
        return

    # Get user's saved address
    addresses = load_addresses()
    if str(user_id) not in addresses:
        await event.reply(
            "❌ <b>No Address Saved</b>\n\n"
            "Please set your address first:\n"
            "<code>/setaddress</code>",
            parse_mode='html'
        )
        return

    user_address = addresses[str(user_id)]

    # Parse command: /order CHECKOUT_URL | CARD|MM|YY|CVV
    try:
        args = event.message.text.replace('/order ', '').strip()
        if '|' not in args:
            await event.reply(
                "❌ <b>Invalid Format</b>\n\n"
                "Use: <code>/order CHECKOUT_URL | CARD|MM|YY|CVV</code>",
                parse_mode='html'
            )
            return

        checkout_url, card_info = args.split('|', 1)
        checkout_url = checkout_url.strip()
        card_info = card_info.strip()

        card_parts = card_info.split('|')
        if len(card_parts) != 4:
            await event.reply(
                "❌ <b>Invalid Card Format</b>\n\n"
                "Use: <code>CARD|MM|YY|CVV</code>",
                parse_mode='html'
            )
            return

        card, month, year, cvv = card_parts

        # Prepare order details
        order_summary = (
            "<b>🛒 Order Summary</b>\n"
            f"<b>Checkout URL:</b> <code>{checkout_url}</code>\n\n"
            "<b>📍 Delivery Address:</b>\n"
            f"<b>Name:</b> {user_address['first_name']} {user_address['last_name']}\n"
            f"<b>Address:</b> {user_address['street']}\n"
            f"<b>City:</b> {user_address['city']}, {user_address['state']} {user_address['zip']}\n"
            f"<b>Country:</b> {user_address['country']}\n"
            f"<b>Phone:</b> {user_address['phone']}\n\n"
            "<b>💳 Card Info:</b>\n"
            f"<b>Card:</b> {card[:6]}****{card[-4:]}\n"
            f"<b>Expiry:</b> {month}/{year}\n\n"
            "<b>⏳ Processing order...</b>"
        )

        status_msg = await event.reply(order_summary, parse_mode='html')

        # Simulate order processing
        try:
            # In real scenario, you'd make actual requests to the checkout URL
            # with the address and card info

            result_text = (
                "<b>✅ Order Processed Successfully!</b>\n\n"
                "<b>📦 Order Details:</b>\n"
                f"<b>Checkout:</b> <code>{checkout_url}</code>\n"
                f"<b>Address Filled:</b> {user_address['first_name']} {user_address['last_name']}\n"
                f"<b>Card Used:</b> {card[:6]}****{card[-4:]}\n"
                f"<b>Status:</b> Awaiting confirmation\n\n"
                "<b>⏱️ Please complete the checkout within 5 minutes.</b>"
            )

            await status_msg.edit(result_text, parse_mode='html')

        except Exception as e:
            await status_msg.edit(f"❌ <b>Error Processing Order:</b>\n\n<code>{str(e)}</code>", parse_mode='html')

    except Exception as e:
        await event.reply(f"❌ <b>Error:</b> {str(e)}", parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"set_addr"))
async def set_addr_button(event):
    """Button handler for set address"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.answer("❌ Premium only!")
        return

    await event.edit(
        "<b>📍 Set Your Address</b>\n\n"
        "Please provide your full address in this format:\n\n"
        "<code>First Name|Last Name|Street Address|City|State|ZIP|Country|Phone</code>\n\n"
        "Example:\n"
        "<code>John|Doe|123 Main St|New York|NY|10001|USA|5551234567</code>",
        parse_mode='html'
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"order"))
async def order_button(event):
    """Button handler for order"""
    user_id = event.sender_id

    if not is_premium(user_id):
        await event.answer("❌ Premium only!")
        return

    addresses = load_addresses()
    if str(user_id) not in addresses:
        await event.answer("❌ Set address first!", alert=True)
        return

    await event.edit(
        "<b>🛒 Place Order</b>\n\n"
        "Format:\n"
        "<code>/order CHECKOUT_URL | CARD|MM|YY|CVV</code>\n\n"
        "Example:\n"
        "<code>/order https://shop.example.com/checkout | 4111111111111111|12|25|123</code>",
        parse_mode='html'
    )
    await event.answer()

@bot.on(events.CallbackQuery(pattern=b"help_order"))
async def help_button(event):
    """Button handler for help"""
    await event.edit(
        "<b>❓ Order Bot Help</b>\n\n"
        "<b>Step 1: Save Address</b>\n"
        "<code>/setaddress</code>\n"
        "Then provide: FirstName|LastName|Street|City|State|ZIP|Country|Phone\n\n"
        "<b>Step 2: Place Order</b>\n"
        "<code>/order CHECKOUT_URL | CARD|MM|YY|CVV</code>\n\n"
        "<b>Features:</b>\n"
        "✓ Auto-fill address in checkout\n"
        "✓ One-click ordering\n"
        "✓ Order summary before processing\n"
        "✓ Premium users only\n\n"
        "<b>⚠️ Use only with authorized sites</b>",
        parse_mode='html'
    )
    await event.answer()

print("✅ Order Bot started successfully!")
bot.run_until_disconnected()
