import os
import asyncio
from telethon import TelegramClient, events
from dotenv import load_dotenv

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path=dotenv_path)

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

SOURCE_GROUPS_INPUTS = [
    'CrazyOffersDealssss',
    'Yaha_Everything',
    'AFMdealzone',
    'shoppinglootindia',
    'shoppingloot8',
    'universaldeals'
]

client = TelegramClient('session', API_ID, API_HASH)

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
            message = event.message.text or event.message.message or ""
            # If there are media attachments, you can also handle them here
            if event.message.media:
                await client.send_file(CHANNEL_ID, event.message.media, caption=message)
            elif message.strip():
                await client.send_message(CHANNEL_ID, message)
            print(f"Sent message without forwarding: {message[:40]}")
        except Exception as e:
            print(f"Error posting message: {e}")

    print("Bot is running... Listening to source groups.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
