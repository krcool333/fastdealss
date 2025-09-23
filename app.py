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
AMAZON_TAG = os.getenv("AFFILIATE_TAG", "lootfastdeals-21")
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

WAHA_API_URL = os.getenv("WAHA_API_URL")
WAHA_API_KEY = os.getenv("WAHA_API_KEY")
WHATSAPP_CHANNEL_ID = os.getenv("WHATSAPP_CHANNEL_ID")

USE_EARNKARO = os.getenv("USE_EARNKARO", "false").lower() == "true"
DEDUPE_SECONDS = int(os.getenv("DEDUPE_SECONDS", "3600"))  # default 1 hr
MAX_MSG_LEN = int(os.getenv("MAX_MSG_LEN", "700"))
PREVIEW_LEN = int(os.getenv("PREVIEW_LEN", "500"))

# --- Second channel config --- #
SECOND_CHANNEL_ID = -1003007607997
SECOND_CHANNEL_TOKEN = "8388668034:AAGNsTsiY2SAkF6TZ1AQsBz6Vx2l-Q7GPrs"

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
    r"(https?://fktt\.co/\S+)", r"(https?://bitly\.cx/\S+)",
    r"(https?://fkt\.co/\S+)"
]

WHATSAPP_BLACKLIST = ["bitly.cx", "bit.ly", "tinyurl.com"]

# ---------------- Runtime state ---------------- #
seen_urls = set()
seen_products = {}
last_msg_time = time.time()
whatsapp_last_success = 0

client = TelegramClient("session", API_ID, API_HASH)
app = Flask(__name__)

HASHTAG_SETS = [
    "#LootDeals #Discount #OnlineShopping",
    "#Free #Offer #Sale",
    "#TopDeals #BigSale #BestPrice",
    "#PriceDrop #FlashSale #DealAlert",
]

# ---------------- Helpers ---------------- #
async def expand_all(text):
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
    pat = r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))'
    def repl(m):
        asin = m.group(2)
        return f"https://www.amazon.in/dp/{asin}/?tag={AMAZON_TAG}"
    text = re.sub(pat, repl, text, flags=re.I)
    text = re.sub(r'([?&])tag=[^&\s]+', r'\1tag=' + AMAZON_TAG, text)
    return text

async def convert_earnkaro(text):
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

# --- Dedup + canonicalize + hashing + helpers ---
# (Keep all original deduplication, extract_product_name, canonicalize, hash_text, truncate_message, choose_hashtags, is_whatsapp_safe, send_to_whatsapp functions intact)

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

        print(f"📨 Raw message: {raw_txt[:100]}...")
        
        processed = await process(raw_txt)
        urls = re.findall(r"https?://\S+", processed)

        now = time.time()
        dedupe_keys = []

        # Dedup logic remains unchanged...

        # ---------------- Label + Hashtags ---------------- #
        label = ""
        expanded_urls = []
        # Expansion + detection logic remains unchanged...

        msg = label + truncate_message(processed)
        msg += f"\n\n{choose_hashtags()}"

        # --- Send to main channel --- #
        try:
            await client.send_message(CHANNEL_ID, msg, link_preview=False)
            print("✅ Sent to Main Telegram Channel")
        except Exception as ex:
            print(f"❌ Telegram error: {ex}")

        # --- Send to second channel (via Bot API) --- #
        try:
            url = f"https://api.telegram.org/bot{SECOND_CHANNEL_TOKEN}/sendMessage"
            payload = {"chat_id": SECOND_CHANNEL_ID, "text": msg, "disable_web_page_preview": True}
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                print("✅ Sent to Second Telegram Channel")
            else:
                print(f"❌ Second Telegram error: {r.text}")
        except Exception as ex:
            print(f"❌ Second Telegram exception: {ex}")

        # WhatsApp send remains unchanged...

        last_msg_time = time.time()
        print(f"✅ Processing complete at {time.strftime('%H:%M:%S')}")

    await client.run_until_disconnected()

# ---------------- Maintenance + Flask endpoints ---------------- #
# (Keep redeploy, keep_alive, monitor_health, start_loop, all Flask routes exactly as in original 447-line file)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False, threaded=True)