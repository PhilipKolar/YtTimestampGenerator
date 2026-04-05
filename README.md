# YT Timestamps Bot

A Telegram bot that generates chapter timestamps for YouTube videos using Claude AI.

Send a YouTube link to the bot and it will fetch the transcript, identify major topic shifts, and reply with a clean timestamped chapter list.

## How it works

1. You send a YouTube URL to the bot from your phone
2. The bot fetches the video's transcript via YouTube's caption system
3. The transcript is sent to Claude API which identifies major topic shifts
4. The bot replies with a formatted list of timestamps and chapter titles

The bot runs on your own machine using Telegram's polling mechanism — it needs to be on and connected to the internet to work.

## Requirements

- Python 3.10+
- A Telegram account
- An Anthropic API account (pay-as-you-go credits)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/yt-timestamps.git
cd yt-timestamps
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot anthropic youtube-transcript-api python-dotenv
```

### 2. Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the token BotFather gives you

### 3. Get your Telegram user ID

1. Search for **@userinfobot** on Telegram and start it
2. Copy your numeric user ID

This is used to restrict the bot so only you can use it.

### 4. Get a Claude API key

1. Sign up at [console.anthropic.com](https://console.anthropic.com)
2. Go to **API Keys** → **Create Key** — copy it (shown once only)
3. Add billing credits under **Plans & Billing** (pay-as-you-go, not a subscription)

### 5. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
TELEGRAM_TOKEN=        # From BotFather
ANTHROPIC_API_KEY=     # From console.anthropic.com
ALLOWED_USER_ID=       # Your numeric Telegram user ID from @userinfobot
```

### 6. Test it manually

```bash
source venv/bin/activate
python3 bot.py
```

You should see `Bot started`. Open Telegram, find your bot, and send it a YouTube URL. If it replies with a chapter list, everything is working.

Press `Ctrl+C` to stop when done testing.

### 7. Run as a background service (Linux/systemd)

```bash
sudo cp yt-timestamps.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yt-timestamps
```

Check it's running:

```bash
sudo systemctl status yt-timestamps
```

### 8. Auto-restart on file changes

This restarts the bot automatically whenever you edit `bot.py` or `.env`.

```bash
sudo cp yt-timestamps-watch.path /etc/systemd/system/
sudo cp yt-timestamps-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yt-timestamps-watch.path
```

## Managing the service

```bash
# Check status
sudo systemctl status yt-timestamps

# View logs (live)
journalctl -u yt-timestamps -f

# Restart manually
sudo systemctl restart yt-timestamps

# Stop
sudo systemctl stop yt-timestamps

# Disable autostart on boot
sudo systemctl disable yt-timestamps
```

## Limitations

- Only works for videos that have captions (auto-generated or manual)
- Very long videos (3+ hours) will have their transcript trimmed to fit the model's context window
- Chapter quality depends on how well-structured the video's speech is

## Secrets reference

| Secret | Where to get it |
|--------|----------------|
| `TELEGRAM_TOKEN` | @BotFather on Telegram |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `ALLOWED_USER_ID` | Your numeric Telegram user ID — get it from @userinfobot |
