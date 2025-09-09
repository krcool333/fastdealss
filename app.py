import os
import asyncio
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# List of channel IDs/usernames you want to monitor.
# All IDs must be integers!
SOURCE_GROUPS_INPUTS = [
    -1001315464303,    # Offerzone 2.0
    -1001714047949,    # Trending Loot Deals 2.0
    -1001707571730,    # Offerzone 3.0
    -1001820593092,    # Steadfast Deals
    -1001448358487,    # Yaha Everything
    # You can add more numeric channel IDs here if needed
]

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

async def bot_main():
    await client.start()
    print(f"Monitoring source groups/channels: {SOURCE_GROUPS_INPUTS}")

    @client.on(events.NewMessage(chats=SOURCE_GROUPS_INPUTS))
    async def handler(event):
        try:
            message = event.message.text or event.message.message or ""
            if event.message.media:
                await client.send_file(CHANNEL_ID, event.message.media, caption=message)
            elif message.strip():
                await client.send_message(CHANNEL_ID, message)
            print(f"Sent message: {message[:40]}")
        except Exception as e:
            print(f"Error posting message: {e}")

    print("Bot is running... Listening to source groups.")
    await client.run_until_disconnected()

def start_bot_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_main())

@app.route('/')
def home():
    return "Bot is running!"

if __name__ == '__main__':
    # Start Telegram bot in a separate thread
    new_loop = asyncio.new_event_loop()
    t = Thread(target=start_bot_loop, args=(new_loop,))
    t.start()
    # Start Flask web server on port 10000 or any allowed port
    app.run(host='0.0.0.0', port=10000)
