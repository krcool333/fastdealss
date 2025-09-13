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
AMAZON_TAG = "lootfastsales-21"  # your affiliate tag
EARN_KARO_ID = "459844"          # your EarnKaro ID

WAHA_API_URL = os.getenv('WAHA_API_URL')   # e.g. ngrok URL
WAHA_API_KEY = os.getenv('WAHA_API_KEY')   # secret API key
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')  # WhatsApp Channel ID

SOURCE_IDS = [
    -1001315464303, -1001717949, -1001707570, ... # your channels
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)', r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)', r'(https?://amazon\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

# ====== Enhanced keep alive =======
async def keep_waha_alive():
    while True:
        try:
            await asyncio.sleep(300)
            if WAHA_API_URL:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{WAHA_API_URL}/api/version",
                        headers={"X-Api-Key": WAHA_API_KEY},
                        timeout=10,
                    ) as resp:
                        if resp.status == 200:
                            print("✅ WAHA keep-alive ping successful")
                        else:
                            print(f"⚠️ WAHA keep-alive ping failed: {resp.status}")
        except Exception as e:
            print(f"❌ WAHA keep-alive error: {e}")

# ====== Send message to WhatsApp with compact logs =====
async def send_whatsapp(message):
    global whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        print("❌ WAHA config missing")
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
                    # Only print first 120 chars to reduce log spam
                    print(f"Response content (truncated): {text[:120]}...")
                    return False
    except Exception as e:
        print(f"❌ WhatsApp send error: {e}")
        return False

# ====== Utilities to process message text ======
async def expand_links(text):
    urls = sum([re.findall(p, text) for p in SHORT_PATTERNS], [])
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
        flags=re.IGNORECASE,
    )

def convert_earnkaro_links(text):
    patterns = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)',
    ]
    for pat in patterns:
        text = re.sub(
            pat,
            lambda m: f"https://earnkaro.com/store/?id={EARN_KARO_ID}&url={m.group(1)}",
            text,
            flags=re.IGNORECASE,
        )
    return text

async def shorten_earnkaro_links(text):
    urls = re.findall(r'https?://earnkaro.com/store/\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                api = f"https://tinyurl.com/api-create.php?url={url}"
                async with session.get(api, timeout=5) as resp:
                    short_url = await resp.text()
                    text = text.replace(url, short_url)
            except:
                pass
    return text

async def process_text(text):
    expanded = await expand_links(text)
    amazon = convert_amazon_links(expanded)
    earnkaro = convert_earnkaro_links(amazon)
    shortened = await shorten_earnkaro_links(earnkaro)
    return shortened

# ====== Extract Amazon thumbnail for Telegram message =====
def extract_amazon_thumbnail(text):
    match = re.search(r'https?://www\.amazon\.in/(?:.*?/)?dp/([A-Z0-9]{10})', text)
    if match:
        asin = match.group(1)
        # Thumbnail image URL format on Amazon
        return f"https://m.media-amazon.com/images/I/{asin}._SL75_.jpg"
    return None

# ====== Send Telegram message with thumbnail =====
async def send_telegram_message(channel_id, message, thumbnail_url=None):
    try:
        if thumbnail_url:
            await client.send_file(channel_id, thumbnail_url, caption=message)
        else:
            await client.send_message(channel_id, message, link_preview=False)
        print("✅ Telegram message sent")
    except Exception as e:
        print(f"❌ Telegram send error: {e}")

# ====== Main bot code =====
async def bot_main():
    await client.start()
    sources = []
    for sid in SOURCE_IDS:
        try:
            entity = await client.get_entity(sid)
            sources.append(entity)
            print(f"✅ Connected to {entity.title}")
        except Exception as e:
            print(f"❌ Failed to fetch source {sid}: {e}")

    print(f"🚀 Monitoring {len(sources)} sources")
    print(f"Forwarding to Telegram {CHANNEL_ID}")
    if WHATSAPP_CHANNEL_ID:
        print(f"Also forwarding to WhatsApp {WHATSAPP_CHANNEL_ID}")

    @client.on(events.NewMessage(chats=sources))
    async def handler(ev):
        global last_msg_time, seen_urls

        if ev.message.media:
            return

        raw_text = ev.message.message or ev.message.text or ""
        if not raw_text:
            return

        processed_msg = await process_text(raw_text)
        urls = re.findall(r'https?://\S+', processed_msg)
        new_urls = [u for u in urls if u not in seen_urls]
        if not new_urls:
            return

        seen_urls.update(new_urls)

        # Prepare message header
        header = ""
        if any("flipkart" in u or "fkrt" in u for u in new_urls):
            header = "🛒 Flipkart Deals\n"
        elif any("myntra" in u for u in new_urls):
            header = "👗 Myntra Deals\n"
        elif any("amazon" in u for u in new_urls):
            header = "📦 Amazon Deals\n"

        final_message = header + processed_msg

        # Extract thumbnail if possible
        thumbnail_url = extract_amazon_thumbnail(final_message)

        try:
            # Send formatted message + image to Telegram
            if thumbnail_url:
                await client.send_file(CHANNEL_ID, thumbnail_url, caption=final_message)
            else:
                await client.send_message(CHANNEL_ID, final_message, link_preview=False)
            print("✅ Telegram message sent")

            # Send text-only to WhatsApp (WhatsApp shows link preview itself)
            if WHATSAPP_CHANNEL_ID:
                await send_whatsapp(final_message)

        except Exception as exc:
            print(f"❌ Message send failed: {exc}")

        last_msg_time = time.time()

    await client.run_until_disconnected()

# ====== Additional functions =====
def redeploy():
    hook = DEPLOY_HOOK
    if hook:
        try:
            requests.post(hook, timeout=10)
            print("✅ Deploy triggered")
            return True
        except Exception as ex:
            print(f"❌ Deploy failed: {ex}")
            return False
    print("⚠️ Deploy hook not set")
    return False

def keep_alive():
    while True:
        try:
            time.sleep(14 * 60)
            requests.get("http://127.0.0.1:10000/ping")
        except:
            pass

def monitor_health():
    while True:
        time.sleep(300)
        since = time.time() - last_msg_time
        if since > 1800:
            print(f"⚠️ No recent messages for {int(since/60)} mins, triggering deploy...")
            redeploy()

def start_loop(loop):
    for trial in range(5):
        try:
            print(f"Starting bot (trial {trial+1})...")
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except common.TypeError as te:
            print(f"Retrying due to {te}")
            time.sleep(10)
        except Exception as e:
            print(f"Bot error: {e}")
            if trial == 4:
                raise

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify(
        {
            "status": "running",
            "service": "Telegram & WhatsApp Bot",
            "telegram_channel": CHANNEL_ID,
            "whatsapp_channel": WHATSAPP_CHANNEL_ID,
            "uptime_hours": round((time.time() - last_msg_time)/3600, 2),
            "last_message_at": last_msg_time,
        }
    )

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    status = "healthy" if (time.time() - last_msg_time) < 3600 else "inactive"
    return jsonify(
        {
            "uptime_hours": round((time.time() - last_msg_time)/3600, 2),
            "unique_links": len(seen_urls),
            "whatsapp_uptime": round((time.time() - whatsapp_last_success)/60, 2),
            "status": status,
        }
    )

@app.route("/stats")
def stats():
    return jsonify(
        {
            "unique_links_processed": len(seen_urls),
            "last_message_in_seconds": last_msg_time,
            "telegram_channel_id": CHANNEL_ID,
            "whatsapp_channel_id": WHATSAPP_CHANNEL_ID,
            "bot_running": True,
        }
    )

@app.route("/redeploy", methods=["POST"])
def redeploy_endpoint():
    if redeploy():
        return "Redeploy triggered", 200
    else:
        return "Failed to redeploy", 500

@app.route("/test-whatsapp", methods=["POST"])
def test_whatsapp_endpoint():
    if not WHATSAPP_CHANNEL_ID:
        return jsonify({"status": "error", "message": "WhatsApp not configured"})
    try:
        testmsg = "Test message from bot 🧪"
        response = requests.post(
            f"{WAHA_API_URL}/api/sendText",
            headers={"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"},
            json={"chatId": WHATSAPP_CHANNEL_ID, "text": testmsg, "session": "default"},
            timeout=10,
        )
        if response.status_code == 200:
            return jsonify({"status": "success", "message": "Test message sent"})
        else:
            return jsonify({"status": "error", "message": response.text})
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
    print(f"Telegram Channel: {CHANNEL_ID}")
    print(f"WhatsApp Channel: {WHATSAPP_CHANNEL_ID}")
    print(f"WAHA API URL: {WAHA_API_URL}")

    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()

    app.run(host="0.0.0.0", port=10000)
