# Technical Monitor Bot

Replicates the investing.com Technical panel (all 8 timeframes, 1 Min → Monthly) from Binance candle data and pings you on Telegram.

## Alerts
- **ALL BUY / ALL SELL** — every timeframe flips green or red for a coin (fires once per flip)
- **Exit signal** — you told the bot you're in a position and 5+ of 8 timeframes flip against you

## Commands (message your bot)
- `/start` — numbered list of coins that are currently ALL BUY or ALL SELL
- `/coin 1,3,5` — track those picks from the /start list (green = long, red = short)
- `/long btc` / `/short eth` — track manually
- `/close btc` — stop tracking
- `/status` — ratings for your open positions

Commands are picked up on the next scheduled run (up to ~15–20 min).

## Setup
1. Message **@BotFather** on Telegram → `/newbot` → copy the token.
2. Message your new bot anything, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy your `chat.id`.
3. Create a **private** GitHub repo with these files.
4. Repo → Settings → Secrets and variables → Actions → add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. Edit `COINS` at the top of `monitor.py` to match the coins tradeable on Involio (Binance symbols, e.g. `"DOGEUSDT"`).
6. Actions tab → enable workflows → run **Technical Monitor** once manually to test.

## Notes
- Ratings use the standard vote method (12 moving averages + 9 oscillators, TradingView-style thresholds). They'll track investing.com closely but may differ by one notch occasionally.
- GitHub cron isn't exact — runs can lag 5–15 min. Fine for swing signals, not for scalping the 1m chart.
- `EXIT_MAJORITY` in `monitor.py` controls how many timeframes must flip against you before the exit alert (default 5 of 8).
