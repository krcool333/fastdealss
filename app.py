# Full ready-to-paste app.py
# FastDeals Bot - Text-first, 1-hour dedupe, parallel image fetch workers
import os
import re
import time
import json
import asyncio
import aiohttp
import threading
import requests
import urllib.parse
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# ---------------- Load env ---------------- #
dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# WAHA (local) config
WAHA_API_URL = os.getenv("WAHA_API_URL")    # e.g. https://xxxx.ngrok-free.app
WAHA_API_KEY = os.getenv("WAHA_API_KEY")
WHATSAPP_CHANNEL_ID = os.getenv("WHATSAPP_CHANNEL_ID")

# Source channels to monitor
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947, -1001767957702,
    -1001404064358, -1001772002285, -1001373588507
]

# Common shortener patterns we expand
SHORT_PATTERNS = [
    r"(https?://fkrt\.cc/\S+)", r"(https?://myntr\.it/\S+)",
    r"(https?://dl\.flipkart\.com/\S+)", r"(https?://ajio\.me/\S+)",
    r"(https?://amzn\.to/\S+)", r"(https?://amzn\.in/\S+)",
    r"(https?://bit\.ly/\S+)", r"(https?://tinyurl\.com/\S+)"
]

# ---------------- Runtime state ---------------- #
seen_urls = set()        # for stats / tracking
seen_products = {}       # canonical product -> last seen timestamp (seconds)
last_msg_time = time.time()
whatsapp_last_success = 0

# Worker queue for image tasks
IMAGE_TASK_QUEUE = None   # will be asyncio.Queue()
WORKER_COUNT = 4          # concurrent image workers (change if needed)
IMAGE_TASK_SEMAPHORE = None  # asyncio.Semaphore to limit concurrent HTTP fetches

# Telethon client & Flask app
client = TelegramClient("session", API_ID, API_HASH)
app = Flask(__name__)

# ---------------- Helpers ---------------- #

async def keep_waha_alive():
    """Ping WAHA every 5 minutes to detect availability (non-blocking)."""
    global WAHA_API_URL
    while True:
        await asyncio.sleep(300)
        if not WAHA_API_URL:
            continue
        try:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(f"{WAHA_API_URL}/api/version",
                                           headers={"X-Api-Key": WAHA_API_KEY},
                                           timeout=10) as resp:
                        if resp.status == 200:
                            print("✅ WAHA keep-alive OK")
                        else:
                            print(f"⚠️ WAHA keep-alive {resp.status}")
                except Exception:
                    print("⚠️ WAHA not reachable (keep-alive); will retry")
        except Exception as e:
            print(f"❌ keep_waha_alive error: {e}")

async def send_to_whatsapp(message):
    """Send text to WhatsApp via Local WAHA (retry-safe)."""
    global WAHA_API_URL, WAHA_API_KEY, WHATSAPP_CHANNEL_ID, whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        # don't spam logs
        return False
    try:
        url = f"{WAHA_API_URL}/api/sendText"
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=12) as r:
                if r.status == 200:
                    whatsapp_last_success = time.time()
                    print("✅ WhatsApp (text) sent")
                    return True
                else:
                    print(f"⚠️ WAHA API Error (text) {r.status}")
                    return False
    except Exception:
        print("⚠️ WAHA unreachable (text); will retry later")
        return False

async def send_to_whatsapp_with_image(caption, image_url):
    """Send image+caption to WAHA via sendFile (best-effort)."""
    global WAHA_API_URL, WAHA_API_KEY, WHATSAPP_CHANNEL_ID, whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        return False
    try:
        url = f"{WAHA_API_URL}/api/sendFile"
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        payload = {
            "chatId": WHATSAPP_CHANNEL_ID,
            "session": "default",
            "fileUrl": image_url,
            "caption": caption
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=20) as r:
                if r.status == 200:
                    whatsapp_last_success = time.time()
                    print("✅ WhatsApp (image) sent")
                    return True
                else:
                    print(f"⚠️ WAHA API Error (image) {r.status}")
                    return False
    except Exception:
        print("⚠️ WAHA unreachable (image); will retry later")
        return False

async def expand_all(text):
    """Expand common shortlinks quickly using HEAD (follow redirects)."""
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
    """Convert amazon links to canonical amazon.in dp links with affiliate tag."""
    pats = [
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))',
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:\?|&amp;)tag=[^&amp;\s]*'
    ]
    for p in pats:
        text = re.sub(p, lambda m: f"https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}", text, flags=re.I)
    return text

def convert_earnkaro(text):
    """Wrap supported store links in EarnKaro affiliate links."""
    parts = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)'
    ]
    for p in parts:
        text = re.sub(p, lambda m: f"https://earnkaro.com/store?id={EARNKARO_ID}&amp;url={m.group(1)}", text, flags=re.I)
    return text

async def shorten_earnkaro(text):
    """Shorten earnkaro wrapper links using tinyurl (best-effort)."""
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&amp;url=\S+', text)
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
    """Expand, convert affiliate links and shorten where applicable."""
    t = await expand_all(text)
    t = convert_amazon(t)
    t = convert_earnkaro(t)
    t = await shorten_earnkaro(t)
    return t

def canonicalize(url):
    """Return canonical id string for known product URLs for dedupe."""
    m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', url, flags=re.I)
    if m:
        return f"amazon:{m.group(1)}"
    for dom in ["flipkart.com", "myntra.com", "ajio.com"]:
        if dom in url:
            return dom + ":" + url.split("?")[0].rstrip("/")
    return None

def truncate_message(msg, max_len=700, preview_len=500):
    """Shorten messages longer than max_len and append a 'More' link if any present."""
    if len(msg) <= max_len:
        return msg
    urls = re.findall(r"https?://\S+", msg)
    more_link = urls[0] if urls else ""
    return msg[:preview_len] + "...\n👉 More: " + more_link

# ---------------- Image fetching ---------------- #

async def fetch_image_from_url(product_url):
    """
    Attempt to fetch a main product image URL from the product page.
    Strategy:
      - GET the product URL (simple HTTP GET)
      - look for meta property="og:image" or meta name="og:image"
      - fallback: JSON-LD image property
    Returns the image URL (string) or None on failure.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; FastDealsBot/1.0)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(product_url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                # common og:image
                m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
                if m:
                    img = m.group(1)
                    if img.startswith("//"):
                        img = "https:" + img
                    return img
                # meta name pattern
                m = re.search(r'<meta[^>]+name=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
                if m:
                    img = m.group(1)
                    if img.startswith("//"):
                        img = "https:" + img
                    return img
                # JSON-LD: look for "image"
                m = re.search(r'"image"\s*:\s*"(https?://[^"]+)"', html)
                if m:
                    img = m.group(1)
                    if img.startswith("//"):
                        img = "https:" + img
                    return img
    except Exception as ex:
        # keep logs concise
        print(f"⚠️ fetch_image failed: {ex}")
    return None

# ---------------- Image worker logic ---------------- #

async def image_worker(name):
    """Background worker: takes tasks from IMAGE_TASK_QUEUE and tries to fetch & send images."""
    global IMAGE_TASK_QUEUE, IMAGE_TASK_SEMAPHORE
    print(f"🔧 Image worker {name} started")
    while True:
        task = await IMAGE_TASK_QUEUE.get()
        try:
            product_urls = task.get("urls", [])
            caption = task.get("caption", "")
            # try each url until image found
            found_img = None
            for u in product_urls:
                # first resolve redirects fast using HEAD under semaphore
                async with IMAGE_TASK_SEMAPHORE:
                    final = u
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.head(u, allow_redirects=True, timeout=6) as r:
                                final = str(r.url)
                    except Exception:
                        final = u
                # attempt to fetch image (GET)
                async with IMAGE_TASK_SEMAPHORE:
                    img = await fetch_image_from_url(final)
                if img:
                    found_img = img
                    break
            # If image found, send to Telegram and WAHA (best-effort)
            if found_img:
                try:
                    # send photo to Telegram as separate message
                    await client.send_file(CHANNEL_ID, found_img, caption=caption)
                    print(f"✅ Image posted to Telegram by worker {name}")
                except Exception as ex:
                    print(f"⚠️ Worker {name} failed to post image to Telegram: {ex}")
                # send to WAHA too (best-effort)
                if WHATSAPP_CHANNEL_ID:
                    try:
                        await send_to_whatsapp_with_image(caption, found_img)
                    except Exception:
                        pass
            else:
                # nothing found; log short line
                print(f"⚠️ Worker {name}: no image found for task")
        except Exception as e:
            print(f"⚠️ Worker {name} exception: {e}")
        finally:
            IMAGE_TASK_QUEUE.task_done()

# ---------------- Bot main ---------------- #

async def bot_main():
    global IMAGE_TASK_QUEUE, IMAGE_TASK_SEMAPHORE
    # init queue + semaphore
    IMAGE_TASK_QUEUE = asyncio.Queue()
    IMAGE_TASK_SEMAPHORE = asyncio.Semaphore(5)  # throttles simultaneous fetches

    # start worker tasks
    for i in range(WORKER_COUNT):
        asyncio.create_task(image_worker(f"W{i+1}"))

    await client.start()
    sources = []
    for i in SOURCE_IDS:
        try:
            e = await client.get_entity(i)
            sources.append(e.id)
            print(f"✅ Connected to source: {e.title}")
        except Exception as ex:
            print(f"❌ Failed to connect source {i}: {ex}")

    print(f"🚀 Monitoring {len(sources)} Telegram sources")
    print(f"🔗 Forwarding to Telegram channel {CHANNEL_ID}")
    if WHATSAPP_CHANNEL_ID:
        print(f"🔗 Also forwarding to WhatsApp channel {WHATSAPP_CHANNEL_ID}")

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        """Main quick handler: do light processing and send text immediately, enqueue image work."""
        global seen_urls, seen_products, last_msg_time

        # ignore media messages for now (can extend later)
        if e.message.media:
            return

        raw_txt = e.message.message or e.message.text or ""
        if not raw_txt:
            return

        # quick expand and processing (non-blocking)
        processed_text = await process(raw_txt)
        expanded_for_urls = await expand_all(raw_txt)
        urls = re.findall(r"https?://\S+", expanded_for_urls)

        # dedupe canonical products with 1-hour window (3600s)
        now = time.time()
        new_canon = []
        for u in urls:
            final_url = u
            # try quick head to resolve redirect (non-blocking)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.head(u, allow_redirects=True, timeout=4) as r:
                        final_url = str(r.url)
            except Exception:
                final_url = u
            c = canonicalize(final_url)
            if not c:
                continue
            last_seen = seen_products.get(c)
            if not last_seen or (now - last_seen) > 3600:  # 1 hour window
                new_canon.append(c)

        if not new_canon:
            # short log: skipped duplicate
            print("⚠️ Skipped: duplicate product(s) within 1-hour window")
            return

        # mark newly accepted products
        for c in new_canon:
            seen_products[c] = now

        # mark urls to seen_urls for stats
        for u in urls:
            seen_urls.add(u)

        # prepare header and truncated message
        hdr = ""
        if any(c.startswith("amazon:") for c in new_canon): hdr = "📦 Amazon Deal:\n"
        elif any(c.startswith("flipkart.com:") for c in new_canon): hdr = "🛒 Flipkart Deal:\n"
        elif any(c.startswith("myntra.com:") for c in new_canon): hdr = "👗 Myntra Deal:\n"
        elif any(c.startswith("ajio.com:") for c in new_canon): hdr = "👟 Ajio Deal:\n"

        msg = truncate_message(hdr + processed_text)

        # 1) Send text immediately to Telegram (NO link preview to be fastest)
        try:
            await client.send_message(CHANNEL_ID, msg, link_preview=False)
            print("✅ Sent text to Telegram (fast)")
        except Exception as ex:
            print(f"❌ Telegram send error: {ex}")

        # 2) Send text immediately to WhatsApp (best-effort)
        if WHATSAPP_CHANNEL_ID:
            try:
                await send_to_whatsapp(msg)
            except Exception:
                pass

        # 3) Enqueue image fetching & posting (background worker will handle)
        # Provide the list of URLs to try (original expanded urls)
        if urls:
            await IMAGE_TASK_QUEUE.put({"urls": urls, "caption": msg})

        last_msg_time = time.time()

    await client.run_until_disconnected()

# ---------------- Maintenance helpers ---------------- #

def redeploy():
    hook = DEPLOY_HOOK
    if not hook:
        print("⚠️ Deploy hook not set!")
        return False
    try:
        requests.post(hook, timeout=10)
        print("✅ Auto redeploy triggered")
        return True
    except Exception as e:
        print(f"❌ Redeploy failed: {e}")
        return False

def keep_alive():
    """HTTP ping to local app to reduce cold-starts on free providers."""
    while True:
        try:
            time.sleep(14 * 60)
            requests.get("http://127.0.0.1:10000/ping", timeout=5)
        except Exception:
            pass

def monitor_health():
    """Monitor last_msg_time and trigger redeploy if idle for too long."""
    global last_msg_time
    while True:
        time.sleep(300)
        since = time.time() - last_msg_time
        if since > 1800:
            print(f"⚠️ No messages for {int(since)//60} minutes — triggering redeploy")
            redeploy()

def start_loop(loop):
    for attempt in range(5):
        try:
            print(f"🚀 Starting Telegram bot (attempt {attempt+1})...")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError:
            print("⚠️ TypeNotFoundError; retrying in 10s")
            time.sleep(10)
        except Exception as ex:
            print(f"❌ Bot start error: {ex}")
            if attempt < 4:
                time.sleep(10)
            break

# ---------------- Flask endpoints ---------------- #

@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "service": "FastDeals Bot - Telegram + WhatsApp (Local WAHA)",
        "telegram_channel": str(CHANNEL_ID),
        "whatsapp_channel": WHATSAPP_CHANNEL_ID or "not configured",
        "waha_url": WAHA_API_URL
    })

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    return jsonify({
        "time_since_last_message": int(time.time() - last_msg_time),
        "unique_links_processed": len(seen_urls),
        "whatsapp_configured": bool(WHATSAPP_CHANNEL_ID),
        "whatsapp_last_success": int(time.time() - whatsapp_last_success) if whatsapp_last_success else None,
        "status": "healthy" if (time.time() - last_msg_time) < 3600 else "inactive",
        "waha_type": "Local via ngrok"
    })

@app.route("/stats")
def stats():
    return jsonify({
        "unique_links": len(seen_urls),
        "last_message_time": last_msg_time,
        "telegram_channel": CHANNEL_ID,
        "whatsapp_channel": WHATSAPP_CHANNEL_ID,
        "bot_running": True,
        "waha_url": WAHA_API_URL
    })

@app.route("/redeploy", methods=["POST"])
def redeploy_endpoint():
    ok = redeploy()
    return ("Redeploy triggered", 200) if ok else ("Redeploy failed", 500)

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp():
    """Send a test message to WAHA to verify connectivity"""
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"status": "error", "message": "WhatsApp not configured"})
    try:
        test_msg = "🧪 Test message from FastDeals bot via Local WAHA!"
        r = requests.post(f"{WAHA_API_URL}/api/sendText",
                          headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
                          json={"chatId": WHATSAPP_CHANNEL_ID, "text": test_msg, "session": "default"},
                          timeout=10)
        if r.status_code == 200:
            return jsonify({"status": "success", "message": "Test message sent to WhatsApp via Local WAHA"})
        else:
            return jsonify({"status": "error", "message": f"Failed ({r.status_code})"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
    """
    Update WAHA_API_URL dynamically (so your laptop/ngrok can push new ngrok URL).
    Example usage on laptop after starting ngrok:
      curl -X POST https://<render-app>/update-waha-url -H "Content-Type: application/json" \
         -d '{"url":"https://abcd-1234.ngrok-free.app"}'
    """
    global WAHA_API_URL
    try:
        data = request.get_json(force=True)
        new_url = data.get("url")
        if not new_url:
            return jsonify({"status": "error", "message": "No URL provided"}), 400
        WAHA_API_URL = new_url.rstrip("/")
        print(f"🔄 WAHA URL updated: {WAHA_API_URL}")
        return jsonify({"status": "success", "new_url": WAHA_API_URL})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------------- Entrypoint ---------------- #

if __name__ == "__main__":
    print("🚀 Starting FastDeals Bot (optimized)...")
    print(f"📱 Telegram Channel: {CHANNEL_ID}")
    print(f"💬 WhatsApp Channel: {WHATSAPP_CHANNEL_ID}")
    print(f"🔗 Local WAHA API: {WAHA_API_URL}")

    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()

    # Start WAHA keep-alive pinger if WAHA configured
    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()

    print("🌐 Starting web server on port 10000...")
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False, threaded=True)
