import os, asyncio, re, aiohttp, time, threading, requests
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime, timedelta

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# Local WAHA API Configuration (via ngrok)
WAHA_API_URL = os.getenv('WAHA_API_URL')
WAHA_API_KEY = os.getenv('WAHA_API_KEY')
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')

SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947,
    -1001404064358, -1001772002285, -1001373588507
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)', r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)', r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
seen_products = defaultdict(datetime)  # Track products and when they were last seen
last_msg_time = time.time()
whatsapp_last_success = 0
product_cooldown = timedelta(hours=4)  # Cooldown period for similar products
client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

# ... [Keep all your existing functions unchanged until the handler] ...

def extract_product_identifier(text):
    """
    Extract a unique identifier for a product to detect duplicates
    based on product name and key features rather than just URLs
    """
    # Patterns to identify product names in messages
    patterns = [
        r'(?:Midea|Samsung|LG|Whirlpool|IFB)\s+\d+\s*Kg.*Washing\s*Machine',
        r'Lifebuoy.*Body\s*Wash',
        r'Dove.*Body\s*Wash',
        r'Axe.*Body\s*Wash',
        r'Levi.*s.*Clothing',
        r'Spykar.*Clothing',
        r'Oversized.*T.*Shirt',
        r'Saree.*@',
        # Add more patterns as needed for your products
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).lower().replace(" ", "")
    
    # Fallback: try to extract product name from percentage off and description
    discount_match = re.search(r'\d+%\%?\s*Off?\s*[:-]?\s*(.*)', text, re.IGNORECASE)
    if discount_match:
        product_desc = discount_match.group(1)
        # Clean up the product description
        product_desc = re.sub(r'@\d+', '', product_desc)  # Remove prices
        product_desc = re.sub(r'https?://\S+', '', product_desc)  # Remove URLs
        product_desc = product_desc.strip().lower().replace(" ", "")
        if len(product_desc) > 5:  # Ensure we have a meaningful identifier
            return product_desc
    
    return None

async def bot_main():
    await client.start()
    
    sources = []
    for i in SOURCE_IDS:
        try:
            e = await client.get_entity(i)
            sources.append(e.id)
            print(f"✅ Connected to source: {e.title}")
        except Exception as ex:
            print(f"❌ Failed to connect to source {i}: {ex}")
    
    print(f"🚀 Monitoring {len(sources)} Telegram channels")
    print("🔗 Bot will forward deals to:")
    print(f"   📱 Telegram Channel: {CHANNEL_ID}")
    if WHATSAPP_CHANNEL_ID:
        print(f"   💬 WhatsApp via Local WAHA: {WHATSAPP_CHANNEL_ID}")
    
    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global seen_urls, last_msg_time, seen_products
        
        if e.message.media: return
        
        txt = e.message.message or e.message.text or ""
        if not txt: return
        
        # Extract product identifier to check for duplicates
        product_id = extract_product_identifier(txt)
        current_time = datetime.now()
        
        # Check if this is a duplicate product within cooldown period
        if product_id and product_id in seen_products:
            time_since_last_seen = current_time - seen_products[product_id]
            if time_since_last_seen < product_cooldown:
                print(f"⏩ Skipping duplicate product: {product_id}")
                return
        
        out = await process(txt)
        urls = re.findall(r'https?://\S+', out)
        new = [u for u in urls if u not in seen_urls]
        
        if not new and product_id in seen_products:
            # No new URLs but we've seen this product before
            print(f"⏩ Skipping duplicate product (no new URLs): {product_id}")
            return
        
        if not new and not product_id:
            # No way to identify this product, skip it
            print("⏩ Skipping message with no identifiable product or new URLs")
            return
        
        seen_urls.update(new)
        if product_id:
            seen_products[product_id] = current_time
        
        hdr = ""
        if any("flipkart.com" in u or "fkrt.cc" in u for u in new):
            hdr = "🛒 Flipkart Deal:\n"
        elif any("myntra.com" in u for u in new):
            hdr = "👗 Myntra Deal:\n"
        elif any("amazon.in" in u for u in new):
            hdr = "📦 Amazon Deal:\n"
        
        msg = hdr + out
        
        try:
            # Send to Telegram Channel
            await client.send_message(CHANNEL_ID, msg, link_preview=False)
            print("✅ Message sent to Telegram channel")
            
            # Send to WhatsApp via Local WAHA
            if WHATSAPP_CHANNEL_ID:
                await send_to_whatsapp(msg)
            
        except Exception as ex:
            print(f"❌ Error sending message: {ex}")
        
        last_msg_time = time.time()
    
    await client.run_until_disconnected()

# ... [Keep the rest of your code unchanged] ...