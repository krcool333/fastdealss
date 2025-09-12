import os
import time
import re
import asyncio
import aiohttp
import threading
import requests
import docker
import signal

from threading import Thread
from flask import Flask, jsonify, request
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

# Telegram credentials
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# Affiliate tags
AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"

# Render redeploy hook
DEPLOY_HOOK = os.getenv("RENDER_DEPLOY_HOOK")

# WAHA WhatsApp API Configuration
WAHA_API_KEY = os.getenv('WAHA_API_KEY')                   # e.g. "kr_cool_99987"
WHATSAPP_CHANNEL_ID = os.getenv('WHATSAPP_CHANNEL_ID')     # e.g. "120363421452755716@newsletter"
WAHA_PORT = 3000

# Telegram source channels to monitor
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947
]

# URL patterns to expand and rewrite
SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)',
    r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)',
    r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)',
    r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)',
    r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
last_msg_time = time.time()

# Initialize clients
telegram_client = TelegramClient('session', API_ID, API_HASH)
flask_app = Flask(__name__)
docker_client = docker.from_env()

# WAHA container management
def start_waha():
    try:
        c = docker_client.containers.get('waha')
        c.stop()
        c.remove()
    except:
        pass
    docker_client.containers.run(
        'devlikeapro/waha',
        name='waha',
        detach=True,
        ports={f'{WAHA_PORT}/tcp': WAHA_PORT},
        environment={'WAHA_API_KEY': WAHA_API_KEY},
        volumes={'/tmp/waha-sessions': {'bind': '/app/.sessions', 'mode': 'rw'}},
        remove=True
    )
    time.sleep(15)

def check_waha():
    try:
        r = requests.get(
            f'http://localhost:{WAHA_PORT}/api/version',
            headers={'X-Api-Key': WAHA_API_KEY},
            timeout=5
        )
        return r.status_code == 200
    except:
        return False

async def send_to_whatsapp(message):
    if not check_waha():
        start_waha()
        await asyncio.sleep(5)
    url = f'http://localhost:{WAHA_PORT}/api/sendText'
    headers = {
        'X-Api-Key': WAHA_API_KEY,
        'Content-Type': 'application/json'
    }
    payload = {
        'chatId': WHATSAPP_CHANNEL_ID,
        'text': message,
        'session': 'default'
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                print("WAHA error", resp.status, await resp.text())

# URL processing
async def expand_all(text):
    urls = sum((re.findall(p, text) for p in SHORT_PATTERNS), [])
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for u in urls:
            try:
                async with session.head(u, allow_redirects=True, timeout=5) as r:
                    text = text.replace(u, str(r.url))
            except:
                pass
    return text

def convert_amazon(text):
    pats = [
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))'
    ]
    for p in pats:
        text = re.sub(
            p,
            lambda m: f'https://www.amazon.in/dp/{m.group(2)}/?tag={AMAZON_TAG}',
            text, flags=re.I
        )
    return text

def convert_earnkaro(text):
    parts = [
        r'(https?://(?:www\.)?flipkart\.com/\S+)',
        r'(https?://(?:www\.)?myntra\.com/\S+)'
    ]
    for p in parts:
        text = re.sub(
            p,
            lambda m: f'https://earnkaro.com/store?id={EARNKARO_ID}&url={m.group(1)}',
            text, flags=re.I
        )
    return text

async def shorten_earnkaro(text):
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&url=\S+', text)
    if not urls:
        return text
    async with aiohttp.ClientSession() as session:
        for u in urls:
            try:
                async with session.get(f'http://tinyurl.com/api-create.php?url={u}', timeout=5) as r:
                    short = await r.text()
                    text = text.replace(u, short)
            except:
                pass
    return text

async def process_text(text):
    t = await expand_all(text)
    t = convert_amazon(t)
    t = convert_earnkaro(t)
    t = await shorten_earnkaro(t)
    return t

# Telegram bot logic
async def bot_main():
    await telegram_client.start()
    sources = []
    for sid in SOURCE_IDS:
        try:
            ent = await telegram_client.get_entity(sid)
            sources.append(ent.id)
        except:
            pass

    @telegram_client.on(events.NewMessage(chats=sources))
    async def handler(event):
        global last_msg_time
        msg_text = event.message.message or event.message.text or ''
        if not msg_text or event.message.media:
            return

        out = await process_text(msg_text)
        urls = re.findall(r'https?://\S+', out)
        new_urls = [u for u in urls if u not in seen_urls]
        if not new_urls:
            return
        seen_urls.update(new_urls)

        header = ''
        if any('flipkart.com' in u for u in new_urls):
            header = '🛒 Flipkart Deal\n'
        elif any('myntra.com' in u for u in new_urls):
            header = '👗 Myntra Deal\n'
        elif any('amazon.in' in u for u in new_urls):
            header = '📦 Amazon Deal\n'

        final_msg = header + out
        await telegram_client.send_message(CHANNEL_ID, final_msg, link_preview=False)
        await send_to_whatsapp(final_msg)
        last_msg_time = time.time()

    await telegram_client.run_until_disconnected()

# Graceful shutdown
def shutdown(sig, frame):
    try:
        docker_client.containers.get('waha').stop()
    except:
        pass
    exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# Flask web server
app = Flask(__name__)

@app.route('/ping')
def ping():
    return 'pong'

@app.route('/health')
def health():
    return jsonify({
        'time_since_last_message': int(time.time() - last_msg_time),
        'unique_links': len(seen_urls),
        'waha_running': check_waha()
    })

if __name__ == '__main__':
    print("🚀 Starting WAHA container...")
    start_waha()

    print("🚀 Starting Telegram bot...")
    loop = asyncio.new_event_loop()
    Thread(target=lambda: loop.run_until_complete(bot_main()), daemon=True).start()

    print("🌐 Starting web server on port 10000...")
    app.run(host='0.0.0.0', port=10000, threaded=True, debug=False)
