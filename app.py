import os
import asyncio
import logging
from telethon import TelegramClient, events
from telegram import Bot
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# List of Telegram groups/channels to monitor
SOURCE_GROUPS_INPUTS = [
    'CrazyOffersDealssss',
    'Yaha_Everything',
    'AFMdealzone',
    'shoppinglootindia',
    'shoppingloot8',
    'universaldeals'
]

# Use bot token for authentication (no phone input needed)
SESSION_NAME = 'bot_session'

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
bot = Bot(BOT_TOKEN)

async def safe_client_start():
    """Safely start the Telegram client using bot token"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Start using bot token (no phone input required)
            await client.start(bot_token=BOT_TOKEN)
            logger.info("Telegram client started successfully with bot token")
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
    logger.info("Starting Telegram bot...")
    
    # Start client safely with bot token
    if not await safe_client_start():
        logger.error("Could not start Telegram client. Exiting.")
        return
    
    try:
        # Resolve source groups
        source_groups = await resolve_source_groups(SOURCE_GROUPS_INPUTS)
        logger.info(f"Monitoring {len(source_groups)} source groups/channels")
        
        # Check if we resolved any groups
        if not source_groups:
            logger.error("No source groups could be resolved. Please check the group IDs/usernames.")
            return

        @client.on(events.NewMessage(chats=source_groups))
        async def handler(event):
            try:
                await client.forward_messages(CHANNEL_ID, event.message)
                logger.info(f"✅ Forwarded message from chat ID: {event.chat_id}")
            except Exception as e:
                logger.error(f"❌ Error forwarding message: {e}")

        logger.info("🤖 Bot is running and listening to source groups...")
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Unexpected error in main: {e}")
    finally:
        await client.disconnect()
        logger.info("Client disconnected")

if __name__ == '__main__':
    asyncio.run(main())