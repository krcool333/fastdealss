import os
import asyncio
import re
import aiohttp
import time
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# Load env vars
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# Affiliate tags
AMAZON_AFFILIATE_TAG = "lootfastdeals-21"
EARNKARO_USER_ID = "4598441"

# Source channel IDs
SOURCE_GROUPS_INPUTS = [
    -1001315464303, -1001714047949, -1001707571730,
    -1001820593092, -1001448358487, -1001378801949,
    -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947
]

# Patterns for short links
SHORTLINK_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)',
    r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)',
    r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)',
    r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)',
    r'(https?://tinyurl\.com/\S+)'
]

# Keep track of forwarded links to avoid duplicates
seen_links = set()

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

async def expand_url(short_url, session):
    try:
        async with session.head(short_url, allow_redirects=True, timeout=5) as resp:
            return str(resp.url)
    except:
        return short_url

async def expand_all_shortlinks(text):
    links = []
    for pattern in SHORTLINK_PATTERNS:
        links += re.findall(pattern, text)
    if not links:
        return text
    async with aiohttp.ClientSession() as session:
        for url in links:
            full = await expand_url(url, session)
            text = text.replace(url, full)
    return text

def convert_amazon_links(text):
    patterns = [
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))',
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:\?|&)tag=[^&\s]*'
    ]
    def rep(m):
        asin = m.group(2)
        return f"https://www.amazon.in/dp/{asin}/?tag={AMAZON_AFFILIATE_TAG}"
    for p in patterns:
        text = re.sub(p, rep, text, flags=re.IGNORECASE)
    return text

def convert_earnkaro_links(text):
    partners = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)'
    ]
    def rep(m):
        url = m.group(1)
        return f"https://earnkaro.com/store?id={EARNKARO_USER_ID}&url={url}"
    for p in partners:
        text = re.sub(p, rep, text, flags=re.IGNORECASE)
    return text

def convert_all_affiliate_links(text):
    text = convert_amazon_links(text)
    text = convert_earnkaro_links(text)
    return text

async def process_text(text):
    text = await expand_all_shortlinks(text)
    return convert_all_affiliate_links(text)

async def resolve_source_groups(ids):
    out = []
    for i in ids:
        try:
            e = await client.get_entity(i)
            out.append(e.id)
        except:
            pass
    return out

async def bot_main():
    await client.start()
    sources = await resolve_source_groups(SOURCE_GROUPS_INPUTS)
    @client.on(events.NewMessage(chats=sources))
    async def handler(ev):
        global seen_links
        msg = ev.message.message or ev.message.text or ""
        # Skip media-only messages
        if ev.message.media or not msg:
            return
        # Process links
        converted = await process_text(msg)
        # Find all URLs in converted text
        urls = re.findall(r'https?://\S+', converted)
        # Filter unseen
        new_urls = [u for u in urls if u not in seen_links]
        if not new_urls:
            return
        # Add to seen
        seen_links.update(new_urls)
        # Send only once per message
        await client.send_message(CHANNEL_ID, converted)
    await client.run_until_disconnected()

def start_bot_loop(loop):
    retries = 0
    while retries < 5:
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError as e:
            retries += 1
            print(f"TypeNotFoundError, retrying in 10s ({retries}/5)...")
            time.sleep(10)
        except Exception as e:
            print(f"Bot error: {e}")
            break

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/ping')
def ping():
    return "pong"

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    t = Thread(target=start_bot_loop, args=(loop,))
    t.daemon = True
    t.start()
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=False)
