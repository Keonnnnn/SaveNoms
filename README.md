# SaveNoms — Telegram Food Waste Tracker

A Telegram bot that helps you cut food waste. Snap a photo of your plate after eating and it estimates how much was left uneaten — in portions, not vague percentages — then gives you a tip for right-sizing your order next time.

## Features

- **Leftover analysis** — send a photo of your plate or drinks after a meal and the bot estimates how much went to waste, using Gemini 2.5 Flash (via OpenRouter).
- **Portions, not percentages** — a mixed plate (e.g. a roast dinner) is judged holistically as one item (`~0.25 portions wasted`), not broken into a per-ingredient breakdown. Countable items (apples, dumplings, wings, eggs, etc.) get an exact count instead (`2 apples wasted`).
- **Drinks included** — every visible cup, glass, bottle, or can is analysed as its own item alongside the food. If the liquid level can't be seen (capped, opaque), it's assumed untouched rather than guessed.
- **Sauces ignored** — condiments, dips, and dressings are excluded from the analysis entirely.
- **Waste report** — a waste level (minimal / low / moderate / high / severe) plus one specific, actionable tip each time (e.g. order a smaller size, share a platter, order fewer drinks).

## Commands

| Command  | Description                  |
|----------|-------------------------------|
| `/start` | Introduce SaveNoms             |
| `/help`  | Explain how the bot works      |

Just send a photo — no commands needed to trigger an analysis.

## Tech stack

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — bot framework
- [OpenRouter](https://openrouter.ai/) (`google/gemini-2.5-flash`) — food/waste image analysis
- [FastAPI](https://fastapi.tiangolo.com/) — webhook endpoint for serverless deployment
- [Pillow](https://python-pillow.org/) — image compression before sending to the AI
- [Vercel](https://vercel.com/) — serverless hosting (webhook mode)

## Project structure

```
bot.py           # Bot logic: commands, photo handler, waste analysis, report formatting
bot_app.py        # Local entry point — runs the bot with long polling
api/index.py      # Vercel serverless entry point — runs the bot via webhook (FastAPI)
vercel.json       # Routes all requests to api/index.py
requirements.txt  # Python dependencies
```

## Setup

1. **Install dependencies**

   ```bash
   python -m venv venv
   venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```

2. **Configure environment variables**

   Copy `.env.example` to `.env` and fill in your keys:

   ```
   BOT_TOKEN=your-telegram-bot-token
   OPENROUTER_API_KEY=your-openrouter-api-key
   ```

   - `BOT_TOKEN` — create a bot with [@BotFather](https://t.me/BotFather) on Telegram.
   - `OPENROUTER_API_KEY` — from [openrouter.ai](https://openrouter.ai/), used for image analysis.

   Never commit `.env` — it's already covered by `.gitignore`.

## Running locally

Runs the bot with long polling — no public URL needed:

```bash
python bot_app.py
```

Then open Telegram, find your bot, and send `/start` followed by a photo of a plate.

## Deployment (Vercel)

The bot can also run as a webhook via a FastAPI app on Vercel:

1. Push the repo to GitHub and import it into Vercel.
2. Set `BOT_TOKEN` and `OPENROUTER_API_KEY` as environment variables in the Vercel project settings.
3. Once deployed, register the webhook with Telegram:

   ```bash
   curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<your-vercel-domain>/api/telegram"
   ```

`vercel.json` rewrites all incoming requests to `api/index.py`, which lazily initializes the bot application and processes each update.
