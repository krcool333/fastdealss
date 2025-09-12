# FastDeals Bot - Telegram to WhatsApp Integration

Complete automation bot that forwards deal messages from multiple Telegram channels to both your Telegram channel and WhatsApp channel using WAHA API.

## Features
- ✅ Monitors multiple Telegram deal channels
- ✅ Processes and converts affiliate links (Amazon, Flipkart, Myntra, etc.)
- ✅ Forwards to your Telegram channel
- ✅ Forwards to your WhatsApp channel using WAHA API
- ✅ URL shortening and expansion
- ✅ Health monitoring and auto-restart
- ✅ Web dashboard with status monitoring

## Setup Instructions

### 1. Environment Variables
Copy the `.env` file and update with your credentials:

```env
TELEGRAM_TOKEN=your_telegram_bot_token
CHANNEL_ID=your_telegram_channel_id
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
AFFILIATE_TAG=your_amazon_affiliate_tag
WAHA_API_KEY=kr_cool_99987
WHATSAPP_CHANNEL_ID=120363421452755716@newsletter
RENDER_DEPLOY_HOOK=your_render_deploy_hook_url
```

### 2. Deploy on Render
1. Connect this repository to Render
2. Create a new Web Service
3. Set Build Command: `pip install -r requirements.txt`
4. Set Start Command: `python app.py`
5. Add all environment variables from `.env` file
6. Deploy

### 3. WhatsApp Setup
The bot will automatically start WAHA container and connect to your WhatsApp channel using the provided channel ID.

## API Endpoints

- `GET /` - Service status and info
- `GET /health` - Health check with statistics
- `GET /stats` - Processing statistics
- `GET /waha-status` - WAHA service status
- `POST /test-whatsapp` - Send test message to WhatsApp
- `POST /redeploy` - Trigger service restart

## Monitoring

The bot includes:
- Health monitoring with auto-restart
- Processing statistics tracking
- WAHA status monitoring
- Keep-alive functionality for Render free tier

## Requirements

- Python 3.8+
- Docker (for WAHA integration)
- Render account (or any hosting service)
- Telegram Bot Token
- WhatsApp account for WAHA connection

## License

MIT License - Feel free to use and modify as needed.