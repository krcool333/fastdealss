# FastDeals Bot - Optimized (No Duplicates, Amazon Tag Enforced, EarnKaro Optional, Labels + Rotating Hashtags)
import os
import re
import time
import requests
import asyncio
import hashlib
import random
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from dotenv import load_dotenv
import aiohttp
from collections import defaultdict
from datetime import datetime, timedelta

# ---------------- Load env ---------------- #
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_ID_2 = int(os.getenv("CHANNEL_ID_2", "-1003007607997"))  # Second channel
AMAZON_TAG = os.getenv("AFFILIATE_TAG", "lootfastdeals-21")
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

WAHA_API_URL = os.getenv("WAHA_API_URL")
WAHA_API_KEY = os.getenv("WAHA_API_KEY")
WHATSAPP_CHANNEL_IDS = [channel_id.strip() for channel_id in os.getenv('WHATSAPP_CHANNEL_IDS').split(',')] if os.getenv('WHATSAPP_CHANNEL_IDS') else []

USE_EARNKARO = os.getenv("USE_EARNKARO", "false").lower() == "true"
DEDUPE_SECONDS = int(os.getenv("DEDUPE_SECONDS", "3600"))  # default 1 hr
MAX_MSG_LEN = int(os.getenv("MAX_MSG_LEN", "700"))
PREVIEW_LEN = int(os.getenv("PREVIEW_LEN", "500"))

# Telegram source groups
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947,
    -1001404064358, -1001772002285, -1001373588507
]

SHORT_PATTERNS = [
    r"(https?://fkrt\.cc/\S+)", r"(https?://myntr\.it/\S+)",
    r"(https?://dl\.flipkart\.com/\S+)", r"(https?://ajio\.me/\S+)",
    r"(https?://amzn\.to/\S+)", r"(https?://amzn\.in/\S+)",
    r"(https?://bit\.ly/\S+)", r"(https?://tinyurl\.com/\S+)",
    r"(https?://fktt\.co/\S+)", r"(https?://bitly\.cx/\S+)",
    r"(https?://fkt\.co/\S+)"
]

# Domains to avoid for WhatsApp (problematic/shorteners)
WHATSAPP_BLACKLIST = [
    "bitly.cx", "bit.ly", "tinyurl.com", "fktt.co", "fkt.co"
]

# ---------------- Runtime state ---------------- #
seen_urls = set()
seen_products = {}
seen_products_times = defaultdict(datetime)
last_msg_time = time.time()
whatsapp_last_success = 0
product_cooldown = timedelta(hours=4)

client = TelegramClient("session", API_ID, API_HASH)
app = Flask(__name__)

# Rotating hashtags pool
HASHTAG_SETS = [
    "#LootDeals #Discount #OnlineShopping",
    "#Free #Offer #Sale",
    "#TopDeals #BigSale #BestPrice",
    "#PriceDrop #FlashSale #DealAlert",
]

# ---------------- Helpers ---------------- #

async def expand_all(text):
    """Expand short URLs like fkrt.cc, amzn.to etc."""
    urls = sum((re.findall(p, text) for p in SHORT_PATTERNS), [])
    if not urls:
        return text
    async with aiohttp.ClientSession() as s:
        for u in urls:
            try:
                async with s.head(u, allow_redirects=True, timeout=5) as r:
                    expanded_url = str(r.url)
                    text = text.replace(u, expanded_url)
                    print(f"🔗 Expanded {u} → {expanded_url}")
            except Exception as e:
                print(f"⚠️ Expansion failed for {u}: {e}")
    return text

def convert_amazon(text):
    """Force Amazon affiliate tag"""
    pat = r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))'
    def repl(m):
        asin = m.group(2)
        return f"https://www.amazon.in/dp/{asin}/?tag={AMAZON_TAG}"
    text = re.sub(pat, repl, text, flags=re.I)
    text = re.sub(r'([?&])tag=[^&\s]+', r'\1tag=' + AMAZON_TAG, text)
    return text

async def convert_earnkaro(text):
    """Optional EarnKaro wrapping with fallback"""
    if not USE_EARNKARO:
        return text
    urls = re.findall(r"(https?://\S+)", text)
    for u in urls:
        if any(x in u for x in ["flipkart", "myntra", "ajio"]):
            try:
                r = requests.post(
                    "https://api.earnkaro.com/api/deeplink",
                    json={"url": u},
                    headers={"Content-Type": "application/json"},
                    timeout=6
                )
                if r.status_code == 200:
                    ek = r.json().get("data", {}).get("link")
                    if ek:
                        text = text.replace(u, ek)
                        continue
            except Exception as e:
                print(f"⚠️ EarnKaro failed for {u}: {e}")
    return text

async def process(text):
    t = await expand_all(text)
    t = convert_amazon(t)
    t = await convert_earnkaro(t)
    return t

def extract_product_identifier(text):
    """Extract a unique identifier for a product to detect duplicates"""
    patterns = [
        r'(?:Midea|Samsung|LG|Whirlpool|IFB)\s+\d+\s*Kg.*Washing\s*Machine',
        r'Lifebuoy.*Body\s*Wash',
        r'Dove.*Body\s*Wash',
        r'Axe.*Body\s*Wash',
        r'Levi.*s.*Clothing',
        r'Spykar.*Clothing',
        r'Oversized.*T.*Shirt',
        r'Saree.*@',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).lower().replace(" ", "")
    
    discount_match = re.search(r'\d+%\%?\s*Off?\s*[:-]?\s*(.*)', text, re.IGNORECASE)
    if discount_match:
        product_desc = discount_match.group(1)
        product_desc = re.sub(r'@\d+', '', product_desc)
        product_desc = re.sub(r'https?://\S+', '', product_desc)
        product_desc = product_desc.strip().lower().replace(" ", "")
        if len(product_desc) > 5:
            return product_desc
    
    return None

def canonicalize(url):
    """Stable key for dedup (Amazon ASIN / Flipkart / Myntra / Ajio)"""
    m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', url, flags=re.I)
    if m:
        return f"amazon:{m.group(1)}"
    
    if "flipkart.com" in url:
        pid_match = re.search(r'/p/([a-zA-Z0-9]+)', url)
        if pid_match:
            return f"flipkart:{pid_match.group(1)}"
        item_match = re.search(r'/itm/([a-zA-Z0-9]+)', url)
        if item_match:
            return f"flipkart:{item_match.group(1)}"
    
    for dom in ["myntra.com", "ajio.com"]:
        if dom in url:
            path = url.split("?")[0].rstrip("/")
            return dom + ":" + path.split("/")[-1] if "/" in path else path
    
    return None

def hash_text(msg):
    """Hash of deal text ignoring numbers/spaces for dedup"""
    product_name = extract_product_identifier(msg)
    if product_name:
        clean = re.sub(r"\s+", " ", product_name.lower())
        clean = re.sub(r"[^\w\s]", "", clean)
        print(f"🔑 Product name hash: {product_name} → {hashlib.md5(clean.encode()).hexdigest()}")
        return hashlib.md5(clean.encode()).hexdigest()
    
    clean = re.sub(r"\s+", " ", msg.lower())
    clean = re.sub(r'https?://\S+', '', clean)
    clean = re.sub(r'₹\s*\d+', '', clean)
    clean = re.sub(r'\d+%', '', clean)
    clean = re.sub(r'[^\w\s]', '', clean)
    result = hashlib.md5(clean.encode()).hexdigest()
    print(f"🔑 Fallback hash: {clean} → {result}")
    return result

def truncate_message(msg):
    if len(msg) <= MAX_MSG_LEN:
        return msg
    urls = re.findall(r"https?://\S+", msg)
    more_link = urls[0] if urls else ""
    return msg[:PREVIEW_LEN] + "...\n👉 More: " + more_link

def choose_hashtags():
    return random.choice(HASHTAG_SETS)

def is_whatsapp_safe(url):
    """Check if URL is safe for WhatsApp (not blacklisted)"""
    return not any(blacklisted in url for blacklisted in WHATSAPP_BLACKLIST)

async def send_to_whatsapp(message, channel_id=None):
    """Send message to WhatsApp Channel - supports multiple channels"""
    global whatsapp_last_success
    
    if not WAHA_API_URL or not WAHA_API_KEY:
        print("❌ Local WhatsApp API not configured")
        return False
    
    target_channel_id = channel_id or (WHATSAPP_CHANNEL_IDS[0] if WHATSAPP_CHANNEL_IDS else None)
    
    if not target_channel_id:
        print("❌ No WhatsApp channel ID configured")
        return False
    
    # Check if message contains unsafe URLs for WhatsApp
    urls = re.findall(r"https?://\S+", message)
    unsafe_urls = [url for url in urls if not is_whatsapp_safe(url)]
    
    if unsafe_urls:
        print(f"⚠️ Skipping WhatsApp - unsafe URLs: {unsafe_urls}")
        return False
    
    try:
        url = f"{WAHA_API_URL}/api/sendText"
        headers = {
            "X-Api-Key": WAHA_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "chatId": target_channel_id,
            "text": message,
            "session": "default"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=15) as response:
                if response.status == 200:
                    print(f"✅ Message sent to WhatsApp: {target_channel_id}")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"❌ WhatsApp API Error for {target_channel_id}: {response.status}")
                    return False
                        
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        print(f"❌ WhatsApp send error for {target_channel_id}: {error_msg}")
        return False

async def send_to_telegram_channels(message):
    """Send message to both Telegram channels with error handling"""
    channels = [CHANNEL_ID]
    if CHANNEL_ID_2:
        channels.append(CHANNEL_ID_2)
    
    for channel_id in channels:
        try:
            await client.send_message(channel_id, message, link_preview=False)
            print(f"✅ Sent to Telegram channel {channel_id}")
        except Exception as ex:
            print(f"❌ Telegram error for channel {channel_id}: {ex}")

# ---------------- Bot main ---------------- #

async def bot_main():
    await client.start()
    sources = []
    for i in SOURCE_IDS:
        try:
            e = await client.get_entity(i)
            sources.append(e.id)
            print(f"✅ Connected: {e.title}")
        except Exception as ex:
            print(f"❌ Failed source {i}: {ex}")

    print(f"📢 Target channels: {CHANNEL_ID} (Primary), {CHANNEL_ID_2} (Secondary)")
    if WHATSAPP_CHANNEL_IDS:
        print(f"💬 WhatsApp Channels: {len(WHATSAPP_CHANNEL_IDS)} channels")

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global seen_products, seen_urls, last_msg_time, seen_products_times

        if e.message.media:
            return

        raw_txt = e.message.message or ""
        if not raw_txt:
            return

        print(f"📨 Raw message: {raw_txt[:100]}...")
        
        # Extract product identifier to check for duplicates
        product_id = extract_product_identifier(raw_txt)
        current_time = datetime.now()
        
        # Check if this is a duplicate product within cooldown period
        if product_id and product_id in seen_products_times:
            time_since_last_seen = current_time - seen_products_times[product_id]
            if time_since_last_seen < product_cooldown:
                print(f"⏩ Skipping duplicate product: {product_id}")
                return
        
        processed = await process(raw_txt)
        urls = re.findall(r"https?://\S+", processed)

        now = time.time()
        dedupe_keys = []

        # Dedup by product URL
        for u in urls:
            c = canonicalize(u)
            if c:
                last_seen = seen_products.get(c)
                if not last_seen or (now - last_seen) > DEDUPE_SECONDS:
                    dedupe_keys.append(c)
                    print(f"🔗 URL dedupe key: {c}")
                else:
                    print(f"⚠️ Duplicate URL skipped: {c} (seen {int(now - last_seen)}s ago)")

        # Dedup by text hash (improved)
        text_key = hash_text(processed)
        last_seen = seen_products.get(text_key)
        if not last_seen or (now - last_seen) > DEDUPE_SECONDS:
            dedupe_keys.append(text_key)
            print(f"📝 Text dedupe key: {text_key}")
        else:
            print(f"⚠️ Duplicate text skipped: {text_key} (seen {int(now - last_seen)}s ago)")

        if not dedupe_keys and product_id in seen_products_times:
            print(f"⏩ Skipping duplicate product (no new URLs): {product_id}")
            return

        if not dedupe_keys and not product_id:
            print("⏩ Skipping message with no identifiable product or new URLs")
            return

        for k in dedupe_keys:
            seen_products[k] = now
        if product_id:
            seen_products_times[product_id] = current_time
        for u in urls:
            seen_urls.add(u)

        # ---------------- Label + Hashtags ---------------- #
        label = ""
        expanded_urls = []
        
        # Expand short URLs to detect the actual domain
        for url in urls:
            if any(pattern in url for pattern in ["fkrt.cc", "myntr.it", "dl.flipkart.com", "amzn.to", "amzn.in", "bit.ly", "tinyurl.com", "fktt.co", "fkt.co", "bitly.cx"]):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, allow_redirects=True, timeout=5) as response:
                            expanded_url = str(response.url)
                            expanded_urls.append(expanded_url)
                            print(f"🔍 Expanded {url} → {expanded_url}")
                except Exception as e:
                    print(f"⚠️ Expansion failed for {url}: {e}")
                    expanded_urls.append(url)
            else:
                expanded_urls.append(url)
        
        # Check both original and expanded URLs for domain detection
        all_urls = urls + expanded_urls
        
        if any("amazon" in u for u in all_urls):
            label = "🔥 Amazon Deal:\n"
        elif any("flipkart" in u for u in all_urls):
            label = "⚡ Flipkart Deal:\n"
        elif any("myntra" in u for u in all_urls):
            label = "✨ Myntra Deal:\n"
        elif any("ajio" in u for u in all_urls):
            label = "🛍️ Ajio Deal:\n"
        else:
            label = "🎯 Fast Deal:\n"

        msg = label + truncate_message(processed)
        msg += f"\n\n{choose_hashtags()}"

        # Send to both Telegram channels
        await send_to_telegram_channels(msg)

        # Send to ALL WhatsApp Channels
        for whatsapp_channel_id in WHATSAPP_CHANNEL_IDS:
            await send_to_whatsapp(msg, whatsapp_channel_id)

        last_msg_time = time.time()
        print(f"✅ Processing complete at {time.strftime('%H:%M:%S')}")

    await client.run_until_disconnected()

# ---------------- Maintenance ---------------- #

def redeploy():
    if not DEPLOY_HOOK:
        return False
    try:
        requests.post(DEPLOY_HOOK, timeout=10)
        return True
    except:
        return False

def keep_alive():
    while True:
        time.sleep(14 * 60)
        try:
            requests.get("http://127.0.0.1:10000/ping", timeout=5)
        except:
            pass

def monitor_health():
    global last_msg_time
    while True:
        time.sleep(300)
        if (time.time() - last_msg_time) > 1800:
            print("⚠️ Idle 30+ min, redeploying")
            redeploy()

def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_main())

# ---------------- Flask endpoints ---------------- #

@app.route("/")
def home():
    return jsonify({
        "status": "running", 
        "telegram_primary": CHANNEL_ID, 
        "telegram_secondary": CHANNEL_ID_2,
        "whatsapp_channels": WHATSAPP_CHANNEL_IDS
    })

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    return jsonify({
        "time_since_last_message": int(time.time() - last_msg_time),
        "unique_links": len(seen_urls),
        "whatsapp_configured": bool(WHATSAPP_CHANNEL_IDS),
        "whatsapp_last_success": int(time.time() - whatsapp_last_success) if whatsapp_last_success else None,
        "status": "healthy" if (time.time() - last_msg_time) < 3600 else "inactive"
    })

@app.route("/stats")
def stats():
    return jsonify({
        "unique_links": len(seen_urls), 
        "last_message_time": last_msg_time,
        "telegram_channels": [CHANNEL_ID, CHANNEL_ID_2] if CHANNEL_ID_2 else [CHANNEL_ID],
        "whatsapp_channels": WHATSAPP_CHANNEL_IDS
    })

@app.route("/redeploy", methods=["POST"])
def redeploy_endpoint():
    return ("ok", 200) if redeploy() else ("fail", 500)

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    if not WHATSAPP_CHANNEL_IDS:
        return jsonify({"error": "no WA channels configured"})
    try:
        results = {}
        for channel_id in WHATSAPP_CHANNEL_IDS:
            r = requests.post(f"{WAHA_API_URL}/api/sendText",
                          headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
                          json={"chatId": channel_id, "text": "Test WA", "session": "default"},
                          timeout=10)
            results[channel_id] = r.status_code
        return jsonify({"status": "test_completed", "results": results})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/waha-health")
def waha_health():
    try:
        r = requests.get(f"{WAHA_API_URL}/api/version", headers={"X-Api-Key": WAHA_API_KEY}, timeout=5)
        if r.status_code == 200:
            return jsonify({"status": "healthy", "waha": r.json()})
        else:
            return jsonify({"status": "error", "code": r.status_code})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/update-waha-url", methods=["POST"])
def update_waha_url():
    global WAHA_API_URL
    data = request.get_json(force=True)
    new_url = data.get("url")
    if new_url:
        WAHA_API_URL = new_url.rstrip("/")
        print(f"🔄 WAHA URL updated: {WAHA_API_URL}")
        return jsonify({"status": "ok", "url": WAHA_API_URL})
    return jsonify({"error": "no url"}), 400

# ---------------- Entrypoint ---------------- #

if __name__ == "__main__":
    print("🚀 Starting FastDeals Bot with Multi-Channel Support...")
    print(f"📱 Telegram Channels: {CHANNEL_ID}, {CHANNEL_ID_2}")
    print(f"💬 WhatsApp Channels: {WHATSAPP_CHANNEL_IDS}")
    print(f"🔗 Local WAHA API: {WAHA_API_URL}")
    
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False, threaded=True)