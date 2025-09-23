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
WHATSAPP_CHANNEL_ID = os.getenv("WHATSAPP_CHANNEL_ID")

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
    r"(https?://fktt\.co/\S+)", r"(https?://bitly\.cx/\S+)",  # Added problematic domains
    r"(https?://fkt\.co/\S+)"
]

# Domains to avoid for WhatsApp (problematic/shorteners)
WHATSAPP_BLACKLIST = [
    "bitly.cx", "bit.ly", "tinyurl.com"
]

# ---------------- Runtime state ---------------- #
seen_urls = set()
seen_products = {}
last_msg_time = time.time()
whatsapp_last_success = 0

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

def extract_product_name(text):
    """Extract product name for better deduplication"""
    # Remove URLs first to avoid interference
    text_no_urls = re.sub(r'https?://\S+', '', text)
    
    # Look for product names after common patterns
    patterns = [
        r"(?:Samsung|iPhone|OnePlus|Realme|Xiaomi|Redmi|Poco|Motorola|Nokia|LG|Sony|HP|Dell|Lenovo|Asus|Acer|MSI|Canon|Nikon|Boat|JBL|Noise|Fire-Boltt|pTron|Mi|Pepe\s+Jeans|Lee\s+Cooper)\s+[^@\n]+?(?=@|₹|http|$)",
        r"[A-Z][a-z]+(?:\s+[A-Za-z0-9]+)+?(?:\s+\d+(?:cm|inch|mm|GB|TB|MB|MHz|GHz|W|mAh|Hz|" r"|MP|K|°|'|”))+(?=@|₹|http|$)",
        r"Upto\s+\d+%+\s+Off\s+On\s+([^@\n]+?)(?=@|₹|http|$)",
        r"Flat\s+\d+%+\s+Off\s+On\s+([^@\n]+?)(?=@|₹|http|$)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text_no_urls, re.IGNORECASE)
        if match:
            product_name = match.group(0).strip()
            # Clean up the product name
            product_name = re.sub(r'^(Upto|Flat)\s+\d+%\s+Off\s+On\s+', '', product_name, flags=re.IGNORECASE)
            return product_name
    
    return None

def canonicalize(url):
    """Stable key for dedup (Amazon ASIN / Flipkart / Myntra / Ajio)"""
    m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', url, flags=re.I)
    if m:
        return f"amazon:{m.group(1)}"
    
    # Handle Flipkart product IDs
    if "flipkart.com" in url:
        pid_match = re.search(r'/p/([a-zA-Z0-9]+)', url)
        if pid_match:
            return f"flipkart:{pid_match.group(1)}"
        # Also check for other Flipkart patterns
        item_match = re.search(r'/itm/([a-zA-Z0-9]+)', url)
        if item_match:
            return f"flipkart:{item_match.group(1)}"
    
    # For Myntra and Ajio, use the full path without parameters
    for dom in ["myntra.com", "ajio.com"]:
        if dom in url:
            path = url.split("?")[0].rstrip("/")
            return dom + ":" + path.split("/")[-1] if "/" in path else path
    
    return None

def hash_text(msg):
    """Hash of deal text ignoring numbers/spaces for dedup"""
    # Extract product name for better deduplication
    product_name = extract_product_name(msg)
    if product_name:
        clean = re.sub(r"\s+", " ", product_name.lower())
        clean = re.sub(r"[^\w\s]", "", clean)
        print(f"🔑 Product name hash: {product_name} → {hashlib.md5(clean.encode()).hexdigest()}")
        return hashlib.md5(clean.encode()).hexdigest()
    
    # Fallback to original method - but improved
    clean = re.sub(r"\s+", " ", msg.lower())
    # Remove URLs for better deduplication
    clean = re.sub(r'https?://\S+', '', clean)
    # Remove prices and percentages
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

async def send_to_whatsapp(message):
    global WAHA_API_URL, WAHA_API_KEY, WHATSAPP_CHANNEL_ID, whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        return
    
    # Check if message contains unsafe URLs for WhatsApp
    urls = re.findall(r"https?://\S+", message)
    unsafe_urls = [url for url in urls if not is_whatsapp_safe(url)]
    
    if unsafe_urls:
        print(f"⚠️ Skipping WhatsApp - unsafe URLs: {unsafe_urls}")
        return
    
    try:
        url = f"{WAHA_API_URL}/api/sendText"
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=10) as r:
                if r.status == 200:
                    whatsapp_last_success = time.time()
                    print("✅ WhatsApp sent")
    except Exception as e:
        print(f"⚠️ WAHA unreachable: {e}")

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

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global seen_products, seen_urls, last_msg_time

        raw_txt = e.message.message or ""
        if not raw_txt:
            return

        print(f"📨 Raw message: {raw_txt[:100]}...")
        
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

        if not dedupe_keys:
            print("⚠️ Duplicate skipped (no new dedupe keys)")
            return

        for k in dedupe_keys:
            seen_products[k] = now
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
            # Default label for other deals
            label = "🎯 Fast Deal:\n"

        msg = label + truncate_message(processed)
        msg += f"\n\n{choose_hashtags()}"

        # Send to both Telegram channels
        await send_to_telegram_channels(msg)

        if WHATSAPP_CHANNEL_ID:
            try:
                await send_to_whatsapp(msg)
            except Exception as e:
                print(f"⚠️ WhatsApp error: {e}")

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
        "whatsapp": WHATSAPP_CHANNEL_ID
    })

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    return jsonify({
        "time_since_last_message": int(time.time() - last_msg_time),
        "unique_links": len(seen_urls),
        "status": "healthy" if (time.time() - last_msg_time) < 3600 else "inactive"
    })

@app.route("/stats")
def stats():
    return jsonify({
        "unique_links": len(seen_urls), 
        "last_message_time": last_msg_time,
        "telegram_channels": [CHANNEL_ID, CHANNEL_ID_2] if CHANNEL_ID_2 else [CHANNEL_ID]
    })

@app.route("/redeploy", methods=["POST"])
def redeploy_endpoint():
    return ("ok", 200) if redeploy() else ("fail", 500)

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"error": "no WA"})
    try:
        r = requests.post(f"{WAHA_API_URL}/api/sendText",
                          headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
                          json={"chatId": WHATSAPP_CHANNEL_ID, "text": "Test WA", "session": "default"},
                          timeout=10)
        return jsonify({"status": r.status_code})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/waha-health")
def waha_health():
    try:
        r = requests.get(f"{WAHA_API_URL}/api/version", headers={"X-Api-Key": WAHA_API_KEY}, timeout=5)
        return jsonify({"status": r.status_code})
    except Exception as e:
        return jsonify({"error": str(e)})

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
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False, threaded=True)