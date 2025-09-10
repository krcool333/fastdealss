import os
import asyncio
import re
import aiohttp
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

# Your affiliate identifiers
AMAZON_AFFILIATE_TAG = "lootfastdeals-21"
EARNKARO_USER_ID = "4598441"

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

# Short URL patterns that need expansion
SHORTLINK_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)',
    r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)',
    r'(https?://amzn\.to/\S+)',
    r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)',
    r'(https?://tinyurl\.com/\S+)'
]

async def expand_url(short_url, session):
    """Expand a short URL to its full destination"""
    try:
        async with session.head(short_url, allow_redirects=True, timeout=5) as resp:
            expanded_url = str(resp.url)
            print(f"Expanded: {short_url} -> {expanded_url}")
            return expanded_url
    except Exception as e:
        print(f"Error expanding {short_url}: {e}")
        return short_url

async def expand_all_shortlinks(text):
    """Find and expand all short links in text"""
    if not text:
        return text
    
    # Find all short links
    shortlinks = []
    for pattern in SHORTLINK_PATTERNS:
        matches = re.findall(pattern, text)
        shortlinks.extend(matches)
    
    if not shortlinks:
        return text
    
    # Expand all short links
    async with aiohttp.ClientSession() as session:
        expanded_text = text
        for short_link in shortlinks:
            expanded_link = await expand_url(short_link, session)
            expanded_text = expanded_text.replace(short_link, expanded_link)
    
    return expanded_text

def convert_amazon_links(text):
    """Convert Amazon links to use your affiliate tag"""
    if not text:
        return text
    
    patterns = [
        r'(https?://(?:www\.)?amazon\.(?:com|in|co\.uk|de|fr|es|it|ca|com\.au|co\.jp)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:[/?].*?)?(?=\s|$|[^\w\-])',
        r'(https?://(?:www\.)?amazon\.(?:com|in|co\.uk|de|fr|es|it|ca|com\.au|co\.jp)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:[/?].*?)?(?:\?|&)tag=[^&\s]*'
    ]
    
    def replace_amazon_link(match):
        if len(match.groups()) >= 2:
            asin = match.group(2)
            return f"https://www.amazon.in/dp/{asin}/?tag={AMAZON_AFFILIATE_TAG}"
        return match.group(0)
    
    for pattern in patterns:
        text = re.sub(pattern, replace_amazon_link, text, flags=re.IGNORECASE)
    
    return text

def convert_earnkaro_links(text):
    """Convert Flipkart/Myntra/other partner links to EarnKaro affiliate links"""
    if not text:
        return text
    
    # Patterns for various e-commerce sites
    patterns = [
        # Flipkart patterns
        r'(https?://(?:www\.)?flipkart\.com/[^\s]+)',
        r'(https?://(?:dl\.)?flipkart\.com/[^\s]+)',
        # Myntra patterns
        r'(https?://(?:www\.)?myntra\.com/[^\s]+)',
        # Ajio patterns
        r'(https?://(?:www\.)?ajio\.com/[^\s]+)',
        # Nykaa patterns
        r'(https?://(?:www\.)?nykaa\.com/[^\s]+)',
        # Meesho patterns
        r'(https?://(?:www\.)?meesho\.com/[^\s]+)',
        # FirstCry patterns
        r'(https?://(?:www\.)?firstcry\.com/[^\s]+)'
    ]
    
    def replace_earnkaro_link(match):
        original_link = match.group(1)
        earnkaro_link = f"https://earnkaro.com/store?id={EARNKARO_USER_ID}&url={original_link}"
        return earnkaro_link
    
    for pattern in patterns:
        text = re.sub(pattern, replace_earnkaro_link, text, flags=re.IGNORECASE)
    
    return text

def convert_all_affiliate_links(text):
    """Convert both Amazon and EarnKaro partner links"""
    text = convert_amazon_links(text)
    text = convert_earnkaro_links(text)
    return text

async def process_message_with_affiliates(message):
    """Complete pipeline: expand short links then convert to affiliate links"""
    # Step 1: Expand all short URLs
    expanded_message = await expand_all_shortlinks(message)
    
    # Step 2: Convert expanded URLs to affiliate links
    affiliate_message = convert_all_affiliate_links(expanded_message)
    
    return affiliate_message

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
            
            # Complete affiliate processing pipeline
            converted_message = await process_message_with_affiliates(message)
            
            if event.message.media:
                await client.send_file(CHANNEL_ID, event.message.media, caption=converted_message)
            elif converted_message.strip():
                await client.send_message(CHANNEL_ID, converted_message)
            
            print(f"Processed message: {converted_message[:60]}")
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
