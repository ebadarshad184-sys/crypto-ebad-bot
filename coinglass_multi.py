"""
CoinGlass Master Model v3 Scanner (Binance Futures)
====================================================================================
- Top 300 Crypto USDT-M Futures Coins on Binance
- Timeframes: 15m, 30m, 45m (resampled), 1h
- Model v3 Logic: Exact Pine Script Setup & 3-Point Score
- Auto-Scanner Loop (Runs every 60 seconds)
"""

import os
import json
import smtplib
import time
import pandas as pd
import numpy as np
import ccxt

# ==========================================
# 1. CONFIG & PARAMETERS
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",          # USD-M Futures
    "native_timeframes": ["15m", "30m", "1h"],
    "also_build_45m": True,
    "candles_to_fetch": 80,
    "top_n_coins": 300,

    # Strategy v3 Inputs
    "use_htf_filter": False,
    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,

    "min_body_ratio": 0.30,
    "inst_cmf_len": 20,
    "cmf_smooth_len": 5,
    "div_lookback": 10,

    "ema_fast_len": 9,
    "ema_slow_len": 20,

    "vol_ema_length": 20,
    "min_rel_vol": 1.3,
    "whale_rel_vol": 2.0,

    "atr_length": 14,
    "atr_buffer_mult": 0.2,
    "min_stars": 1,                 # Minimum 1 Star to trigger alert
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "pgmq hgoz kkwc dcwg"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_v3_binance.json"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def get_top_coins(ex, cfg):
    markets = ex.load_markets()
    valid = []
    for symbol, market in markets.items():
        if market.get("swap", False) and market.get("active", True) and market.get("settle") == "USDT":
            valid.append(symbol)
    return valid[:cfg["top_n_coins"]]

def fetch_ohlcv_df(ex, symbol, timeframe, limit):
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df

def resample_to_45m(df15):
    df = df15.set_index("timestamp")
    out = df.resample("45min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    return out

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

# ==========================================
# 3. INDICATORS ENGINE (MODEL v3)
# ==========================================
def process_v3_data(df, cfg):
    df = df.copy()

    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    # Relative Volume
    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["min_rel_vol"]
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    # CMF Calculation
    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    # Divergence
    lb = cfg["div_lookback"]
    df["bearish_div"] = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    df["bullish_div"] = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))

    # Candle Filters
    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["min_body_ratio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    # Strict Structure Breakout
    df["break_long"] = df["close"] > df["high"].shift(1)
    df["break_short"] = df["close"] < df["low"].shift(1)

    df["break_both_long"] = df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"]) & (df["close"] > df["ema_slow"]) & df["break_long"]
    df["break_both_short"] = df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"]) & (df["close"] < df["ema_slow"]) & df["break_short"]

    pass_cmf_long = df["is_accum"] if cfg["use_cmf_filter"] else True
    pass_cmf_short = df["is_distrib"] if cfg["use_cmf_filter"] else True
    pass_div_long = ~df["bearish_div"] if cfg["use_div_filter"] else True
    pass_div_short = ~df["bullish_div"] if cfg["use_div_filter"] else True
    vol_break = df["is_whale_vol"] if cfg["require_whale_vol"] else df["is_high_vol"]

    # Setup Conditions
    df["setup_long"] = df["break_both_long"] & df["ema_align_long"] & vol_break & df["is_solid_body"] & pass_cmf_long & pass_div_long
    df["setup_short"] = df["break_both_short"] & df["ema_align_short"] & vol_break & df["is_solid_body"] & pass_cmf_short & pass_div_short

    # Confirmation Logic
    if cfg["use_confirmation"]:
        df["confirm_long"] = df["setup_long"].shift(1).fillna(False) & (df["close"] > df["high"].shift(1)) & df["is_bull"]
        df["confirm_short"] = df["setup_short"].shift(1).fillna(False) & (df["close"] < df["low"].shift(1)) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

    # Scores (0 to 3 Stars)
    df["score_long"] = df["is_accum"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)
    df["score_short"] = df["is_distrib"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)

    return df

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print("  -> Instant Email Sent:", subject)
        return True
    except Exception as e:
        print("  -> Email FAIL:", e)
        return False

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f: return json.load(f)
        except Exception: pass
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f: json.dump(state, f, indent=4)
    except Exception as e: print(f"State save error: {e}")

def run_scanner():
    ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": CONFIG["market_type"]}})
    symbols = get_top_coins(ex, CONFIG)
    state = load_state()

    for symbol in symbols:
        for tf in CONFIG["native_timeframes"]:
            try:
                df = fetch_ohlcv_df(ex, symbol, tf, CONFIG["candles_to_fetch"])
                process_symbol(df, symbol, tf, CONFIG, state)
            except Exception: continue

        if CONFIG["also_build_45m"]:
            try:
                df15 = fetch_ohlcv_df(ex, symbol, "15m", CONFIG["candles_to_fetch"])
                df45 = resample_to_45m(df15)
                process_symbol(df45, symbol, "45m", CONFIG, state)
            except Exception: continue

    save_state(state)

def process_symbol(df, symbol, timeframe, cfg, state):
    df = process_v3_data(df, cfg)
    i = len(df) - 1
    if i < cfg["div_lookback"] + 5: return

    state_key = f"{symbol}_{timeframe}"
    ts_str = str(df["timestamp"].iloc[i])
    if state.get(state_key) == ts_str: return

    ts_pkt = df["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (PKT)"
    usd_vol = df["volume"].iloc[i] * df["close"].iloc[i]
    usd_vol_str = f"${usd_vol/1e6:.2f}M" if usd_vol >= 1e6 else f"${usd_vol/1e3:.1f}K"

    idx_setup = i - 1 if cfg["use_confirmation"] else i
    atr = df["atr"].iloc[i]
    step = df["step"].iloc[i]
    close = df["close"].iloc[i]

    if df["confirm_long"].iloc[i] and df["score_long"].iloc[i] >= cfg["min_stars"]:
        stars = int(df["score_long"].iloc[i])
        star_str = "★" * stars + "☆" * (3 - stars)
        sl = (df["low"].iloc[idx_setup] - step) - (atr * cfg["atr_buffer_mult"])
        tp = close + (4 * step)
        body = f"COINGLASS MODEL V3 - LONG\nSymbol: {symbol}\nTF: {timeframe}\nTime: {ts_pkt}\nEntry: {close:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nScore: {star_str}\nVol: {usd_vol_str}"
        if send_email(f"V3 LONG {symbol} ({timeframe}) {star_str}", body):
            state[state_key] = ts_str

    elif df["confirm_short"].iloc[i] and df["score_short"].iloc[i] >= cfg["min_stars"]:
        stars = int(df["score_short"].iloc[i])
        star_str = "★" * stars + "☆" * (3 - stars)
        sl = (df["high"].iloc[idx_setup] + step) + (atr * cfg["atr_buffer_mult"])
        tp = close - (4 * step)
        body = f"COINGLASS MODEL V3 - SHORT\nSymbol: {symbol}\nTF: {timeframe}\nTime: {ts_pkt}\nEntry: {close:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nScore: {star_str}\nVol: {usd_vol_str}"
        if send_email(f"V3 SHORT {symbol} ({timeframe}) {star_str}", body):
            state[state_key] = ts_str

if __name__ == "__main__":
    print("🚀 Binance Model v3 Scanner Started!")
    while True:
        try:
            print(f"[SCAN v3] Running Binance Futures scan...")
            run_scanner()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(60)
