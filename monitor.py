"""
Investing.com-style technical monitor -> Telegram alerts.
Runs on GitHub Actions. Recomputes the technical summary (MAs + oscillators)
from Binance candle data on 8 timeframes, exactly like the investing.com panel:
1m, 5m, 15m, 30m, 1h, 1d, 1w, 1M.

Alerts:
  - Coin flips to ALL BUY (every timeframe Buy/Strong Buy) or ALL SELL
  - You have an open position (/long btc, /short eth) and the majority of
    timeframes flip against you -> specific "close it" alert

Commands (send to your bot in Telegram, picked up on the next run):
  /long btc     /short eth     /close btc     /status
"""

import json
import os
import sys
import time
import requests
import pandas as pd
import numpy as np

# ----------------- CONFIG (edit me) -----------------
# Full Involio market list (from your screenshots)
INVOLIO_COINS = [
    "BTC", "ETH", "HYPE", "SOL", "ZEC", "CASHCAT", "XRP", "LIT", "PUMP",
    "ONDO", "KAITO", "FARTCOIN", "NEAR", "VVV", "AAVE", "UNI", "KBONK",
    "SUI", "ADA", "KPEPE", "LINK", "WLD", "BNB", "AVAX", "ARB", "XMR",
    "TRUMP", "TAO", "XPL", "ENA", "DOGE", "LTC", "GRAM", "ETHFI", "CRV",
    "PAXG", "BCH", "AERO", "INJ", "VIRTUAL", "DOT", "ZRO", "EIGEN", "LDO",
    "ENS", "JUP", "PENGU", "SPX", "JTO", "XLM", "WLFI", "APT", "TIA",
    "MET", "PYTH", "ICP", "MON", "MORPHO", "TRX", "VINE", "MEGA", "GRASS",
    "STABLE", "PENDLE", "AR", "FET", "WIF", "OP", "STRK", "POL", "CHIP",
    "HBAR", "KSHIB", "BLUR", "MOODENG", "ZORA", "SEI", "ASTER", "ETC",
    "BERA", "SYRUP", "RUNE", "FIL", "RESOLV", "ZETA", "USUAL", "LINEA",
    "MNT", "BSV", "NIL", "AVNT", "SKY", "PURR", "MINA", "SNX", "SUSHI",
    "GMX", "ORDI", "HMSTR", "DASH", "TRB", "APE", "STBL", "GALA", "W",
    "BANANA", "POPCAT", "ATOM", "DYDX", "PNUT", "MELANIA", "CC", "IO",
    "ZK", "CELO", "ALGO", "HEMI", "ALT", "MANTA", "BABY", "CFX", "KNEIRO",
    "0G", "AXS", "CAKE", "BRETT", "REZ", "MOVE", "RENDER", "GAS", "IMX",
    "BIGTIME", "COMP", "POLYX", "HYPER", "TURBO", "LAYER", "PROVE", "ME",
    "STX", "S", "IOTA", "ANIME", "DYM", "YGG", "INIT", "AIXBT", "MERL",
    "KLUNC", "GOAT", "2Z", "BIO", "ZEN", "WCT", "AZTEC", "PEOPLE", "SAND",
    "SUPER", "APEX", "KFLOKI", "GRIFFAIN", "KAS", "GMT", "FOGO", "UMA",
    "SKR", "NEO", "SAGA", "NXPC", "XAI", "SOPH", "MEME", "RSR", "TNSR",
    "NOT", "BOME",
]
# Involio's kilo-coins map to the normal Binance symbol (ratings are
# identical — the x1000 price scale doesn't change any indicator)
SYMBOL_OVERRIDES = {"KBONK": "BONK", "KPEPE": "PEPE", "KSHIB": "SHIB",
                    "KFLOKI": "FLOKI", "KLUNC": "LUNC"}
COINS = [SYMBOL_OVERRIDES.get(c, c) + "USDT" for c in INVOLIO_COINS]
TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"]
TF_NAMES = {"1m": "1 Min", "5m": "5 Min", "15m": "15 Min", "30m": "30 Min",
            "1h": "Hourly", "1d": "Daily", "1w": "Weekly", "1M": "Monthly"}
EXIT_MAJORITY = 5   # this many of 8 timeframes against your position = exit alert
# -----------------------------------------------------

# Binance public market-data mirror (works from US-based GitHub runners,
# unlike api.binance.com which geo-blocks)
KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
STATE_FILE = "state.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ENV_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# set in main() once state is loaded; the bot adopts the first private
# chat that messages it and stores it in state, so a wrong or missing
# TELEGRAM_CHAT_ID secret can't break delivery
_STATE = {}


def owner_chat_id():
    return str(_STATE.get("chat_id") or ENV_CHAT_ID)

BUYISH = {"Buy", "Strong Buy"}
SELLISH = {"Sell", "Strong Sell"}


# ----------------- data -----------------

def get_klines(symbol, interval, limit=300):
    r = requests.get(KLINES_URL, params={"symbol": symbol, "interval": interval,
                                         "limit": limit}, timeout=20)
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=["t", "o", "h", "l", "c", "v", "ct",
                                         "qv", "n", "tb", "tq", "ig"])
    for col in ["o", "h", "l", "c", "v"]:
        df[col] = df[col].astype(float)
    return df


# ----------------- indicator votes (+1 buy, -1 sell, 0 neutral) -----------------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def vote_mas(df):
    votes = []
    c = df["c"]
    last = c.iloc[-1]
    for p in [5, 10, 20, 50, 100, 200]:
        if len(c) > p:
            votes.append(1 if last > c.rolling(p).mean().iloc[-1] else -1)
            votes.append(1 if last > ema(c, p).iloc[-1] else -1)
    return votes


def vote_rsi(df, n=14):
    c = df["c"]
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / down)
    v, prev = rsi.iloc[-1], rsi.iloc[-2]
    if v > 70:
        return -1
    if v < 30:
        return 1
    return 1 if v > prev else -1


def vote_stoch(df, k=9, d=6):
    lo, hi = df["l"].rolling(k).min(), df["h"].rolling(k).max()
    kline = 100 * (df["c"] - lo) / (hi - lo)
    dline = kline.rolling(d).mean()
    kv, dv = kline.iloc[-1], dline.iloc[-1]
    if kv > 80 and kv < dv:
        return -1
    if kv < 20 and kv > dv:
        return 1
    return 1 if kv > dv else -1


def vote_macd(df):
    macd = ema(df["c"], 12) - ema(df["c"], 26)
    sig = ema(macd, 9)
    return 1 if macd.iloc[-1] > sig.iloc[-1] else -1


def vote_cci(df, n=14):
    tp = (df["h"] + df["l"] + df["c"]) / 3
    sma = tp.rolling(n).mean()
    mad = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (tp - sma) / (0.015 * mad)
    v = cci.iloc[-1]
    if v > 100:
        return 1
    if v < -100:
        return -1
    return 1 if v > cci.iloc[-2] else -1


def vote_willr(df, n=14):
    hi, lo = df["h"].rolling(n).max(), df["l"].rolling(n).min()
    wr = -100 * (hi - df["c"]) / (hi - lo)
    v = wr.iloc[-1]
    if v > -20:
        return -1
    if v < -80:
        return 1
    return 1 if v > wr.iloc[-2] else -1


def vote_roc(df, n=12):
    roc = df["c"].pct_change(n) * 100
    return 1 if roc.iloc[-1] > 0 else -1


def vote_uo(df):
    prev_c = df["c"].shift(1)
    bp = df["c"] - pd.concat([df["l"], prev_c], axis=1).min(axis=1)
    tr = pd.concat([df["h"], prev_c], axis=1).max(axis=1) - \
        pd.concat([df["l"], prev_c], axis=1).min(axis=1)
    avg = lambda n: bp.rolling(n).sum() / tr.rolling(n).sum()
    uo = 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
    v = uo.iloc[-1]
    if v > 70:
        return -1
    if v < 30:
        return 1
    return 1 if v > 50 else -1


def vote_adx(df, n=14):
    up = df["h"].diff()
    dn = -df["l"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = df["c"].shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev_c).abs(),
                    (df["l"] - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    if plus_di.iloc[-1] > minus_di.iloc[-1]:
        return 1
    return -1


def vote_bullbear(df, n=13):
    e = ema(df["c"], n)
    bull = df["h"].iloc[-1] - e.iloc[-1]
    bear = df["l"].iloc[-1] - e.iloc[-1]
    if bull > 0 and bear > 0:
        return 1
    if bull < 0 and bear < 0:
        return -1
    return 0


def rating(df):
    """Return the summary label for one timeframe."""
    votes = vote_mas(df)
    for fn in [vote_rsi, vote_stoch, vote_macd, vote_cci, vote_willr,
               vote_roc, vote_uo, vote_adx, vote_bullbear]:
        try:
            v = fn(df)
            if not np.isnan(v):
                votes.append(v)
        except Exception:
            pass
    if not votes:
        return "Neutral"
    score = sum(votes) / len(votes)
    if score > 0.6:
        return "Strong Buy"
    if score > 0.25:
        return "Buy"
    if score < -0.6:
        return "Strong Sell"
    if score < -0.25:
        return "Sell"
    return "Neutral"


# ----------------- telegram -----------------

def send(text):
    cid = owner_chat_id()
    if not cid:
        print("no chat id yet — message the bot on Telegram to connect")
        return
    for attempt in range(2):
        r = requests.post(f"{TG_API}/sendMessage",
                          json={"chat_id": cid, "text": text}, timeout=20)
        try:
            body = r.json()
        except Exception:
            body = {}
        if body.get("ok"):
            break
        if body.get("error_code") == 429:  # rate limited — wait and retry
            time.sleep(body.get("parameters", {}).get("retry_after", 3) + 1)
            continue
        print(f"send failed: {r.text[:200]}")
        break
    time.sleep(0.5)  # pace bursts of per-coin alerts


def short(symbol):
    return symbol.replace("USDT", "")


def match_coin(word):
    w = word.upper()
    for c in COINS:
        if c == w or c.startswith(w):
            return c
    return None


def read_commands(state, ratings):
    r = requests.get(f"{TG_API}/getUpdates",
                     params={"offset": state.get("offset", 0) + 1, "timeout": 0},
                     timeout=20)
    for upd in r.json().get("result", []):
        state["offset"] = upd["update_id"]
        msg = upd.get("message") or {}
        chat = msg.get("chat", {})
        # adopt the first private chat that messages the bot as the owner
        if not state.get("chat_id") and chat.get("type") == "private" \
                and chat.get("id"):
            state["chat_id"] = str(chat["id"])
            send("Connected! This chat will now receive all alerts. "
                 "Send /start to see current signals.")
        if str(chat.get("id")) != owner_chat_id():
            continue
        parts = (msg.get("text") or "").strip().lower().split()
        if not parts:
            continue
        cmd = parts[0]
        coin = match_coin(parts[1]) if len(parts) > 1 else None

        if cmd == "/start":
            menu = []
            for c in COINS:
                labels = list(ratings.get(c, {}).values())
                if labels and all(l in BUYISH for l in labels):
                    menu.append([c, "buy"])
                elif labels and all(l in SELLISH for l in labels):
                    menu.append([c, "sell"])
            state["menu"] = menu
            if not menu:
                send("No coins are fully green or fully red right now. "
                     "I'll ping you the moment one flips.")
            else:
                lines = [f"{i + 1}. {short(c)} — "
                         f"{'🟢 ALL BUY' if s == 'buy' else '🔴 ALL SELL'}"
                         for i, (c, s) in enumerate(menu)]
                send("Signals right now:\n" + "\n".join(lines) +
                     "\n\nReply /coin 1,3,5 to track the ones you're taking.")
        elif cmd == "/coin" and len(parts) > 1:
            menu = state.get("menu", [])
            tracked = []
            for tok in "".join(parts[1:]).split(","):
                tok = tok.strip()
                if tok.isdigit() and 0 < int(tok) <= len(menu):
                    c, s = menu[int(tok) - 1]
                    side = "long" if s == "buy" else "short"
                    state["positions"][c] = {"side": side}
                    state["exit_alerted"][c] = False
                    tracked.append(f"{side.upper()} {short(c)}")
            if tracked:
                send("Tracking: " + ", ".join(tracked) +
                     ". I'll warn you if the technicals flip against any of them.")
            else:
                send("Couldn't match those numbers — send /start first, "
                     "then /coin with numbers from that list.")
        elif cmd in ("/long", "/short") and coin:
            state["positions"][coin] = {"side": cmd[1:]}
            state["exit_alerted"][coin] = False
            send(f"Tracking: {cmd[1:].upper()} {short(coin)}. I'll warn you "
                 f"if the technicals turn against it.")
        elif cmd == "/close" and coin:
            state["positions"].pop(coin, None)
            state["exit_alerted"].pop(coin, None)
            send(f"Stopped tracking {short(coin)}.")
        elif cmd == "/status":
            if not state["positions"]:
                send("No open positions tracked. Send /start to see signals.")
            else:
                lines = []
                for c, pos in state["positions"].items():
                    tfs = ratings.get(c, {})
                    row = " | ".join(f"{TF_NAMES[tf]}: {tfs.get(tf, '?')}"
                                     for tf in TIMEFRAMES)
                    lines.append(f"{short(c)} [{pos['side'].upper()}]\n{row}")
                send("\n\n".join(lines))


# ----------------- main -----------------

def valid_symbols(state):
    """Filter COINS to symbols that actually trade on Binance; notify once
    about any that don't (some DEX-only coins have no Binance market)."""
    try:
        r = requests.get("https://data-api.binance.vision/api/v3/ticker/price",
                         timeout=20)
        r.raise_for_status()
        listed = {t["symbol"] for t in r.json()}
    except Exception as e:
        print(f"symbol check failed, using full list: {e}")
        return COINS
    good = [c for c in COINS if c in listed]
    missing = [short(c) for c in COINS if c not in listed]
    if missing and not state.get("missing_notified"):
        send("Heads up: no Binance market for " + ", ".join(missing) +
             " — I can't compute technicals for these, skipping them.")
        state["missing_notified"] = True
    return good


def main():
    # verify the token before doing anything — fail the run loudly if bad
    me = requests.get(f"{TG_API}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("ERROR: TELEGRAM_BOT_TOKEN is invalid — Telegram says:", me)
        print("Fix: repo Settings > Secrets > TELEGRAM_BOT_TOKEN, paste the "
              "token fresh from BotFather.")
        sys.exit(1)
    bot_name = me["result"].get("username", "?")
    print(f"Token OK — bot username: @{bot_name}")

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("offset", 0)
    state.setdefault("all_signal", {})
    state.setdefault("positions", {})
    state.setdefault("exit_alerted", {})
    global _STATE
    _STATE = state

    # answer any commands you sent since the last run right away,
    # using the previous scan's ratings — no need to wait for the
    # new scan or trigger anything manually
    read_commands(state, state.get("ratings", {}))

    send(f"🔄 Scan started — checking {len(COINS)} coins across 8 "
         f"timeframes (~10-15 min).")

    coins = valid_symbols(state)
    ratings = {}
    for coin in coins:
        ratings[coin] = {}
        for tf in TIMEFRAMES:
            try:
                ratings[coin][tf] = rating(get_klines(coin, tf))
            except Exception as e:
                print(f"{coin} {tf} failed: {e}")
                ratings[coin][tf] = "Neutral"
            time.sleep(0.05)  # stay well under Binance rate limits

    # commands first, so /long registers before exit checks
    read_commands(state, ratings)

    greens, reds = [], []
    for coin, tfs in ratings.items():
        labels = list(tfs.values())
        if labels and all(l in BUYISH for l in labels):
            greens.append(short(coin))
        elif labels and all(l in SELLISH for l in labels):
            reds.append(short(coin))

        # position exit alerts
        pos = state["positions"].get(coin)
        if pos:
            against = SELLISH if pos["side"] == "long" else BUYISH
            n_against = sum(1 for l in labels if l in against)
            if n_against >= EXIT_MAJORITY and not state["exit_alerted"].get(coin):
                word = "sell" if pos["side"] == "long" else "buy back"
                send(f"⚠️ EXIT SIGNAL: you're {pos['side'].upper()} {short(coin)} "
                     f"and {n_against}/8 timeframes flipped against you. "
                     f"Time to {word} — consider closing the position.")
                state["exit_alerted"][coin] = True
            elif n_against < EXIT_MAJORITY:
                state["exit_alerted"][coin] = False  # re-arm

    # per-coin alerts, repeated every run while the coin stays fully
    # green/red — the message style you asked to keep
    for name in greens:
        send(f"🟢 ALL BUY: {name}\nEvery timeframe (1m–monthly) shows buy.")
    for name in reds:
        send(f"🔴 ALL SELL: {name}\nEvery timeframe (1m–monthly) shows sell.")
    if greens or reds:
        send(f"📊 Scan done — {len(greens)} all-green, {len(reds)} all-red. "
             f"Send /start to pick and track any of them.")
    else:
        send("📊 Scan done — no coins are fully green or fully red "
             "right now.")

    state["ratings"] = ratings
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


if __name__ == "__main__":
    main()
