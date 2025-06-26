# Discord Music Bot

A simple Discord bot built with `discord.py` and `yt-dlp` that can join a voice channel and play music from YouTube.

## Features

- Slash-command interface (no `!prefix` spam!)
- Search YouTube and play the **first result**: `/search <keywords>`
- Play a specific YouTube URL: `/play <url>`
- Playback controls: `/pause`, `/resume`, `/skip`
- Show the queue: `/queue`
- Leave the voice channel: `/leave`

## Requirements

1. Python **3.9+**
2. A Discord **bot token** – [create one here](https://discord.com/developers/applications)
3. FFmpeg (the binary is **not** committed to the repo).

## Quick start

```bash
# 1. Clone the repo & cd into it
$ git clone https://github.com/sai-ruthviz/DiscordPlayerTest.git
$ cd DiscordPlayerTest

# 2. Create a virtual environment (optional but recommended)
$ python -m venv .venv
$ source .venv/bin/activate      # Linux/MacOS
# .venv\Scripts\activate.ps1    # Windows PowerShell

# 3. Install dependencies
$ pip install -r requirements.txt

# 4. Configure environment variables
$ cp .env.example .env           # then edit the file and paste your token

# 5. Run the bot
$ python player.py
```

The first time the bot starts it will **sync** all slash-commands. This can take up to an hour globally, but guild-specific sync happens instantly.

## Environment variables

Create a `.env` file at the project root:

```
DISCORD_TOKEN=your_discord_bot_token_here
```

## Deployment notes

- **Never commit** your real `.env` or token.
- Large binaries such as `ffmpeg.exe` are ignored via `.gitignore`. Provide your own copy in production (or install FFmpeg via your package manager).
- For production hosting (Heroku, Railway, etc.) make sure to set the `DISCORD_TOKEN` config variable and have FFmpeg available on the `$PATH`.

## License

This project is released under the MIT License – see `LICENSE` for details. 