# Telegram QR Bot — فارسی / English

A bilingual Telegram bot that converts text, links, and very small files into QR codes.

## Features

- Persian + English user interface
- Text/link → QR
- Small file → Base64 → QR
- Conservative center banana logo (18%) with QR error correction level H
- Last 10 generated QR images per Telegram user
- SQLite history
- `/start`, `/help`, `/history`
- No token hardcoded in source code

## Token

The bot token supplied for this build is already placed directly in `telegram_qr_bot.py` so the project can be dropped into GitHub without editing the code.

⚠️ Anyone who can access the repository can see the token. Use a private repository if you want to keep this test bot private. For a public repository, revoke the token and move it to an environment variable.

## GitHub setup

Upload these files directly to your repository:

```text
telegram_qr_bot.py
requirements.txt
.gitignore
.env.example
README.md
fonts/
```

Then create `.env` on the server/host:

```text
BOT_TOKEN=YOUR_NEW_TOKEN
```

Do NOT upload `.env`.

## Run

```bash
pip install -r requirements.txt
python telegram_qr_bot.py
```

Python 3.10+ recommended.

## History

The bot stores the generated QR PNG for each user and keeps only the newest 10 records per Telegram user. The SQLite file is local to the machine running the bot and is ignored by Git.

## QR/logo reliability

The center logo is intentionally limited to 18% of the QR image and the QR uses error correction level H. This is safer than the previous 22% logo. The logo still cannot make QR scanning mathematically guaranteed on every camera/condition, so real-device scanning should be tested after deployment.

## File limitation

A QR code has limited capacity. Files are intentionally capped at 900 bytes before Base64 encoding. For normal files, use a download link instead.
