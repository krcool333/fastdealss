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

SOURCE_GROUPS_INPUTS = [
    -1001315464303,  # Offerzone 2.0
    -1001714047949,  # Trending Loot Deals 2.0
    -1001707571730,  # Offerzone 3.0
    -1001820593092,  # Steadfast Deals
    -1001448358487,  # Yaha Everything
    -1001378801949,  # UNIVERSAL DEALS
    -1001387180060,  # Crazy Offers Deals - COD
    -1001361058246,  # QUICK DEALS
]

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

async def resolve_source_groups(inputs):
    resolved = []
    for src in inputs:
        try:
            entity = await client.get_entity(src)
            resolved.append(entity.id)
        except Exception as e:
            print(f"Failed to resolve {src}: {e}")
    return resolved

async def bot_main():
    await client.start()
    source_groups = await resolve_source_groups(SOURCE_GROUPS_INPUTS)
    print(f"Monitoring source groups/channels: {source_groups}")

    @client.on(events.NewMessage(chats=source_groups))
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

@app.route('/ping')
def ping():
    return "pong"  # Respond to uptime monitor pings to keep service alive

if __name__ == '__main__':
    new_loop = asyncio.new_event_loop()
    t = Thread(target=start_bot_loop, args=(new_loop,))
    t.daemon = True  # Ensure thread closes with main process
    t.start()
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=False)
