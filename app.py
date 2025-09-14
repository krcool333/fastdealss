import os, asyncio, re, aiohttp, time, threading, requests
import urllib.parse
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

# Local WAHA API Configuration (via ngrok)
WAHA_API_URL = os.getenv('WAHA_API_URL')  # https://your-ngrok-url
WAHA_API_KEY = os.getenv('WAHA_API_KEY')
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')  # Your WhatsApp channel ID

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
seen_products = {}   # Track product URLs (canonical) with timestamps for dedup
last_msg_time = time.time()
whatsapp_last_success = 0
client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

async def keep_waha_alive():
    """Keep local WAHA service alive by pinging it every 5 minutes"""
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            if WAHA_API_URL:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{WAHA_API_URL}/api/version", 
                                           headers={"X-Api-Key": WAHA_API_KEY},
                                           timeout=10) as response:
                        if response.status == 200:
                            print("✅ Local WAHA keep-alive ping successful")
                        else:
                            print(f"⚠️ Local WAHA keep-alive ping failed: {response.status}")
        except Exception as e:
            print(f"❌ Local WAHA keep-alive error: {e}")

async def send_to_whatsapp(message):
    """Send message to WhatsApp Channel using local WAHA API"""
    global whatsapp_last_success
    
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        print("❌ Local WhatsApp API not configured")
        return False
    
    try:
        url = f"{WAHA_API_URL}/api/sendText"
        headers = {
            "X-Api-Key": WAHA_API_KEY,
            "Content-Type": "application/json"
        }
        
        payload = {
            "chatId": WHATSAPP_CHANNEL_ID,
            "text": message,
            "session": "default"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=15) as response:
                if response.status == 200:
                    print("✅ Message sent to WhatsApp via local WAHA")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"❌ Local WAHA API Error: {response.status}")
                    text = await response.text()
                    print(f"Error details: {text}")
                    return False
                        
    except Exception as e:
        print(f"❌ Local WAHA send error: {e}")
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
        global seen_urls, last_msg_time
        
        if e.message.media: return
        
        txt = e.message.message or e.message.text or ""
        if not txt: return
        
        # Deduplication based on final destination URLs (5-hour window)
        expanded = await expand_all(txt)
        urls_expanded = re.findall(r'https?://\S+', expanded)
        current_time = time.time()
        new_canonicals = []
        async with aiohttp.ClientSession() as session:
            for url in urls_expanded:
                try:
                    async with session.head(url, allow_redirects=True, timeout=5) as r:
                        final_url = str(r.url)
                except:
                    continue
                # Determine canonical product URL
                m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', final_url, flags=re.I)
                if m:
                    asin = m.group(1)
                    canonical = f"amazon.in/dp/{asin}"
                elif "flipkart.com" in final_url:
                    canonical = final_url.split('?')[0].rstrip('/')
                elif "myntra.com" in final_url:
                    canonical = final_url.split('?')[0].rstrip('/')
                elif "ajio.com" in final_url:
                    canonical = final_url.split('?')[0].rstrip('/')
                else:
                    continue
                # Check if within 5-hour window
                if canonical not in seen_products or current_time - seen_products.get(canonical, 0) > 18000:
                    new_canonicals.append(canonical)
        new_canonicals = list(set(new_canonicals))
        if not new_canonicals:
            return
        for c in new_canonicals:
            seen_products[c] = current_time
        
        # Process and forward the message
        out = await process(txt)
        urls_out = re.findall(r'https?://\S+', out)
        new_out_urls = []
        dup_out_urls = []
        async with aiohttp.ClientSession() as session:
            for out_url in urls_out:
                canonical = None
                if "amazon." in out_url:
                    m = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', out_url, flags=re.I)
                    if m:
                        asin = m.group(1)
                        canonical = f"amazon.in/dp/{asin}"
                elif any(x in out_url for x in ["tinyurl.com", "bit.ly", "amzn.to", "amzn.in"]):
                    try:
                        async with session.head(out_url, allow_redirects=True, timeout=5) as r:
                            final_earn = str(r.url)
                        parsed = urllib.parse.urlparse(final_earn)
                        query = urllib.parse.parse_qs(parsed.query)
                        orig_list = query.get('url') or []
                        if orig_list:
                            orig_url = orig_list[0]
                            m2 = re.search(r'amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10})', orig_url, flags=re.I)
                            if m2:
                                asin = m2.group(1)
                                canonical = f"amazon.in/dp/{asin}"
                            elif "flipkart.com" in orig_url:
                                canonical = orig_url.split('?')[0].rstrip('/')
                            elif "myntra.com" in orig_url:
                                canonical = orig_url.split('?')[0].rstrip('/')
                            elif "ajio.com" in orig_url:
                                canonical = orig_url.split('?')[0].rstrip('/')
                    except:
                        pass
                if canonical is None or canonical in new_canonicals:
                    new_out_urls.append(out_url)
                else:
                    dup_out_urls.append(out_url)
        if not new_out_urls:
            return
        # Update seen_urls for tracking
        seen_urls.update(new_out_urls)
        
        # Header based on product domain
        hdr = ""
        if any("flipkart.com" in c for c in new_canonicals):
            hdr = "🛒 Flipkart Deal:\n"
        elif any("myntra.com" in c for c in new_canonicals):
            hdr = "👗 Myntra Deal:\n"
        elif any("amazon.in" in c for c in new_canonicals):
            hdr = "📦 Amazon Deal:\n"
        # Remove duplicate URLs from message
        for u in dup_out_urls:
            out = out.replace(u, "")
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
            time.sleep(14 * 60)  # 14 minutes
            requests.get("http://127.0.0.1:10000/ping", timeout=5)
        except:
            pass

def monitor_health():
    while True:
        time.sleep(300)  # 5 minutes
        since = time.time() - last_msg_time
        
        # Auto redeploy if no messages for 30 minutes
        if since > 1800:
            print(f"⚠️ No messages for {int(since)//60} minutes, triggering redeploy...")
            redeploy()

def start_loop(loop):
    for attempt in range(5):
        try:
            print(f"🚀 Starting Telegram bot (attempt {attempt + 1})...")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError:
            print("⚠️ Type not found, retrying in 10 seconds...")
            time.sleep(10)
        except Exception as ex:
            print(f"❌ Bot error: {ex}")
            if attempt < 4:
                time.sleep(10)
            break

# Flask Routes
@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "FastDeals Bot - Telegram + WhatsApp (Local WAHA)",
        "telegram_channel": str(CHANNEL_ID),
        "whatsapp_channel": WHATSAPP_CHANNEL_ID or "not configured",
        "waha_type": "Local via ngrok",
        "waha_url": WAHA_API_URL
    })

@app.route('/ping')
def ping():
    return "pong"

@app.route('/health')
def health():
    return jsonify({
        "time_since_last_message": int(time.time() - last_msg_time),
        "unique_links_processed": len(seen_urls),
        "whatsapp_configured": bool(WHATSAPP_CHANNEL_ID),
        "whatsapp_last_success": int(time.time() - whatsapp_last_success) if whatsapp_last_success else None,
        "status": "healthy" if (time.time() - last_msg_time) < 3600 else "inactive",
        "waha_type": "Local via ngrok"
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
    """Test endpoint to send a message to WhatsApp via local WAHA"""
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"status": "error", "message": "WhatsApp not configured"})
    
    try:
        test_msg = "🧪 Test message from FastDeals bot via Local WAHA!"
        response = requests.post(f"{WAHA_API_URL}/api/sendText",
                               headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
                               json={"chatId": WHATSAPP_CHANNEL_ID, "text": test_msg, "session": "default"},
                               timeout=10)
        
        if response.status_code == 200:
            return jsonify({"status": "success", "message": "Test message sent to WhatsApp via Local WAHA"})
        else:
            return jsonify({"status": "error", "message": f"Failed: {response.text}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/waha-health')
def waha_health():
    """Check local WAHA API health"""
    try:
        response = requests.get(f"{WAHA_API_URL}/api/version",
                              headers={"X-Api-Key": WAHA_API_KEY},
                              timeout=5)
        if response.status_code == 200:
            return jsonify({"status": "healthy", "type": "Local WAHA", "waha": response.json()})
        else:
            return jsonify({"status": "error", "code": response.status_code})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    print("🚀 Starting FastDeals Bot with Local WAHA Integration...")
    print(f"📱 Telegram Channel: {CHANNEL_ID}")
    print(f"💬 WhatsApp Channel: {WHATSAPP_CHANNEL_ID}")
    print(f"🔗 Local WAHA API: {WAHA_API_URL}")
    
    # Start all threads
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    
    # Start WAHA keep-alive for local instance
    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()
    
    # Start Flask web server
    print("🌐 Starting web server on port 10000...")
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)
