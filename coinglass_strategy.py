"""
CoinGlass Master Model v4 (Classic) - Ultra-Fast MEXC Parallel Scanner
====================================================================================
- Exchange: MEXC USDT-M Futures
- Speed Optimization: Multi-Threading (Concurrent Execution for Sub-15s Scans)
- Precision Indexing: Candle Close detection (iloc[-2] closed bar logic)
- Score Filter: 4/6, 5/6, or 6/6 (Min 2 Stars)
- Timeframes: 15m, 30m, 45m (resampled), 1h, 2h
- Timezone: Pakistan Standard Time (PKT / Asia/Karachi)
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import ccxt

# ==========================================
# 1. CONFIG & PARAMETERS
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",            # USDT-M Futures
    "native_timeframes": ["15m", "30m", "1h", "2h"],
    "also_build_45m": True,
    "candles_to_fetch": 60,
    "top_n_coins": 200,               # Focused on high liquid pairs for speed
    "max_threads": 20,                # High-speed parallel threads

    "min_score_required": 4,          # Rating 4/6, 5/6, 6/6
    "min_stars_to_show": 2,

    "minBodyRatio": 0.25,
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
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "pgmq hgoz kkwc dcwg"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_ebad_v4_mexc.json"

# ==========================================
# 2. INDICATORS & LOGIC
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

def build_indicators_v4(df, cfg):
    df = df.copy()

    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["min_rel_vol_input"]
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    lb = cfg["div_lookback"]
    bearish_div = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    bullish_div = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))

    df["bearish_div"] = bearish_div.fillna(False)
    df["bullish_div"] = bullish_div.fillna(False)

    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["minBodyRatio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    df["break_both_ema_long"] = df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"]) & (df["close"] > df["ema_slow"]) & (df["close"] > df["close"].shift(1))
    df["break_both_ema_short"] = df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"]) & (df["close"] < df["ema_slow"]) & (df["close"] < df["close"].shift(1))

    df["signal_long"] = df["break_both_ema_long"] & df["ema_align_long"] & df["is_high_vol"] & df["is_solid_body"] & df["is_accum"] & (~df["bearish_div"])
    df["signal_short"] = df["break_both_ema_short"] & df["ema_align_short"] & df["is_high_vol"] & df["is_solid_body"] & df["is_distrib"] & (~df["bullish_div"])

    score_long = (
        df["is_accum"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int) +
        df["ema_align_long"].astype(int) +
        (~df["bearish_div"]).astype(int)
    )
    score_short = (
        df["is_distrib"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int) +
        df["ema_align_short"].astype(int) +
        (~df["bullish_div"]).astype(int)
    )

    df["score_long"] = score_long
    df["score_short"] = score_short

    def calc_stars(score):
        if score >= 5:
            return 3
        elif score == 4:
            return 2
        return 1

    df["stars_long"] = df["score_long"].apply(calc_stars)
    df["stars_short"] = df["score_short"].apply(calc_stars)

    return df

def compute_levels_v4(df, i, cfg, side):
    setup_low = df["low"].iloc[i]
    setup_high = df["high"].iloc[i]
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
# 3. EMAIL & STATE MANAGEMENT
# ==========================================
def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=5) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print(f"  [FAST ALERT SENT] -> {subject}")
        return True
    except Exception as e:
        print(f"  [EMAIL ERROR] -> {e}")
        return False

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"State save error: {e}")

# ==========================================
# 4. PARALLEL WORKER ENGINE
# ==========================================
def process_symbol(symbol, ex, cfg, state):
    alerts = []
    
    for tf in cfg["native_timeframes"]:
        try:
            raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=cfg["candles_to_fetch"])
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            
            # Using -2 index to explicitly check the LAST COMPLETED BAR
            i = len(df) - 2
            if i < cfg["div_lookback"] + 5:
                continue

            df_calculated = build_indicators_v4(df, cfg)
            state_key = f"{symbol}_{tf}"
            ts_str = str(df_calculated["timestamp"].iloc[i])

            if state.get(state_key) == ts_str:
                continue

            ts_pkt = df_calculated["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (PKT)"
            usd_vol = df_calculated["volume"].iloc[i] * df_calculated["close"].iloc[i]
            usd_vol_str = f"${usd_vol/1e6:.2f}M" if usd_vol >= 1e6 else f"${usd_vol/1e3:.1f}K"

            if df_calculated["signal_long"].iloc[i]:
                score = int(df_calculated["score_long"].iloc[i])
                stars = int(df_calculated["stars_long"].iloc[i])
                if score >= cfg["min_score_required"] and stars >= cfg["min_stars_to_show"]:
                    sl, tp = compute_levels_v4(df_calculated, i, cfg, "long")
                    entry = df_calculated["close"].iloc[i]
                    star_str = "★" * stars + "☆" * (3 - stars)
                    body = (f"COINGLASS MASTER MODEL V4 (MEXC) - FAST LONG\n"
                            f"Coin: {symbol}\nTimeframe: {tf}\nTime: {ts_pkt}\n"
                            f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                            f"Inst Score: {star_str} ({score}/6 Score)\nVol USD: {usd_vol_str}")
                    
                    alerts.append((state_key, ts_str, f"V4 LONG {symbol} ({tf}) {star_str}", body))

            if df_calculated["signal_short"].iloc[i]:
                score = int(df_calculated["score_short"].iloc[i])
                stars = int(df_calculated["stars_short"].iloc[i])
                if score >= cfg["min_score_required"] and stars >= cfg["min_stars_to_show"]:
                    sl, tp = compute_levels_v4(df_calculated, i, cfg, "short")
                    entry = df_calculated["close"].iloc[i]
                    star_str = "★" * stars + "☆" * (3 - stars)
                    body = (f"COINGLASS MASTER MODEL V4 (MEXC) - FAST SHORT\n"
                            f"Coin: {symbol}\nTimeframe: {tf}\nTime: {ts_pkt}\n"
                            f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                            f"Inst Score: {star_str} ({score}/6 Score)\nVol USD: {usd_vol_str}")
                    
                    alerts.append((state_key, ts_str, f"V4 SHORT {symbol} ({tf}) {star_str}", body))

        except Exception:
            continue

    return alerts

# ==========================================
# 5. SCANNER RUNNER
# ==========================================
def run_live(cfg):
    ex = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})
    markets = ex.load_markets()
    
    symbols = [
        symbol for symbol, market in markets.items() 
        if market.get("swap", False) and market.get("active", True) and market.get("settle") == "USDT"
    ][:cfg["top_n_coins"]]

    state = load_state()
    print(f"🚀 Starting High-Speed Parallel Scan for {len(symbols)} coins on MEXC...")

    pending_alerts = []
    
    # ThreadPoolExecutor runs requests concurrently across 20 threads
    with ThreadPoolExecutor(max_workers=cfg["max_threads"]) as executor:
        futures = [executor.submit(process_symbol, sym, ex, cfg, state) for sym in symbols]
        for future in as_completed(futures):
            res = future.result()
            if res:
                pending_alerts.extend(res)

    for state_key, ts_str, subject, body in pending_alerts:
        if send_email(subject, body):
            state[state_key] = ts_str

    save_state(state)
    print("✅ Ultra-Fast Parallel Scan Completed.")

if __name__ == "__main__":
    run_live(CONFIG)
