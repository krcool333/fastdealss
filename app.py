import os
import asyncio
import logging
from telethon import TelegramClient, events
from telegram import Bot
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# List of Telegram groups/channels to monitor
SOURCE_GROUPS_INPUTS = [
    'https://t.me/+sX1Ht4p33nFjZDE1',
    -1001361058246,
    'CrazyOffersDealssss',
    'Yaha_Everything',
    'AFMdealzone',
    'shoppinglootindia',
    'shoppingloot8',
    'universaldeals'
]

# Use a unique session name based on environment
SESSION_NAME = os.getenv('SESSION_NAME', 'telegram_monitor_session')

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
bot = Bot(BOT_TOKEN)

async def safe_client_start():
    """Safely start the Telegram client with session management"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await client.start()
            logger.info("Telegram client started successfully")
            return True
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            
            if "AuthKeyDuplicated" in str(e):
                logger.warning("Session conflict detected. Cleaning up session...")
                # Clean up session files
                session_files = [f for f in os.listdir('.') 
                               if f.startswith(SESSION_NAME)]
                for file in session_files:
                    try:
                        os.remove(file)
                        logger.info(f"Removed session file: {file}")
                    except:
                        pass
                
                # Wait before retry
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(5)
    
    logger.error("Failed to start client after multiple attempts")
    return False

async def resolve_source_groups(inputs):
    resolved = []
    for src in inputs:
        try:
            entity = await client.get_entity(src)
            resolved.append(entity.id)
            logger.info(f"Resolved {src} to ID: {entity.id}")
        except Exception as e:
            logger.error(f"Failed to resolve {src}: {e}")
    return resolved

async def main():
    # Start client safely
    if not await safe_client_start():
        logger.error("Could not start Telegram client. Exiting.")
        return
    
    try:
        source_groups = await resolve_source_groups(SOURCE_GROUPS_INPUTS)
        logger.info(f"Monitoring source groups/channels: {source_groups}")

        @client.on(events.NewMessage(chats=source_groups))
        async def handler(event):
            try:
                await client.forward_messages(CHANNEL_ID, event.message)
                logger.info(f"Forwarded message from {event.chat_id}")
            except Exception as e:
                logger.error(f"Error forwarding message: {e}")

        logger.info("Bot is running... Listening to source groups.")
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
    finally:
        await client.disconnect()
        logger.info("Client disconnected")

if __name__ == '__main__':
    asyncio.run(main())