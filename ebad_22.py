"""
CoinGlass Master Model v3 - Fast Multi-Threaded Edition (Batch 2: Coins 51-100)
=====================================================================
- Next 50 Active Crypto Pairs (MEXC USDT-M Perpetual Swaps)
- Timeframes: 15m, 30m, 45m (Resampled), 1h, 2h
- 10 Parallel Threads Scanning Engine (~3-5 Seconds Execution)
- Fresh Candle Verification & Instant Email Notification
"""

import sys
import json
import os
import smtplib
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import ccxt

# ==========================================
# 1. CONFIG
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",
    "native_timeframes": ["15m", "30m", "1h", "2h"],
    "also_build_45m": True,
    "candles_to_fetch": 120,
    "max_threads": 10,

    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,

    "min_body_ratio": 0.25,
    "inst_cmf_len": 20,
    "cmf_smooth_len": 5,
    "div_lookback": 10,

    "ema_fast_len": 9,
    "ema_slow_len": 20,

    "vol_ema_length": 20,
    "min_rel_vol": 1.1,
    "whale_rel_vol": 2.0,

    "atr_length": 14,
    "atr_buffer_mult": 0.2,

    # Next 50 Crypto Perpetual Pairs (51 to 100)
    "fixed_coin_list": [
        "1000SATS/USDT:USDT", "AXS/USDT:USDT", "SAND/USDT:USDT", "MANA/USDT:USDT", "CHZ/USDT:USDT", 
        "KAS/USDT:USDT", "NOT/USDT:USDT", "BRETT/USDT:USDT", "POPCAT/USDT:USDT", "WLD/USDT:USDT", 
        "BEAM/USDT:USDT", "NEO/USDT:USDT", "XTZ/USDT:USDT", "KAVA/USDT:USDT", "MINA/USDT:USDT", 
        "ASTR/USDT:USDT", "MANTA/USDT:USDT", "STRK/USDT:USDT", "BLUR/USDT:USDT", "ZEC/USDT:USDT", 
        "DASH/USDT:USDT", "XMR/USDT:USDT", "IOTA/USDT:USDT", "KLAY/USDT:USDT", "COMP/USDT:USDT", 
        "SNX/USDT:USDT", "CRV/USDT:USDT", "LDO/USDT:USDT", "CVX/USDT:USDT", "FXS/USDT:USDT", 
        "RPL/USDT:USDT", "PENDLE/USDT:USDT", "MAV/USDT:USDT", "RDNT/USDT:USDT", "EDU/USDT:USDT", 
        "ID/USDT:USDT", "HOOK/USDT:USDT", "ARKM/USDT:USDT", "CYBER/USDT:USDT", "MAGIC/USDT:USDT", 
        "GMX/USDT:USDT", "SSV/USDT:USDT", "AGLD/USDT:USDT", "TRB/USDT:USDT", "GAS/USDT:USDT", 
        "LOOM/USDT:USDT", "BIGTIME/USDT:USDT", "TOKEN/USDT:USDT", "MEME/USDT:USDT", "MYRO/USDT:USDT"
    ],
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_v3_batch2.json"

# ==========================================
# 2. INDICATORS & RESAMPLING
# ==========================================
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

def build_indicators_v3(df, cfg):
    df = df.copy()

    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["min_rel_vol"]
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    vol_break = df["is_whale_vol"] if cfg["require_whale_vol"] else df["is_high_vol"]

    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    if cfg["use_cmf_filter"]:
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

    df["break_both_ema_long"] = (
        df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"])
        & (df["close"] > df["ema_slow"]) & (df["close"] > prev_high)
    )
    df["break_both_ema_short"] = (
        df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"])
        & (df["close"] < df["ema_slow"]) & (df["close"] < prev_low)
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
        df["confirm_long"] = prev_setup_long & (df["close"] > prev_high) & df["is_bull"]
        df["confirm_short"] = prev_setup_short & (df["close"] < prev_low) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

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
        print("  -> Email bhej diya:", subject)
        return True
    except Exception as e:
        print("  -> Email FAIL:", e)
        return False

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save fail: {e}")

# ==========================================
# 4. PARALLEL PROCESSOR
# ==========================================
def process_symbol_v3(symbol, ex, cfg, state, now_utc):
    alerts = []

    for tf in cfg["native_timeframes"]:
        try:
            raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=cfg["candles_to_fetch"])
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

            i = len(df) - 2  # Closed candle
            if i < cfg["div_lookback"] + 5:
                continue

            candle_time = df["timestamp"].iloc[i]
            state_key = f"{symbol}_{tf}"
            ts_str = str(candle_time)

            age_minutes = (now_utc - candle_time).total_seconds() / 60
            tf_minutes = {"15m": 15, "30m": 30, "45m": 45, "1h": 60, "2h": 120}.get(tf, 60)
            if age_minutes > tf_minutes * 2.5:
                state[state_key] = ts_str
                continue

            if state.get(state_key) == ts_str:
                continue

            df_calc = build_indicators_v3(df, cfg)
            ts_pkt = candle_time.tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"

            if df_calc["confirm_long"].iloc[i]:
                sl, tp = compute_levels(df_calc, i, cfg, "long")
                entry = df_calc["close"].iloc[i]
                body = (f"Coin: {symbol}\nTimeframe: {tf}\nVersion: v3 Model (Batch 2)\nTime: {ts_pkt}\n"
                        f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}")
                alerts.append((state_key, ts_str, f"LONG {symbol} ({tf}) - v3 Signal", body))

            if df_calc["confirm_short"].iloc[i]:
                sl, tp = compute_levels(df_calc, i, cfg, "short")
                entry = df_calc["close"].iloc[i]
                body = (f"Coin: {symbol}\nTimeframe: {tf}\nVersion: v3 Model (Batch 2)\nTime: {ts_pkt}\n"
                        f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}")
                alerts.append((state_key, ts_str, f"SHORT {symbol} ({tf}) - v3 Signal", body))

        except Exception:
            continue

    if cfg["also_build_45m"]:
        try:
            raw15 = ex.fetch_ohlcv(symbol, timeframe="15m", limit=cfg["candles_to_fetch"])
            df15 = pd.DataFrame(raw15, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df15["timestamp"] = pd.to_datetime(df15["timestamp"], unit="ms", utc=True)
            df45 = resample_to_45m(df15)

            i = len(df45) - 2
            if i >= cfg["div_lookback"] + 5:
                candle_time = df45["timestamp"].iloc[i]
                state_key = f"{symbol}_45m"
                ts_str = str(candle_time)

                age_minutes = (now_utc - candle_time).total_seconds() / 60
                if age_minutes <= 45 * 2.5 and state.get(state_key) != ts_str:
                    df45_calc = build_indicators_v3(df45, cfg)
                    ts_pkt = candle_time.tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"

                    if df45_calc["confirm_long"].iloc[i]:
                        sl, tp = compute_levels(df45_calc, i, cfg, "long")
                        entry = df45_calc["close"].iloc[i]
                        body = (f"Coin: {symbol}\nTimeframe: 45m\nVersion: v3 Model (Batch 2)\nTime: {ts_pkt}\n"
                                f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}")
                        alerts.append((state_key, ts_str, f"LONG {symbol} (45m) - v3 Signal", body))

                    if df45_calc["confirm_short"].iloc[i]:
                        sl, tp = compute_levels(df45_calc, i, cfg, "short")
                        entry = df45_calc["close"].iloc[i]
                        body = (f"Coin: {symbol}\nTimeframe: 45m\nVersion: v3 Model (Batch 2)\nTime: {ts_pkt}\n"
                                f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}")
                        alerts.append((state_key, ts_str, f"SHORT {symbol} (45m) - v3 Signal", body))
        except Exception:
            pass

    return alerts

# ==========================================
# 5. EXECUTION ENGINE
# ==========================================
def run_live(cfg):
    now_utc = datetime.now(timezone.utc)

    ex = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})
    markets = ex.load_markets()

    valid_symbols = [sym for sym in cfg["fixed_coin_list"] if sym in markets and markets[sym].get("active", True)]
    state = load_state()

    print(f"🚀 Scanning Coins 51-100 ({len(valid_symbols)} Active Pairs) - v3 Model...")

    pending_alerts = []
    with ThreadPoolExecutor(max_workers=cfg["max_threads"]) as executor:
        futures = [executor.submit(process_symbol_v3, sym, ex, cfg, state, now_utc) for sym in valid_symbols]
        for future in as_completed(futures):
            res = future.result()
            if res:
                pending_alerts.extend(res)

    for state_key, ts_str, subject, body in pending_alerts:
        if send_email(subject, body):
            state[state_key] = ts_str

    save_state(state)
    print("✅ v3 Batch 2 Scan Completed.")

if __name__ == "__main__":
    run_live(CONFIG)
