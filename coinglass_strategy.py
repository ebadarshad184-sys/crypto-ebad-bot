"""
CoinGlass Master Model v3 - Top 50 USDT-M Perpetual Crypto Coins
===================================================================
- Top 50 Popular Crypto USDT-M Futures Pairs
- Timeframes: 15m, 30m, 45m (resampled), 1h, 2h
- Multi-Threaded Parallel Scanning for Instant Execution
- Strictly Closed Candle Detection (iloc[-2])
- Pakistan Standard Time (PKT)
"""

import sys
import json
import os
import smtplib
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import ccxt

# ==========================================
# 1. CONFIG & TOP 50 COINS
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",           # Perpetual futures (USDT-M)
    "native_timeframes": ["15m", "30m", "1h", "2h"],
    "also_build_45m": True,
    "candles_to_fetch": 300,
    "max_threads": 10,               # Fast parallel scanning threads

    # Strictly Top 50 Popular USDT-M Crypto Coins (No Stocks / Foreign Markets)
    "fixed_coin_list": [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT",
        "XRP/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT",
        "TRX/USDT:USDT", "LINK/USDT:USDT", "DOT/USDT:USDT", "MATIC/USDT:USDT",
        "LTC/USDT:USDT", "SHIB/USDT:USDT", "BCH/USDT:USDT", "UNI/USDT:USDT",
        "ATOM/USDT:USDT", "ETC/USDT:USDT", "NEAR/USDT:USDT", "APT/USDT:USDT",
        "FIL/USDT:USDT", "ARB/USDT:USDT", "OP/USDT:USDT", "SUI/USDT:USDT",
        "INJ/USDT:USDT", "TON/USDT:USDT", "SAND/USDT:USDT", "AAVE/USDT:USDT",
        "XLM/USDT:USDT", "ALGO/USDT:USDT", "PEPE/USDT:USDT", "FET/USDT:USDT",
        "RNDR/USDT:USDT", "TIA/USDT:USDT", "SEI/USDT:USDT", "STX/USDT:USDT",
        "GALA/USDT:USDT", "ICP/USDT:USDT", "LDO/USDT:USDT", "IMX/USDT:USDT",
        "WIF/USDT:USDT", "FLOKI/USDT:USDT", "BONK/USDT:USDT", "JUP/USDT:USDT",
        "PENDLE/USDT:USDT", "PYTH/USDT:USDT", "ENA/USDT:USDT", "WLD/USDT:USDT",
        "STRK/USDT:USDT", "ORDI/USDT:USDT"
    ],

    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,

    "min_body_ratio": 0.20,
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

    "min_star_score": 2,   # 2-Star (2/3) aur 3-Star (3/3) signals pe alert
}

# Email Setup
GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state.json"

# ==========================================
# 2. DATA RESAMPLING & INDICATORS
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

def build_indicators(df, cfg):
    df = df.copy()
    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["min_rel_vol"]
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
    df["pass_div_long"] = ~bearish_div if cfg["use_div_filter"] else True
    df["pass_div_short"] = ~bullish_div if cfg["use_div_filter"] else True

    df["pass_cmf_long"] = df["is_accum"] if cfg["use_cmf_filter"] else True
    df["pass_cmf_short"] = df["is_distrib"] if cfg["use_cmf_filter"] else True

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

    vol_break = df["is_high_vol"] & (df["is_whale_vol"] if cfg["require_whale_vol"] else True)

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

    df["inst_score_long"] = df["is_accum"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)
    df["inst_score_short"] = df["is_distrib"].astype(int) + df["is_high_vol"].astype(int) + df["is_whale_vol"].astype(int)

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
# 4. PARALLEL SYMBOL PROCESSOR
# ==========================================
def process_symbol_v3(symbol, ex, cfg, state):
    alerts = []
    
    # Process native timeframes (15m, 30m, 1h, 2h)
    for tf in cfg["native_timeframes"]:
        try:
            raw = ex.fetch_ohlcv(symbol, timeframe=tf, limit=cfg["candles_to_fetch"])
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

            i = len(df) - 2  # Strict closed candle
            if i < cfg["div_lookback"] + 5:
                continue

            df_calc = build_indicators(df, cfg)
            state_key = f"{symbol}_{tf}"
            ts_str = str(df_calc["timestamp"].iloc[i])

            if state.get(state_key) == ts_str:
                continue

            ts_pkt = df_calc["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"

            if df_calc["confirm_long"].iloc[i]:
                score = int(df_calc["inst_score_long"].iloc[i])
                if score >= cfg["min_star_score"]:
                    sl, tp = compute_levels(df_calc, i, cfg, "long")
                    entry = df_calc["close"].iloc[i]
                    stars = "*" * score + "-" * (3 - score)
                    body = (f"Coin: {symbol}\nTimeframe: {tf}\nTime: {ts_pkt}\n"
                            f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars} ({score}/3)")
                    alerts.append((state_key, ts_str, f"LONG {symbol} ({tf}) {stars}", body))

            if df_calc["confirm_short"].iloc[i]:
                score = int(df_calc["inst_score_short"].iloc[i])
                if score >= cfg["min_star_score"]:
                    sl, tp = compute_levels(df_calc, i, cfg, "short")
                    entry = df_calc["close"].iloc[i]
                    stars = "*" * score + "-" * (3 - score)
                    body = (f"Coin: {symbol}\nTimeframe: {tf}\nTime: {ts_pkt}\n"
                            f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars} ({score}/3)")
                    alerts.append((state_key, ts_str, f"SHORT {symbol} ({tf}) {stars}", body))

        except Exception:
            continue

    # Process resampled 45m timeframe
    if cfg["also_build_45m"]:
        try:
            raw15 = ex.fetch_ohlcv(symbol, timeframe="15m", limit=cfg["candles_to_fetch"])
            df15 = pd.DataFrame(raw15, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df15["timestamp"] = pd.to_datetime(df15["timestamp"], unit="ms", utc=True)
            df45 = resample_to_45m(df15)

            i = len(df45) - 2
            if i >= cfg["div_lookback"] + 5:
                df45_calc = build_indicators(df45, cfg)
                state_key = f"{symbol}_45m"
                ts_str = str(df45_calc["timestamp"].iloc[i])

                if state.get(state_key) != ts_str:
                    ts_pkt = df45_calc["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"

                    if df45_calc["confirm_long"].iloc[i]:
                        score = int(df45_calc["inst_score_long"].iloc[i])
                        if score >= cfg["min_star_score"]:
                            sl, tp = compute_levels(df45_calc, i, cfg, "long")
                            entry = df45_calc["close"].iloc[i]
                            stars = "*" * score + "-" * (3 - score)
                            body = (f"Coin: {symbol}\nTimeframe: 45m\nTime: {ts_pkt}\n"
                                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars} ({score}/3)")
                            alerts.append((state_key, ts_str, f"LONG {symbol} (45m) {stars}", body))

                    if df45_calc["confirm_short"].iloc[i]:
                        score = int(df45_calc["inst_score_short"].iloc[i])
                        if score >= cfg["min_star_score"]:
                            sl, tp = compute_levels(df45_calc, i, cfg, "short")
                            entry = df45_calc["close"].iloc[i]
                            stars = "*" * score + "-" * (3 - score)
                            body = (f"Coin: {symbol}\nTimeframe: 45m\nTime: {ts_pkt}\n"
                                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars} ({score}/3)")
                            alerts.append((state_key, ts_str, f"SHORT {symbol} (45m) {stars}", body))
        except Exception:
            pass

    return alerts

# ==========================================
# 5. LIVE EXECUTION ENGINE
# ==========================================
def run_live(cfg):
    ex = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})
    markets = ex.load_markets()

    valid_symbols = [sym for sym in cfg["fixed_coin_list"] if sym in markets and markets[sym].get("active", True)]
    state = load_state()

    print(f"🚀 Scanning Top {len(valid_symbols)} Crypto Coins across 15m, 30m, 45m, 1h, 2h...")

    pending_alerts = []
    with ThreadPoolExecutor(max_workers=cfg["max_threads"]) as executor:
        futures = [executor.submit(process_symbol_v3, sym, ex, cfg, state) for sym in valid_symbols]
        for future in as_completed(futures):
            res = future.result()
            if res:
                pending_alerts.extend(res)

    for state_key, ts_str, subject, body in pending_alerts:
        if send_email(subject, body):
            state[state_key] = ts_str

    save_state(state)
    print("✅ Fast Scan Completed.")

if __name__ == "__main__":
    run_live(CONFIG)
