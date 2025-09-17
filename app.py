import os
import re
import time
import random
import asyncio
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify, request
from telethon import TelegramClient, events

# ---------------- CONFIG ---------------- #
API_ID = int(os.getenv("API_ID", ""))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("SESSION", "anon")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
FORWARD_FROM = os.getenv("FORWARD_FROM", "").split(",")
DEPLOY_HOOK = os.getenv("DEPLOY_HOOK", "")
WHATSAPP_CHANNEL_ID = os.getenv("WHATSAPP_CHANNEL_ID", "")
WAHA_URL = os.getenv("WAHA_URL", "")
AMAZON_TAG = "lootfastdeals-21"

# Dedup memory (1 hour window)
dedup_cache = {}
DEDUP_WINDOW = 3600  # 1 hour

# Rotating hashtags
HASHTAG_SETS = [
    "#LootDeals #Discount #OnlineShopping",
    "#Free #Offer #Sale",
]

# Flask app
app = Flask(__name__)

# Telegram client
client = TelegramClient(SESSION, API_ID, API_HASH)

# ---------------- HELPERS ---------------- #

def normalize_url(url: str) -> str:
    """Normalize product URLs and extract unique ID"""
    if "amazon." in url:
        asin_match = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
        if asin_match:
            asin = asin_match.group(1)
            return f"amazon:{asin}"
    if "flipkart." in url:
        pid_match = re.search(r"pid=([A-Z0-9]+)", url)
        if pid_match:
            return f"flipkart:{pid_match.group(1)}"
    if "myntra." in url:
        mid_match = re.search(r"/(\d+)", url)
        if mid_match:
            return f"myntra:{mid_match.group(1)}"
    if "ajio." in url:
        ajio_match = re.search(r"/p/(\d+)", url)
        if ajio_match:
            return f"ajio:{ajio_match.group(1)}"
    return url  # fallback

def clean_url(url: str) -> str:
    """Clean product URLs and force Amazon affiliate tag"""
    if "amazon." in url:
        asin_match = re.search(r"/([A-Z0-9]{10})(?:[/?]|$)", url)
        if asin_match:
            asin = asin_match.group(1)
            return f"https://www.amazon.in/dp/{asin}?tag={AMAZON_TAG}"
    if "flipkart." in url:
        base = re.sub(r"\?.*", "", url)
        return base
    if "myntra." in url or "ajio." in url:
        base = re.sub(r"\?.*", "", url)
        return base
    return url

def truncate_message(msg: str, limit: int = 3500) -> str:
    return msg if len(msg) <= limit else msg[:limit] + "..."

def choose_hashtags() -> str:
    return random.choice(HASHTAG_SETS)

def is_duplicate(urls: list) -> bool:
    """Check deduplication cache"""
    now = time.time()
    for u in urls:
        key = normalize_url(u)
        if key in dedup_cache and now - dedup_cache[key] < DEDUP_WINDOW:
            return True
    for u in urls:
        key = normalize_url(u)
        dedup_cache[key] = now
    return False

def detect_platform(urls: list) -> str:
    if any("amazon" in u for u in urls):
        return "🔥 Amazon Deal:"
    if any("flipkart" in u for u in urls):
        return "⚡ Flipkart Deal:"
    if any("myntra" in u for u in urls):
        return "✨ Myntra Deal:"
    if any("ajio" in u for u in urls):
        return "🛍️ Ajio Deal:"
    return "💥 Hot Deal:"

# ---------------- TELEGRAM HANDLER ---------------- #

@client.on(events.NewMessage(chats=FORWARD_FROM))
async def handler(event):
    try:
        text = event.raw_text
        urls = re.findall(r'https?://\S+', text)

        if not urls:
            return

        urls = [clean_url(u) for u in urls]

        if is_duplicate(urls):
            print("⏩ Skipped duplicate deal")
            return

        platform_label = detect_platform(urls)
        hashtags = choose_hashtags()

        msg = f"{platform_label}\n{text}\n\n" + "\n".join(urls) + f"\n\n{hashtags}"
        msg = truncate_message(msg)

        await client.send_message(CHANNEL_ID, msg, link_preview=False)
        print("✅ Forwarded to Telegram")

        if WHATSAPP_CHANNEL_ID and WAHA_URL:
            send_to_whatsapp(msg)

    except Exception as e:
        print(f"❌ Handler error: {e}")

# ---------------- WHATSAPP ---------------- #

def send_to_whatsapp(msg):
    try:
        r = requests.post(
            f"{WAHA_URL}/messages/text",
            json={"to": WHATSAPP_CHANNEL_ID, "text": msg},
            timeout=10
        )
        if r.status_code == 200:
            print("📲 Sent to WhatsApp")
        else:
            print(f"⚠️ WhatsApp send failed: {r.text}")
    except Exception as e:
        print(f"❌ WAHA error: {e}")

# ---------------- FLASK ROUTES ---------------- #

@app.route("/")
def home():
    return "✅ Loot Deals Bot Running"

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    return jsonify(ok=True, time=str(datetime.utcnow()))

@app.route("/stats")
def stats():
    return jsonify(dedup_count=len(dedup_cache))

@app.route("/redeploy", methods=["POST"])
def redeploy():
    if DEPLOY_HOOK:
        try:
            r = requests.post(DEPLOY_HOOK, timeout=10)
            return jsonify(ok=True, status=r.status_code)
        except Exception as e:
            return jsonify(ok=False, error=str(e))
    return jsonify(ok=False, error="No deploy hook set")

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    msg = request.json.get("msg", "Test message ✅")
    send_to_whatsapp(msg)
    return jsonify(ok=True)

@app.route("/waha-health")
def waha_health():
    try:
        r = requests.get(f"{WAHA_URL}/health", timeout=5)
        return jsonify(ok=True, status=r.json())
    except Exception as e:
        return jsonify(ok=False, error=str(e))

@app.route("/update-waha-url", methods=["POST"])
def update_waha_url():
    global WAHA_URL
    data = request.get_json(force=True)
    new_url = data.get("url")
    if new_url:
        WAHA_URL = new_url
        return jsonify(ok=True, new_url=WAHA_URL)
    return jsonify(ok=False, error="No URL provided")

# ---------------- MAIN ---------------- #

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(client.start())
    loop.create_task(client.run_until_disconnected())
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

if __name__ == "__main__":
    main()
