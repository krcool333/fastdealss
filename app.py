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

# List of Telegram groups/channels to monitor: usernames, invite links, or numeric IDs
SOURCE_GROUPS_INPUTS = [
    'CrazyOffersDealssss',             # Crazy Offers Deals - COD (username)
    'Yaha_Everything',                 # Yaha Everything (username)
    'AFMdealzone',                    # AFM Dealzone (username)
    'shoppinglootindia',              # Shopping Loot India (username)
    'shoppingloot8',                  # Shopping Loot 8 (username)
    'universaldeals'                  # Universal Deals (username)
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
            # Forward full original message including media and formatting
            await client.forward_messages(CHANNEL_ID, event.message)
        except Exception as e:
            print(f"Error forwarding message: {e}")

    print("Bot is running... Listening to source groups.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
