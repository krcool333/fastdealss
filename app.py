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

# Load env
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
AMAZON_TAG = "lootast21"  # your affiliate tag
EARNKARO_ID = "459844"    # your earnkaro id

WAHA_API_URL = os.getenv('WAHA_API_URL')  # your WAHA URL e.g. ngrok
WAHA_API_KEY = os.getenv('WAHA_API_KEY')  # your WAHA key
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')

SOURCE_IDS = [
    -1001315464303, -1001714047949, -100170757170, # etc.
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)', r'(https?://dl\.flipkart\.com/\S+)',
    r'(https?://ajio\.me/\S+)', r'(https?://amzn\.to/\S+)', r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

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

async def send_whatsapp(message):
    global whatsapp_last_success
    if not WAHA_API_URL or not WAHA_API_KEY or not WHATSAPP_CHANNEL_ID:
        print("❌ WAHA config not set")
        return False
    try:
        payload = {"chatId": WHATSAPP_CHANNEL_ID, "text": message, "session": "default"}
        headers = {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{WAHA_API_URL}/api/sendText", json=payload, headers=headers, timeout=15
            ) as resp:
                if resp.status == 200:
                    print("✅ Message sent to WhatsApp")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"❌ WAHA Error: HTTP {resp.status}")
                    text = await resp.text()
                    # Silence large logs
                    print(f"WAHA Response: {text[:100]}...[truncated]")
                    return False
    except Exception as e:
        print(f"❌ WAHA send error: {e}")
        return False

async def expand_links(text):
    urls = sum((re.findall(pat, text) for pat in SHORT_PATTERNS), [])
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
    pats = [
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?)/(?:dp|gp/product)/([A-Z0-9]{10}))'
    ]
    for pat in pats:
        text = re.sub(
            pat,
            lambda m: f"https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}",
            text,
            flags=re.I,
        )
    return text

def convert_earnkaro_links(text):
    pats = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:dl\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)',
        r'(https?://(?:www\.)?ajio\.com/\S+)',
        r'(https?://(?:www\.)?nykaa\.com/\S+)',
    ]
    for pat in pats:
        text = re.sub(
            pat,
            lambda m: f"https://earnkaro.com/store?id={EARNKARO_ID}&url={m.group(1)}",
            text,
            flags=re.I,
        )
    return text

async def shorten_earnkaro_links(text):
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                api = f"http://tinyurl.com/api-create.php?url={url}"
                async with session.get(api, timeout=5) as resp:
                    short = await resp.text()
                    text = text.replace(url, short)
            except:
                pass
    return text

async def process_text(text):
    expanded = await expand_links(text)
    amazon = convert_amazon_links(expanded)
    earnkaro = convert_earnkaro_links(amazon)
    shortened = await shorten_earnkaro_links(earnkaro)
    return shortened

def extract_telegram_message_image(text):
    # Tries to extract product image from affiliate links for Telegram message
    # Looks for amzn.to or amazon.in links and converts to small product images
    img_url = None
    match = re.search(r"(https?://www\.amazon\.in/.*/dp/([A-Z0-9]{10}))", text)
    if match:
        asin = match.group(2)
        # Use Amazon product image url pattern (small img)
        img_url = f"http://images.amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg"
    return img_url

async def send_message_to_telegram(channel_id, message, image_url=None):
    try:
        if image_url:
            await client.send_file(channel_id, image_url, caption=message)
        else:
            await client.send_message(channel_id, message, link_preview=False)
        print("✅ Sent message to telegram")
    except Exception as e:
        print(f"❌ Telegram send error: {e}")

async def bot_main():
    await client.start()
    sources = []
    for id_ in SOURCE_IDS:
        try:
            e = await client.get_entity(id_)
            sources.append(e)
            print(f"✅ Connected to {e.title}")
        except Exception as err:
            print(f"❌ Failed to connect {id_}: {err}")
    print(f"🚀 Monitoring {len(sources)} sources")

    @client.on(events.NewMessage(chats=sources))
    async def handler(event):
        global last_msg_time, seen_urls
        if event.message.media:
            return
        raw_text = event.message.text or event.message.message or ""
        if not raw_text:
            return
        processed_text = await process_text(raw_text)
        urls = re.findall(r"https?://\S+", processed_text)
        new_urls = [u for u in urls if u not in seen_urls]
        if not new_urls:
            return
        seen_urls.update(new_urls)

        # Extract product image if any
        img_url = extract_telegram_message_image(processed_text)

        # Build formatted message prefix
        header = ""
        if any("flipkart" in url or "fkrt" in url for url in new_urls):
            header = "🛒 Flipkart Deal\n"
        elif any("myntra" in url for url in new_urls):
            header = "👗 Myntra Deal\n"
        elif any("amazon" in url for url in new_urls):
            header = "📦 Amazon Deal\n"

        final_message = header + processed_text

        try:
            # Send to Telegram with image (if any)
            await send_message_to_telegram(CHANNEL_ID, final_message, img_url)

            # Send to WhatsApp
            if WHATSAPP_CHANNEL_ID:
                await send_whatsapp(final_message)

            last_msg_time = time.time()
        except Exception as exc:
            print(f"❌ Send error: {exc}")

    await client.run_until_disconnected()

# Additional functions: redeploy, keep_alive, monitor_health, start_loop

# Add your existing functions redeploy, keep_alive, monitor_health, start_loop here

if __name__ == "__main__":
    print("🚀 Starting Telegram Bot with WAHA Integration")
    print(f"Telegram Channel: {API_ID}")
    print(f"WhatsApp Channel: {WHATSAPP_CHANNEL_ID}")
    print(f"WAHA API URL: {WAHA_API_URL}")

    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()

    if WAHA_API_URL:
        Thread(target=lambda: asyncio.run(keep_waha_alive()), daemon=True).start()

    app.run(host="0.0.0.0", port=10000)
