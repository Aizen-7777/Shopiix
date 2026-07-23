"""
Run this ONCE on your PC to get the SESSION_STRING for Railway.
No phone number needed - it uses the bot token only.

  pip install telethon
  python gen_session.py

Copy the printed string and set it as SESSION_STRING env var on Railway.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID   = 32253547
API_HASH = '868242502bea6a1e41b2ce46001d0580'
BOT_TOKEN = '8692888647:AAGBRVuhOBnNe5jIi71o7sLBAYOY6JBsevQ'

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    client.start(bot_token=BOT_TOKEN)
    session_string = client.session.save()

print("\n" + "="*60)
print("YOUR SESSION STRING (paste into Railway as SESSION_STRING):")
print("="*60)
print(session_string)
print("="*60 + "\n")
