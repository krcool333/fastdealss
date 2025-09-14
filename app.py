# ... (all imports + existing code same as before)
import os, asyncio, re, aiohttp, time, threading, requests, urllib.parse, json
from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# Load env
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

# (all your variables same as before…)

# ---------------- IMAGE FETCHING ---------------- #

async def fetch_image_from_url(product_url):
    """Fetch main product image (og:image) from product page"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(product_url, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Look for og:image tag
                    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
                    if m:
                        return m.group(1)
    except Exception as e:
        print(f"⚠️ Failed to fetch image: {e}")
    return None

async def send_to_whatsapp_with_image(caption, image_url):
    """Send WhatsApp message with image + caption via WAHA"""
    global whatsapp_last_success, WAHA_API_URL
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
                    print("✅ WhatsApp sent (image+caption)")
                    whatsapp_last_success = time.time()
                    return True
                else:
                    print(f"⚠️ WAHA image send error {r.status}")
                    return False
    except Exception:
        print("⚠️ WAHA unreachable (image send)")
        return False

# ---------------- BOT MAIN ---------------- #

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

        # Try fetch product image
        img_url = None
        for u in urls:
            img_url = await fetch_image_from_url(u)
            if img_url: break

        try:
            if img_url:
                # Send as photo with caption
                await client.send_file(CHANNEL_ID, img_url, caption=msg)
                print("✅ Forwarded (image+caption) to Telegram")
                if WHATSAPP_CHANNEL_ID:
                    await send_to_whatsapp_with_image(msg, img_url)
            else:
                # Fallback to normal message
                await client.send_message(CHANNEL_ID, msg, link_preview=True)
                print("✅ Forwarded (text only) to Telegram")
                if WHATSAPP_CHANNEL_ID:
                    await send_to_whatsapp(msg)
        except Exception as ex:
            print(f"❌ Send error: {ex}")
        last_msg_time = time.time()

    await client.run_until_disconnected()

# ... (all other functions, redeploy, health, Flask endpoints, update-waha-url etc. SAME AS before)
