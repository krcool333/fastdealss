import os, asyncio, re, aiohttp, time, threading, requests
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
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730, -1001820593092,
    -1001448358487, -1001378801949, -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)', r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)', r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
last_msg_time = time.time()
client = TelegramClient('session', API_ID, API_HASH)
app = Flask(__name__)

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
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:\?|&)tag=[^&\s]*'
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
        text = re.sub(p, lambda m: f"https://earnkaro.com/store?id={EARNKARO_ID}&url={m.group(1)}", text, flags=re.I)
    return text

async def shorten_earnkaro(text):
    urls = re.findall(r'https?://earnkaro\.com/store\?id=\d+&url=\S+', text)
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
        except:
            pass

    @client.on(events.NewMessage(chats=sources))
    async def handler(e):
        global seen_urls, last_msg_time
        if e.message.media: return
        txt = e.message.message or e.message.text or ""
        if not txt: return
        out = await process(txt)
        urls = re.findall(r'https?://\S+', out)
        new = [u for u in urls if u not in seen_urls]
        if not new: return
        seen_urls.update(new)
        hdr = ""
        if any("flipkart.com" in u or "fkrt.cc" in u for u in new):
            hdr = "Flipkart Deal:\n"
        elif any("myntra.com" in u for u in new):
            hdr = "Myntra Deal:\n"
        elif any("amazon.in" in u for u in new):
            hdr = "Amazon Deal:\n"
        msg = hdr + out
        await client.send_message(CHANNEL_ID, msg, link_preview=False)
        last_msg_time = time.time()

    await client.run_until_disconnected()

def redeploy():
    hook = DEPLOY_HOOK
    if hook:
        try:
            requests.post(hook, timeout=10)
            return True
        except Exception as e:
            print(f"Redeploy failed: {e}")
            return False
    print("Deploy hook not set!")
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
        time.sleep(300)  # 5min
        since = time.time() - last_msg_time
        if since > 1800:  # 30min without messages
            print(f"Health: No msg for {int(since)//60} min, triggering redeploy...")
            redeploy()

def start_loop(loop):
    for _ in range(5):
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError:
            time.sleep(10)
        except:
            break

@app.route('/')
def home():
    return "Bot running"

@app.route('/ping')
def ping():
    return "pong"

@app.route('/health')
def health():
    return jsonify(time_since_last_message=int(time.time() - last_msg_time))

@app.route('/stats')
def stats():
    return jsonify(unique_links=len(seen_urls))

@app.route('/redeploy', methods=['POST'])
def redeploy_endpoint():
    ok = redeploy()
    return ("ok", 200) if ok else ("fail", 500)

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    Thread(target=start_loop, args=(loop,), daemon=True).start()
    Thread(target=keep_alive, daemon=True).start()
    Thread(target=monitor_health, daemon=True).start()
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=False)
