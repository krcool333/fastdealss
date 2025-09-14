# FastDeals Bot - Text-only, no duplicates, super fast
import os
import re
import time
import requests
import asyncio
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
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

WAHA_API_URL = os.getenv("WAHA_API_URL")
WAHA_API_KEY = os.getenv("WAHA_API_KEY")
WHATSAPP_CHANNEL_ID = os.getenv("WHATSAPP_CHANNEL_ID")

# Dedup window (seconds)
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
    r"(https?://bit\.ly/\S+)", r"(https?://tinyurl\.com/\S+)"
]

# ---------------- Runtime state ---------------- #
seen_urls = set()
seen_products = {}  # product key -> last seen
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient("session", API_ID, API_HASH)
app = Flask(__name__)

# ---------------- Helpers ---------------- #

async def expand_all(text):
    urls = sum((re.findall(p, text) for p in SHORT_PATTERNS), [])
    if not urls:
        return text
    async with aiohttp.ClientSession() as s:
        for u in urls:
            try:
                async with s.head(u, allow_redirects=True, timeout=5) as r:
                    text = text.replace(u, str(r.url))
            except Exception:
                pass
    return text

def convert_amazon(text):
    pats = [
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))',
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:\?|&amp;)tag=[^&amp;\s]*'
    ]
    for p in pats:
        text = re.sub(p, lambda m: f"https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}", text, flags=re.I)
    return text

def convert_earnkaro(text):
    pats = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)'
    ]
    for p in pats:
        text = re.sub(p, lambda m: f"https://earnkaro.com/store?id={EARNKARO_ID}&url={m.group(1)}", text, flags=re.I)
    return text

async def shorten_earnkaro(text):
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as s:
        for u in urls:
            try:
                api = f"http://tinyurl.com/api-create.php?url={u}"
                async with s.get(api, timeout=6) as r:
                    short = await r.text()
                    text = text.replace(u, short)
            except Exception:
                pass
    return text

async def process(text):
    t = await expand_all(text)
    t = convert_amazon(t)
    t = convert_earnkaro(t)
    t = await shorten_earnkaro(t)
    return t

def canonicalize(url):
    m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', url, flags=re.I)
    if m:
        return f"amazon:{m.group(1)}"
    for dom in ["flipkart.com", "myntra.com", "ajio.com"]:
        if dom in url:
            return dom + ":" + url.split("?")[0].rstrip("/")
    return None

def truncate_message(msg):
    if len(msg) <= MAX_MSG_LEN:
        return msg
    urls = re.findall(r"https?://\S+", msg)
    more_link = urls[0] if urls else ""
    return msg[:PREVIEW_LEN] + "...\n👉 More: " + more_link

async def send_to_whatsapp(message):
    global WAHA_API_URL, WAHA_API_KEY, WHATSAPP_CHANNEL_ID, whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
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
    except Exception:
        print("⚠️ WAHA unreachable")

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

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global seen_products, seen_urls, last_msg_time

        raw_txt = e.message.message or ""
        if not raw_txt:
            return

        processed = await process(raw_txt)
        urls = re.findall(r"https?://\S+", processed)

        now = time.time()
        new_canon = []
        for u in urls:
            c = canonicalize(u)
            if not c:
                continue
            last_seen = seen_products.get(c)
            if not last_seen or (now - last_seen) > DEDUPE_SECONDS:
                new_canon.append(c)

        if not new_canon:
            print("⚠️ Duplicate skipped")
            return

        for c in new_canon:
            seen_products[c] = now
        for u in urls:
            seen_urls.add(u)

        msg = truncate_message(processed)

        try:
            await client.send_message(CHANNEL_ID, msg, link_preview=False)
            print("✅ Sent to Telegram")
        except Exception as ex:
            print(f"❌ Telegram error: {ex}")

        if WHATSAPP_CHANNEL_ID:
            try:
                await send_to_whatsapp(msg)
            except Exception:
                pass

        last_msg_time = time.time()

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
    return jsonify({"status": "running", "telegram": CHANNEL_ID, "whatsapp": WHATSAPP_CHANNEL_ID})

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
    return jsonify({"unique_links": len(seen_urls), "last_message_time": last_msg_time})

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
