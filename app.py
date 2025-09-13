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
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# Local WAHA API configuration (via ngrok)
WAHA_API_URL = os.getenv('WAHA_API_URL')
WAHA_API_KEY = os.getenv('WAHA_API_KEY')
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')

# Telegram source channel IDs (deduplicated)
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947,
    -1001404064358, -1001772002285, -1001373588507
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)', r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)', r'(https?://www\.amazon\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

async def keep_waha_alive():
    while True:
        await asyncio.sleep(300)
        if WAHA_API_URL and WAHA_API_KEY:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{WAHA_API_URL}/api/version",
                        headers={"X-Api-Key": WAHA_API_KEY},
                        timeout=10
                    ) as resp:
                        if resp.status == 200:
                            print("✅ WAHA keep-alive ping successful")
                        else:
                            print(f"⚠️ WAHA ping failed: {resp.status}")
            except Exception as e:
                print(f"❌ WAHA keep-alive error: {e}")

async def send_whatsapp(message):
    global whatsapp_last_success
    if not (WAHA_API_URL and WAHA_API_KEY and WHATSAPP_CHANNEL_ID):
        print("❌ WhatsApp config missing")
        return False
    try:
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{WAHA_API_URL}/api/sendText", json=payload, headers=headers, timeout=15
            ) as resp:
                if resp.status == 200:
                    print("✅ Sent to WhatsApp")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"❌ WhatsApp API HTTP {resp.status}")
                    text = await resp.text()
                    print(f"Response truncated: {text[:120]}...")
                    return False
    except Exception as e:
        print(f"❌ WhatsApp error: {e}")
        return False

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

def convert_amazon_links(text):
    pattern = r'(https?://(?:www\.)?amazon\.in/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))'
    return re.sub(
        pattern,
        lambda m: f"https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}",
        text,
        flags=re.IGNORECASE
    )

def convert_earnkaro_links(text):
    patterns = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)'
    ]
    for pat in patterns:
        text = re.sub(
            pat,
            lambda m: f"https://earnkaro.com/store/?id={EARNKARO_ID}&url={m.group(1)}",
            text,
            flags=re.IGNORECASE
        )
    return text

async def shorten_earnkaro_links(text):
    urls = re.findall(r'https?://earnkaro\.com/store/\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                api = f"https://tinyurl.com/api-create.php?url={url}"
                async with session.get(api, timeout=5) as resp:
                    short = await resp.text()
                    text = text.replace(url, short)
            except:
                pass
    return text

async def process_text(text):
    t = await expand_links(text)
    t = convert_amazon_links(t)
    t = convert_earnkaro_links(t)
    t = await shorten_earnkaro_links(t)
    return t

def extract_amazon_thumbnail(text):
    match = re.search(r'/dp/([A-Z0-9]{10})', text)
    if match:
        asin = match.group(1)
        return f"https://m.media-amazon.com/images/I/{asin}._SL75_.jpg"
    return None

async def bot_main():
    await client.start()
    sources = []
    for sid in SOURCE_IDS:
        try:
            ent = await client.get_entity(sid)
            sources.append(ent)
            print(f"✅ Subscribed to {ent.title}")
        except Exception as e:
            print(f"❌ Failed {sid}: {e}")
    print(f"🚀 Monitoring {len(sources)} channels")

    @client.on(events.NewMessage(chats=sources))
    async def handler(ev):
        global last_msg_time, seen_urls
        if ev.message.media:
            return
        raw = ev.message.message or ev.message.text or ""
        if not raw:
            return
        final = await process_text(raw)
        urls = re.findall(r'https?://\S+', final)
        new = [u for u in urls if u not in seen_urls]
        if not new:
            return
        seen_urls.update(new)
        hdr = ""
        if any("flipkart" in u or "fkrt" in u for u in new):
            hdr = "🛒 Flipkart Deals\n"
        elif any("myntra" in u for u in new):
            hdr = "👗 Myntra Deals\n"
        elif any("amazon" in u for u in new):
            hdr = "📦 Amazon Deals\n"
        msg = hdr + final
        thumb = extract_amazon_thumbnail(msg)
        try:
            if thumb:
                await client.send_file(CHANNEL_ID, thumb, caption=msg)
            else:
                await client.send_message(CHANNEL_ID, msg, link_preview=False)
            print("✅ Telegram sent")
            if WHATSAPP_CHANNEL_ID:
                await send_whatsapp(msg)
        except Exception as e:
            print(f"❌ Send error: {e}")
        last_msg_time = time.time()

    await client.run_until_disconnected()

def redeploy():
    if DEPLOY_HOOK:
        try:
            requests.post(DEPLOY_HOOK, timeout=10)
            print("✅ Redeploy triggered")
            return True
        except Exception as e:
            print(f"❌ Redeploy failed: {e}")
    return False

def keep_alive():
    while True:
        time.sleep(14*60)
        try:
            requests.get("http://127.0.0.1:10000/ping", timeout=5)
        except:
            pass

def monitor_health():
    while True:
        time.sleep(300)
        if time.time() - last_msg_time > 1800:
            print("⚠️ No messages for 30+ min, redeploying")
            redeploy()

def start_loop(loop):
    for i in range(5):
        try:
            print(f"🚀 Bot start attempt {i+1}")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except common.TypeError as te:
            print(f"Retry due to {te}")
            time.sleep(10)
        except Exception as ex:
            print(f"❌ Bot error: {ex}")
            time.sleep(10)

# Flask endpoints
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"status":"running","telegram":CHANNEL_ID,"whatsapp":WHATSAPP_CHANNEL_ID})

@app.route("/ping")
def ping(): return "pong"

@app.route("/health")
def health():
    status = "healthy" if (time.time()-last_msg_time)<3600 else "inactive"
    return jsonify({"status":status,"links":len(seen_urls)})

@app.route("/redeploy", methods=["POST"])
def redeploy_ep():
    return ("OK",200) if redeploy() else ("Fail",500)

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp_ep():
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"error":"no whatsapp configured"}),400
    r = requests.post(
        f"{WAHA_API_URL}/api/sendText",
        headers={"X-Api-Key":WAHA_API_KEY,"Content-Type":"application/json"},
        json={"chatId":WHATSAPP_CHANNEL_ID,"text":"Test","session":"default"},
        timeout=10
    )
    return (r.text, r.status_code)

if __name__=="__main__":
    print("Starting Bot")
    loop = asyncio.new_event_loop()
    Thread(target=start_loop,args=(loop,),daemon=True).start()
    Thread(target=keep_alive,daemon=True).start()
    Thread(target=monitor_health,daemon=True).start()
    Thread(target=lambda: asyncio.run(keep_waha_alive()),daemon=True).start()
    app.run(host="0.0.0.0",port=10000)
