
import os
import asyncio
from telethon import TelegramClient, events
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# Add your source groups/channels here (usernames or IDs)
SOURCE_GROUPS = [
    'sX1Ht4p33nFjZDE1',   # Example username or group invite hash
    -1001361058246        # Numeric channel or group ID
]

client = TelegramClient('session', API_ID, API_HASH)
bot = Bot(BOT_TOKEN)

async def main():
    await client.start()

    @client.on(events.NewMessage(chats=SOURCE_GROUPS))
    async def handler(event):
        try:
            message = event.message.message or ""
            if message.strip():
                await bot.send_message(chat_id=CHANNEL_ID, text=message)
        except Exception as e:
            print(f"Error forwarding: {e}")

    print("Bot running... Listening to source groups.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
