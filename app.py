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

# List of sources: either usernames, invite links, or channel IDs
SOURCE_GROUPS_INPUTS = [
    'https://t.me/+sX1Ht4p33nFjZDE1',  # Offerzone 2.0 (invite link, will be resolved)
    -1001361058246,                    # QUICK DEALS (numeric ID)
    'CrazyOffersDealssss',             # Crazy Offers Deals - COD (username)
    'Yaha_Everything'                  # Yaha Everything (username)
]

client = TelegramClient('session', API_ID, API_HASH)
bot = Bot(BOT_TOKEN)

async def resolve_source_groups(inputs):
    resolved = []
    for src in inputs:
        try:
            entity = await client.get_entity(src)
            resolved.append(entity.id)
        except Exception as e:
            print(f"Failed to resolve {src}: {e}")
    return resolved

async def main():
    await client.start()
    source_groups = await resolve_source_groups(SOURCE_GROUPS_INPUTS)
    
    print(f"Monitoring source groups/channels: {source_groups}")

    @client.on(events.NewMessage(chats=source_groups))
    async def handler(event):
        try:
            message = event.message.message or ""
            if message.strip():
                await bot.send_message(chat_id=CHANNEL_ID, text=message)
        except Exception as e:
            print(f"Error forwarding message: {e}")

    print("Bot is running... Listening to source groups.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
