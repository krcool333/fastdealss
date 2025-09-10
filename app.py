import os, asyncio, re, aiohttp, time
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.errors.common import TypeNotFoundError
from dotenv import load_dotenv

# Load env vars
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

AMAZON_TAG = "lootfastdeals-21"
EARNKARO_ID = "4598441"
SOURCE_IDS = [
    -1001315464303, -1001714047949, -1001707571730,
    -1001820593092, -1001448358487, -1001378801949,
    -1001387180060, -1001361058246,
    -1001561964907, -1002444882171, -1001505338947
]

SHORT_PATTERNS = [
    r'(https?://fkrt\.cc/\S+)', r'(https?://myntr\.it/\S+)',
    r'(https?://dl\.flipkart\.com/\S+)', r'(https?://ajio\.me/\S+)',
    r'(https?://amzn\.to/\S+)', r'(https?://amzn\.in/\S+)',
    r'(https?://bit\.ly/\S+)', r'(https?://tinyurl\.com/\S+)'
]

seen_urls = set()
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
        r'(https?://(?:www\.)?amazon\.(?:com|in)/(?:.*?/)?(?:dp|gp/product)/([A-Z0-9]{10}))(?:[/?].*?)?(?:\?|&)tag=[^&\s]*'
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

async def shorten_all(text):
    urls = re.findall(r'https?://\S+', text)
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
    t = await shorten_all(t)
    return t

async def bot_main():
    await client.start()
    sources = []
    for i in SOURCE_IDS:
        try: e = await client.get_entity(i); sources.append(e.id)
        except: pass

    @client.on(events.NewMessage(chats=sources))
    async def h(e):
        global seen_urls
        if e.message.media: return
        txt = e.message.text or ""
        if not txt: return
        out = await process(txt)
        urls = re.findall(r'https?://\S+', out)
        new = [u for u in urls if u not in seen_urls]
        if not new: return
        seen_urls.update(new)
        await client.send_message(CHANNEL_ID, out, link_preview=False)

    await client.run_until_disconnected()

def start_loop(loop):
    for i in range(5):
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_main())
            break
        except TypeNotFoundError:
            time.sleep(10)
        except Exception:
            break

@app.route('/')
def home(): return "Bot running"
@app.route('/ping')
def ping(): return "pong"

if __name__ == '__main__':
    l = asyncio.new_event_loop()
    t = Thread(target=start_loop, args=(l,))
    t.daemon = True
    t.start()
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False, threaded=False)
