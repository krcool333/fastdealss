import os
import re
import asyncio
import aiohttp
import time
import threading
import requests
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors import common
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
AMAZON_TAG = "lootast21"  # your affiliate tag value
EARN_KARO_ID = "459844"   # your earnkaro affiliate id

WAHA_API_URL = os.getenv('WAHA_API_URL')
WAHA_API_KEY = os.getenv('WAHA_API_KEY')
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')

# List of Telegram channels to monitor (deduplicated)
SOURCE_IDS = [
    -1001315464303, -1001717949, -1001707570,
    -1001820592, -1001448358, -1001378809,
    -1001404064, -1001772002, -1002448217,
    -1001500000, -1001505334, -1001373588
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)',
    r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart.com/\S+)',
    r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)',
    r'(https?://amazon\.in/\S+)',
    r'(https?://bit\.ly/\S+)',
    r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
recent_products = set()
RECENT_LIMIT = 1000
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

# Keep WAHA alive periodically
async def keep_waha_alive():
    while True:
        try:
            await asyncio.sleep(300)  # 5 mins
            if WAHA_API_URL and WAHA_API_KEY:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        WAHA_API_URL + "/api/version",
                        headers={"X-Api-Key": WAHA_API_KEY},
                        timeout=10
                    ) as resp:
                        if resp.status == 200:
                            print("✅ WAHA keep-alive ping successful")
                        else:
                            print(f"⚠️ WAHA ping failed with status: {resp.status}")
        except Exception as e:
            print(f"❌ WAHA keep-alive error: {e}")

# Send message to WhatsApp via WAHA API
async def send_whatsapp(message):
    global whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        print("❌ WAHA API config missing")
        return False
    try:
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(WAHA_API_URL + "/api/sendText", json=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    print("✅ Sent to WhatsApp")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    text = await resp.text()
                    print(f"⚠️ WAHA API response {resp.status}: {text[:120]}...[truncated]")
                    return False
    except Exception as e:
        print(f"❌ Error sending WhatsApp message: {e}")
        return False

# Expand short links (optional)
async def expand_links(text):
    urls = sum((re.findall(p, text) for p in SHORT_PATTERNS), [])
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.head(url, allow_redirects=True, timeout=5) as resp:
                    text = text.replace(url, str(resp.url))
            except:
                pass
    return text

# Replace Amazon URLs with affiliate tag
def replace_amazon_tags(text):
    pattern = r'(https?://(?:www\.)?amazon\.in/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))'
    return re.sub(pattern, lambda m: f"https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}", text, flags=re.I)

# Replace other URLs with EarnKaro affiliate
def replace_earnkaro_urls(text):
    domains = [r'flipkart\.com', r'ajio\.me', r'myntra\.com', r'nykaa\.com']
    for domain in domains:
        pattern = rf'(https?://(?:www\.)?{domain}/\S+)'
        text = re.sub(pattern, lambda m: f"https://earnkaro.com/store?id={EARN_KARO_ID}&url={m.group(1)}", text)
    return text

# Shorten EarnKaro links
async def shorten_earnkaro_links(text):
    urls = re.findall(r'https?://earnkaro.com/store\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                api_url = "https://tinyurl.com/api-create.php?url=" + url
                async with session.get(api_url, timeout=5) as resp:
                    short_url = await resp.text()
                    text = text.replace(url, short_url)
            except:
                pass
    return text

# Complete processing pipeline
async def process_text(text):
    text = await expand_links(text)
    text = replace_amazon_tags(text)
    text = replace_earnkaro_urls(text)
    text = await shorten_earnkaro_links(text)
    return text

# Extract product code to deduplicate
def extract_product_key(text):
    # Amazon ASIN
    m = re.search(r'amazon\.in/.+/dp/([A-Z0-9]{10})', text)
    if m:
        return "AMZ_" + m.group(1)
    # Ajio code
    m = re.search(r'ajio\.me/(\w+)', text)
    if m:
        return "AJIO_" + m.group(1)
    # Flipkart product code
    m = re.search(r'flipkart\.com/.+/p/itm([a-zA-Z0-9]+)', text)
    if m:
        return "FK_" + m.group(1)
    # Myntra product code
    m = re.search(r'myntra\.com/.+-([0-9]+)', text)
    if m:
        return "MYN_" + m.group(1)
    return None

# Main Telegram bot code
async def bot_main():
    await client.start()
    sources = []
    for sid in SOURCE_IDS:
        try:
            entity = await client.get_entity(sid)
            sources.append(entity)
            print(f"✅ Subscribed {entity.title}")
        except Exception as e:
            print(f"❌ Failed to subscribe {sid}: {e}")

    print(f"🔍 Monitoring {len(sources)} channels")

    @client.on(events.NewMessage(chats=sources))
    async def handler(event):
        global last_msg_time, seen_urls, recent_products

        if event.message.media:
            return

        message_text = event.message.message or event.message.text
        if not message_text:
            return

        processed_text = await process_text(message_text)
        urls = re.findall(r'https?://\S+', processed_text)

        new_urls = [url for url in urls if url not in seen_urls]
        if not new_urls:
            return

        # Deduplication
        prod_key = extract_product_key(processed_text)
        if prod_key:
            if prod_key in recent_products:
                print(f"🔁 Duplicate detected, skipping: {prod_key}")
                return
            recent_products.add(prod_key)
            if len(recent_products) > 1000:
                recent_products = set(list(recent_products)[-1000:])

        seen_urls.update(new_urls)

        header = ""
        if any("flipkart" in u or "fkrt" in u for u in new_urls):
            header = "🛒 Flipkart Deals\n"
        elif any("myntra" in u for u in new_urls):
            header = "👗 Myntra Deals\n"
        elif any("amazon" in u for u in new_urls):
            header = "📦 Amazon Deals\n"

        final_msg = header + processed_text

        try:
            # Send to Telegram channel
            await client.send_message(CHANNEL_ID, final_msg, link_preview=True)
            print("✅ Sent to Telegram")

            # Send to WhatsApp channel
            if WHATSAPP_CHANNEL_ID:
                await send_whatsapp(final_msg)
        except Exception as e:
            print(f"❌ Sending error: {e}")

        last_msg_time = time.time()

    await client.run_until_disconnected()

def redeploy():
    if DEPLOY_HOOK:
        try:
            requests.post(DEPLOY_HOOK, timeout=10)
            print("🔄 Redeploy triggered")
            return True
        except Exception as e:
            print(f"❌ Redeploy failed: {e}")
            return False
    return False

def keep_alive():
    while True:
        try:
            time.sleep(14 * 60)
            requests.get("http://127.0.0.1:10000/ping")
        except:
            pass

def monitor():
    while True:
        try:
            time.sleep(300)
            if time.time() - last_msg_time > 1800:
                print("⚠️ No recent messages, triggering redeploy")
                redeploy()
        except:
            pass

def start_loop(loop):
    for retry in range(5):
        try:
            print(f"🚀 Starting bot (try {retry+1})")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except common.TypeError as e:
            print(f"Retry due to TypeError: {e}")
            time.sleep(10)
        except Exception as e:
            print(f"❌ Bot error: {e}")
            if retry == 4:
                raise

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "telegram_channel": CHANNEL_ID,
        "whatsapp_channel": WHATSAPP_CHANNEL_ID,
        "waha_url": WAHA_API_URL
    })

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    status = "healthy" if (time.time() - last_msg_time) < 3600 else "inactive"
    return jsonify({
        "status": status,
        "unique_links": len(seen_urls),
        "last_message": last_msg_time,
        "whatsapp_last_success": whatsapp_last_success
    })

@app.route("/stats")
def stats():
    return jsonify({
        "unique_links_processed": len(seen_urls),
        "last_message_time": last_msg_time,
        "telegram_channel": CHANNEL_ID,
        "whatsapp_channel": WHATSAPP_CHANNEL_ID,
        "bot_running": True,
        "waha_url": WAHA_API_URL
    })

@app.route("/redeploy", methods=["POST"])
def redeploy_endpoint():
    if redeploy():
        return "Deployment triggered", 200
    else:
        return "Deployment failed", 500

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"error": "WhatsApp not configured"}), 400
    try:
        resp = requests.post(
            f"{WAHA_API_URL}/api/sendText",
            headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
            json={"chatId": WHATSAPP_CHANNEL_ID, "text": "Test message from bot 🧪", "session": "default"},
            timeout=10,
        )
        if resp.status_code == 200:
            return jsonify({"status": "success", "message": "Test message sent"})
        else:
            return jsonify({"status": "error", "message": resp.text})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/waha-health")
def waha_health():
    try:
        resp = requests.get(
            f"{WAHA_API_URL}/api/version",
            headers={"X-Api-Key": WAHA_API_KEY},
            timeout=5,
        )
        if resp.status_code == 200:
            return jsonify({"status": "healthy", "waha": resp.json()})
        else:
            return jsonify({"status": "error", "code": resp.status_code})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    print("Starting Telegram & WhatsApp Bot")
    print(f"Telegram Channel ID: {CHANNEL_ID}")
    print(f"WhatsApp Channel ID: {WHATSAPP_CHANNEL_ID}")
    print(f"WAHA API URL: {WAHA_API_URL}")

    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor, daemon=True).start()
    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()

    app.run(host="0.0.0.0", port=10000)
