import os
import asyncio
import re
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

# Your Amazon affiliate tag
AFFILIATE_TAG = "lootfastdeals-21"

SOURCE_GROUPS_INPUTS = [
    -1001315464303,  # Offerzone 2.0
    -1001714047949,  # Trending Loot Deals 2.0
    -1001707571730,  # Offerzone 3.0
    -1001820593092,  # Steadfast Deals
    -1001448358487,  # Yaha Everything
    -1001378801949,  # UNIVERSAL DEALS
    -1001387180060,  # Crazy Offers Deals - COD
    -1001361058246,  # QUICK DEALS
    -1001561964907,  # NEW SOURCE 1
    -1002444882171,  # NEW SOURCE 2
    -1001505338947   # NEW SOURCE 3
]

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

def convert_amazon_links(text):
    """
    Convert Amazon links to use your affiliate tag
    """
    if not text:
        return text
    
    # Patterns for different Amazon URL formats
    patterns = [
        # Standard Amazon URLs with /dp/ or /gp/product/
        r'(https?://(?:www\.)?amazon\.(?:com|in|co\.uk|de|fr|es|it|ca|com\.au|co\.jp)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:[/?].*?)?(?=\s|$|[^\w\-])',
        # Short Amazon URLs (amzn.to, amzn.in, etc.)
        r'(https?://(?:amzn\.to|amzn\.in|amzn\.eu)/([A-Z0-9]{8,}))',
        # Amazon links with existing affiliate tags
        r'(https?://(?:www\.)?amazon\.(?:com|in|co\.uk|de|fr|es|it|ca|com\.au|co\.jp)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:[/?].*?)?(?:\?|&)tag=[^&\s]*'
    ]
    
    def replace_link(match):
        # Extract ASIN (Amazon product ID)
        if len(match.groups()) >= 2:
            asin = match.group(2)
            # Create clean affiliate link
            return f"https://www.amazon.in/dp/{asin}/?tag={AFFILIATE_TAG}"
        return match.group(0)
    
    # Apply patterns to replace Amazon links
    for pattern in patterns:
        text = re.sub(pattern, replace_link, text, flags=re.IGNORECASE)
    
    return text

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
            
            # Convert Amazon links to use your affiliate tag
            converted_message = convert_amazon_links(message)
            
            if event.message.media:
                await client.send_file(CHANNEL_ID, event.message.media, caption=converted_message)
            elif converted_message.strip():
                await client.send_message(CHANNEL_ID, converted_message)
            
            print(f"Sent message with converted links: {converted_message[:40]}")
        except Exception as e:
            print(f"Error posting message: {e}")

    print("Bot is running... Listening to source groups.")
    await client.run_until_disconnected()

def start_bot_loop(loop):
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_main())
    except Exception as e:
        print(f"Bot thread exception: {type(e).__name__}: {e}")

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/ping')
def ping():
    return "pong"

if __name__ == '__main__':
    new_loop = asyncio.new_event_loop()
    t = Thread(target=start_bot_loop, args=(new_loop,))
    t.daemon = True
    t.start()
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=False)
