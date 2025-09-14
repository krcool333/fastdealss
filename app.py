import os, asyncio, re, aiohttp, time, threading, requests, urllib.parse, json
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# Local WAHA API Configuration
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
seen_products = {}   # Dedup store
last_msg_time = time.time()
whatsapp_last_success = 0
client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

# ---------------- Utils ---------------- #

async def keep_waha_alive():
    """Ping WAHA every 5 minutes, auto-retry if disconnected"""
    global WAHA_API_URL
    while True:
        await asyncio.sleep(300)
        if not WAHA_API_URL: 
            continue
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{WAHA_API_URL}/api/version",
                                       headers={"X-Api-Key": WAHA_API_KEY},
                                       timeout=10) as response:
                    if response.status == 200:
                        print("✅ WAHA alive")
                    else:
                        print(f"⚠️ WAHA ping failed {response.status}")
        except Exception:
            print("⚠️ WAHA not reachable, will retry...")

async def send_to_whatsapp(message):
    """Send message to WhatsApp via WAHA (auto-retry safe)"""
    global whatsapp_last_success, WAHA_API_URL
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        return False
    try:
        url = f"{WAHA_API_URL}/api/sendText"
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=15) as r:
                if r.status == 200:
                    print("✅ Forwarded to WhatsApp")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"⚠️ WAHA API Error {r.status}")
                    return False
    except Exception:
        print("⚠️ WAHA unreachable, skipping WhatsApp")
        return False

async def expand_all(text):
    urls = sum((re.findall(p, text) for p in SHORT_PATTERNS), [])
    if not urls: return text
    async with aiohttp.ClientSession() as s:
        for u in urls:
            try:
                async with s.head(u, allow_redirects=True, timeout=5) as r:
                    text = text.replace(u, str(r.url))
            except:
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
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&amp;url=\S+', text)
    if not urls: return text
    async with aiohttp.ClientSession() as s:
        for u in urls:
            try:
                api = f"http://tinyurl.com/api-create.php?url={u}"
                async with s.get(api, timeout=5) as r:
                    short = await r.text()
                    text = text.replace(u, short)
            except:
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
    if m: return f"amazon:{m.group(1)}"
    for dom in ["flipkart.com", "myntra.com", "ajio.com"]:
        if dom in url:
            return dom + ":" + url.split("?")[0].rstrip("/")
    return None

def truncate_message(msg):
    if len(msg) <= 700: return msg
    urls = re.findall(r'https?://\S+', msg)
    more_link = urls[0] if urls else ""
    return msg[:500] + "...\n👉 More: " + more_link

# ---------------- Bot ---------------- #

async def bot_main():
    await client.start()
    sources = []
    for i in SOURCE_IDS:
        try:
            e = await client.get_entity(i)
            sources.append(e.id)
            print(f"✅ Connected: {e.title}")
        except Exception as ex:
            print(f"❌ Source {i} failed: {ex}")
    print(f"🚀 Monitoring {len(sources)} sources")

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global last_msg_time, seen_products
        if e.message.media: return
        txt = e.message.message or e.message.text or ""
        if not txt: return

        expanded = await expand_all(txt)
        urls = re.findall(r'https?://\S+', expanded)
        now = time.time()

        new_canon = []
        async with aiohttp.ClientSession() as s:
            for u in urls:
                try:
                    async with s.head(u, allow_redirects=True, timeout=5) as r:
                        final = str(r.url)
                except:
                    final = u
                c = canonicalize(final)
                if not c: continue
                if c not in seen_products or now - seen_products[c] > 18000:
                    new_canon.append(c)
        if not new_canon: return
        for c in new_canon: seen_products[c] = now

        out = await process(txt)
        hdr = ""
        if any(c.startswith("amazon:") for c in new_canon): hdr = "📦 Amazon Deal:\n"
        elif any(c.startswith("flipkart") for c in new_canon): hdr = "🛒 Flipkart Deal:\n"
        elif any(c.startswith("myntra") for c in new_canon): hdr = "👗 Myntra Deal:\n"
        elif any(c.startswith("ajio") for c in new_canon): hdr = "👟 Ajio Deal:\n"
        msg = truncate_message(hdr + out)

        try:
            await client.send_message(CHANNEL_ID, msg, link_preview=True)
            print("✅ Forwarded to Telegram")
            if WHATSAPP_CHANNEL_ID:
                await send_to_whatsapp(msg)
        except Exception as ex:
            print(f"❌ Send error: {ex}")
        last_msg_time = time.time()

    await client.run_until_disconnected()

# ---------------- Maintenance ---------------- #

def redeploy():
    hook = DEPLOY_HOOK
    if hook:
        try:
            requests.post(hook, timeout=10)
            print("✅ Auto redeploy triggered")
            return True
        except Exception as e:
            print(f"❌ Redeploy failed: {e}")
            return False
    print("⚠️ Deploy hook not set!")
    return False

def keep_alive():
    while True:
        try:
            time.sleep(14 * 60)
            requests.get("http://127.0.0.1:10000/ping", timeout=5)
        except:
            pass

def monitor_health():
    while True:
        time.sleep(300)
        since = time.time() - last_msg_time
        if since > 1800:
            print(f"⚠️ Idle {since//60} minutes, redeploying...")
            redeploy()

def start_loop(loop):
    for attempt in range(5):
        try:
            print(f"🚀 Starting Telegram bot (attempt {attempt+1})...")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError:
            print("⚠️ Type not found, retrying...")
            time.sleep(10)
        except Exception as ex:
            print(f"❌ Bot error: {ex}")
            if attempt < 4: time.sleep(10)
            break

# ---------------- Flask ---------------- #

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "FastDeals Bot - Telegram + WhatsApp (Local WAHA)",
        "telegram_channel": str(CHANNEL_ID),
        "whatsapp_channel": WHATSAPP_CHANNEL_ID or "not configured",
        "waha_url": WAHA_API_URL
    })

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health():
    return jsonify({
        "time_since_last_message": int(time.time() - last_msg_time),
        "unique_links_processed": len(seen_urls),
        "whatsapp_configured": bool(WHATSAPP_CHANNEL_ID),
        "whatsapp_last_success": int(time.time() - whatsapp_last_success) if whatsapp_last_success else None,
        "status": "healthy" if (time.time() - last_msg_time) < 3600 else "inactive"
    })

@app.route('/stats')
def stats():
    return jsonify({
        "unique_links": len(seen_urls),
        "last_message_time": last_msg_time,
        "telegram_channel": CHANNEL_ID,
        "whatsapp_channel": WHATSAPP_CHANNEL_ID,
        "bot_running": True,
        "waha_url": WAHA_API_URL
    })

@app.route('/redeploy', methods=['POST'])
def redeploy_endpoint():
    ok = redeploy()
    return ("Redeploy triggered", 200) if ok else ("Redeploy failed", 500)

@app.route('/test-whatsapp', methods=['POST'])
def test_whatsapp():
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"status": "error", "message": "WhatsApp not configured"})
    try:
        test_msg = "🧪 Test message from FastDeals bot!"
        r = requests.post(f"{WAHA_API_URL}/api/sendText",
                          headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
                          json={"chatId": WHATSAPP_CHANNEL_ID, "text": test_msg, "session": "default"},
                          timeout=10)
        if r.status_code == 200:
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": f"Failed {r.text}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/waha-health')
def waha_health():
    try:
        r = requests.get(f"{WAHA_API_URL}/api/version",
                         headers={"X-Api-Key": WAHA_API_KEY}, timeout=5)
        if r.status_code == 200:
            return jsonify({"status": "healthy", "waha": r.json()})
        else:
            return jsonify({"status": "error", "code": r.status_code})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/update-waha-url', methods=['POST'])
def update_waha_url():
    """Update WAHA_API_URL dynamically from laptop/ngrok"""
    global WAHA_API_URL
    try:
        data = request.get_json()
        new_url = data.get("url")
        if not new_url:
            return jsonify({"status": "error", "message": "No URL provided"}), 400
        WAHA_API_URL = new_url.rstrip("/")
        print(f"🔄 WAHA URL updated: {WAHA_API_URL}")
        return jsonify({"status": "success", "new_url": WAHA_API_URL})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------------- Main ---------------- #

if __name__ == '__main__':
    print("🚀 Starting FastDeals Bot...")
    print(f"📱 Telegram Channel: {CHANNEL_ID}")
    print(f"💬 WhatsApp Channel: {WHATSAPP_CHANNEL_ID}")
    print(f"🔗 Local WAHA API: {WAHA_API_URL}")
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()
    print("🌐 Starting web server on port 10000...")
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=True)
