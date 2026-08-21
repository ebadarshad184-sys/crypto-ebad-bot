"""
CoinGlass Master Model v5 - Weighted Institutional Score (Python)
=====================================================================
Pine v5 (Weighted Institutional Score, mode-based) ka Python conversion.
MEXC perpetual swap, top ~300 coins (dynamic, no stablecoins/leveraged tokens),
Timeframes: 15m, 30m, 45m, 1h, 2h.
Sirf NAYE confirmed signals pe email (purani/beeti hui candle kabhi nahi).

SACH KEHTA HOON: 300 coins x 5 timeframes = ~1200 API calls per run.
Isme khud 5-10 minute lag sakte hain. "1-2 min ke andar mail" is scale
pe possible nahi - agar chahiye to coin count kam karo (CONFIG mein
"max_coins" badal do, jaise 300 se 60).

Setup:
  pip install ccxt pandas numpy --break-system-packages
  GMAIL_ADDRESS / GMAIL_APP_PASSWORD / TO_EMAIL neeche fill karo.

Command: python3 coinglass_v5.py live
"""

import sys
import json
import os
import smtplib
from email.mime.text import MIMEText
import pandas as pd
import numpy as np
import ccxt

# ==========================================
# 1. CONFIG
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",
    "max_coins": 300,                 # jitne coins chahiye (dynamic, volume ke hisaab se)
    "native_timeframes": ["15m", "30m", "1h", "2h"],
    "also_build_45m": True,           # 45m 15m data se resample hoga
    "candles_to_fetch": 120,          # kam rakha hai speed ke liye (300 coins ke liye zaroori)

    "signal_mode": "Balanced",        # "Conservative" / "Balanced" / "More Signals"
    "min_stars_to_show": 2,           # 1,2,3 - jitna zyada utna strict/kam signals

    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,
    # HTF filter Python version mein OFF hai (extra API calls bacha ne ke liye,
    # 300 coins ke sath already bohot calls ho rahi hain)

    "min_body_ratio": 0.25,
    "inst_cmf_len": 20,
    "cmf_smooth_len": 5,
    "div_lookback": 10,

    "ema_fast_len": 9,
    "ema_slow_len": 20,

    "vol_ema_length": 20,
    "min_rel_vol_input": 1.1,
    "whale_rel_vol": 2.0,

    "atr_length": 14,
    "atr_buffer_mult": 0.2,

    # Yeh symbols/patterns exclude honge (leveraged tokens, stablecoin pairs)
    "exclude_patterns": ["3L", "3S", "5L", "5S", "BULL", "BEAR", "UP/", "DOWN/"],
    "exclude_bases": ["USDC", "DAI", "TUSD", "BUSD", "FDUSD", "USDT"],
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "pgmq hgoz kkwc dcwg"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_v5.json"

# ==========================================
# 2. DYNAMIC TOP N COINS (no stablecoins, no leveraged tokens)
# ==========================================
def get_top_coins(ex, cfg):
    markets = ex.load_markets()
    candidates = []
    for sym, m in markets.items():
        if not m.get("swap"):
            continue
        if m.get("quote") != "USDT":
            continue
        if not m.get("active", True):
            continue
        base = m.get("base", "")
        if base in cfg["exclude_bases"]:
            continue
        if any(pat in sym for pat in cfg["exclude_patterns"]):
            continue
        candidates.append(sym)

    tickers = ex.fetch_tickers(candidates)
    ranked = sorted(
        tickers.items(),
        key=lambda kv: (kv[1].get("quoteVolume") or 0),
        reverse=True,
    )
    top = [sym for sym, _ in ranked[: cfg["max_coins"]]]
    return top

# ==========================================
# 3. DATA FETCH
# ==========================================
def fetch_ohlcv_df(ex, symbol, timeframe, limit):
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def resample_to_45m(df15):
    df = df15.set_index("timestamp")
    out = df.resample("45min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    return out

# ==========================================
# 4. INDICATORS (Pine v5 mode logic)
# ==========================================
def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def sma(series, length):
    return series.rolling(length).mean()

def wilder_atr(df, length):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()

def build_indicators(df, cfg):
    df = df.copy()

    mode = cfg["signal_mode"]
    mode_min_rel_vol = cfg["min_rel_vol_input"]
    if mode == "Conservative":
        mode_min_rel_vol = max(cfg["min_rel_vol_input"], 1.30)
    elif mode == "Balanced":
        mode_min_rel_vol = max(cfg["min_rel_vol_input"], 1.10)
    elif mode == "More Signals":
        mode_min_rel_vol = max(cfg["min_rel_vol_input"], 1.00)

    strict_breakout = (mode == "Conservative")
    strict_confirmation = (mode == "Conservative")
    flexible_flow = (mode != "Conservative")

    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= mode_min_rel_vol
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    vol_break = df["is_whale_vol"] if cfg["require_whale_vol"] else df["is_high_vol"]

    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    if cfg["use_cmf_filter"]:
        if flexible_flow:
            df["pass_cmf_long"] = df["is_accum"] | df["is_high_vol"]
            df["pass_cmf_short"] = df["is_distrib"] | df["is_high_vol"]
        else:
            df["pass_cmf_long"] = df["is_accum"]
            df["pass_cmf_short"] = df["is_distrib"]
    else:
        df["pass_cmf_long"] = True
        df["pass_cmf_short"] = True

    lb = cfg["div_lookback"]
    df["bearish_div"] = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    df["bullish_div"] = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))
    if cfg["use_div_filter"]:
        df["pass_div_long"] = ~df["bearish_div"]
        df["pass_div_short"] = ~df["bullish_div"]
    else:
        df["pass_div_long"] = True
        df["pass_div_short"] = True

    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["min_body_ratio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)

    if strict_breakout:
        break_long_cond = df["close"] > prev_high
        break_short_cond = df["close"] < prev_low
    else:
        break_long_cond = df["close"] > prev_close
        break_short_cond = df["close"] < prev_close

    df["break_both_ema_long"] = (
        df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"])
        & (df["close"] > df["ema_slow"]) & break_long_cond
    )
    df["break_both_ema_short"] = (
        df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"])
        & (df["close"] < df["ema_slow"]) & break_short_cond
    )

    df["setup_long"] = (
        df["break_both_ema_long"] & df["ema_align_long"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_long"] & df["pass_div_long"]
    )
    df["setup_short"] = (
        df["break_both_ema_short"] & df["ema_align_short"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_short"] & df["pass_div_short"]
    )

    if cfg["use_confirmation"]:
        prev_setup_long = df["setup_long"].shift(1).fillna(False)
        prev_setup_short = df["setup_short"].shift(1).fillna(False)
        if strict_confirmation:
            df["confirm_long"] = prev_setup_long & (df["close"] > prev_high) & df["is_bull"]
            df["confirm_short"] = prev_setup_short & (df["close"] < prev_low) & df["is_bear"]
        else:
            df["confirm_long"] = prev_setup_long & (df["close"] > prev_close) & df["is_bull"]
            df["confirm_short"] = prev_setup_short & (df["close"] < prev_close) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

    # Weighted score 0-6
    score_long = (
        df["is_accum"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)
        + df["ema_align_long"].astype(int) + (~df["bearish_div"]).astype(int)
    )
    score_short = (
        df["is_distrib"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)
        + df["ema_align_short"].astype(int) + (~df["bullish_div"]).astype(int)
    )
    df["score_long"] = score_long
    df["score_short"] = score_short

    def star_rating(score):
        if score >= 5:
            return 3
        elif score >= 3:
            return 2
        else:
            return 1

    df["stars_long"] = df["score_long"].apply(star_rating)
    df["stars_short"] = df["score_short"].apply(star_rating)

    return df

def compute_levels(df, i, cfg, side):
    use_conf = cfg["use_confirmation"]
    idx_setup = i - 1 if use_conf else i
    setup_low = df["low"].iloc[idx_setup]
    setup_high = df["high"].iloc[idx_setup]
    step = df["step"].iloc[i]
    close = df["close"].iloc[i]
    atr = df["atr"].iloc[i]

    if side == "long":
        bottom_buy = setup_low - step
        sl = bottom_buy - (atr * cfg["atr_buffer_mult"])
        tp = close + (4 * step)
    else:
        top_sell = setup_high + step
        sl = top_sell + (atr * cfg["atr_buffer_mult"])
        tp = close - (4 * step)
    return sl, tp

# ==========================================
# 5. EMAIL
# ==========================================
def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print("  -> Email bhej diya:", subject)
        return True
    except Exception as e:
        print("  -> Email FAIL:", e)
        return False

# ==========================================
# 6. STATE
# ==========================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ==========================================
# 7. CHECK ONE SYMBOL+TIMEFRAME (sirf NAYI candle pe alert)
# ==========================================
def check_one(df, symbol, timeframe, cfg, state, state_key, now_utc):
    df = build_indicators(df, cfg)
    i = len(df) - 2   # last CLOSED candle
    if i < cfg["div_lookback"] + 5:
        return

    candle_time = df["timestamp"].iloc[i]
    ts = str(candle_time)

    # Purani/beeti hui candle skip karo - sirf woh candle jo pichle ~2 candle-lengths
    # ke andar close hui ho, "naya" mana jayega. (Purana data se retroactive alert nahi.)
    age_minutes = (now_utc - candle_time).total_seconds() / 60
    tf_minutes = {"15m": 15, "30m": 30, "45m": 45, "1h": 60, "2h": 120}.get(timeframe, 60)
    if age_minutes > tf_minutes * 2.5:
        state[state_key] = ts  # mark done taake dobara check na ho, lekin alert nahi
        return

    if state.get(state_key) == ts:
        return

    ts_pkt = (candle_time + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"
    all_sent_ok = True

    if df["confirm_long"].iloc[i]:
        stars = int(df["stars_long"].iloc[i])
        if stars >= cfg["min_stars_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            star_str = "*" * stars + "-" * (3 - stars)
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {star_str} ({int(df['score_long'].iloc[i])}/5)")
            ok = send_email(f"LONG {symbol} ({timeframe}) {star_str}", body)
            all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i]:
        stars = int(df["stars_short"].iloc[i])
        if stars >= cfg["min_stars_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            star_str = "*" * stars + "-" * (3 - stars)
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {star_str} ({int(df['score_short'].iloc[i])}/5)")
            ok = send_email(f"SHORT {symbol} ({timeframe}) {star_str}", body)
            all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        state[state_key] = ts

# ==========================================
# 8. LIVE MODE
# ==========================================
def run_live(cfg):
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    print("Top coins fetch kar raha hoon (MEXC swap, no stable/leveraged)...")
    symbols = get_top_coins(ex, cfg)
    print(f"{len(symbols)} coins mile. Timeframes: 15m,30m,45m,1h,2h. Mode: {cfg['signal_mode']}")

    state = load_state()

    for idx, symbol in enumerate(symbols):
        try:
            df15 = fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])
        except Exception as e:
            print(f"{symbol}: 15m fetch fail -> {e}")
            continue

        for tf in cfg["native_timeframes"]:
            try:
                df = df15 if tf == "15m" else fetch_ohlcv_df(ex, symbol, tf, cfg["candles_to_fetch"])
            except Exception as e:
                print(f"{symbol} {tf}: fetch fail -> {e}")
                continue
            check_one(df, symbol, tf, cfg, state, f"{symbol}_{tf}", now_utc)

        if cfg["also_build_45m"]:
            df45 = resample_to_45m(df15)
            check_one(df45, symbol, "45m", cfg, state, f"{symbol}_45m", now_utc)

        if (idx + 1) % 50 == 0:
            print(f"  ...{idx + 1}/{len(symbols)} coins check ho chuke")
            save_state(state)  # periodically save, taake beech mein rukne pe progress na khoye

    save_state(state)
    print("Sab coins check ho gaye.")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    if mode == "live":
        run_live(CONFIG)
    else:
        print("Usage: python3 coinglass_v5.py live")
