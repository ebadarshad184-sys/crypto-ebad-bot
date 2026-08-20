"""
CoinGlass Master Model v3 - Python Multi-Coin Scanner
====================================================================================
- Top 300 Crypto USDT-M Futures Coins
- Timeframes: 15m, 30m, 45m (resampled), 1h
- Minimum 2-Star Quality Filter (Score >= 2 out of 3)
- Real-time instant live candle alerts (Index: len(df) - 1)
- Pakistan Standard Time (PKT)
- Separate State File: alert_state_ebad_v3.json
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
# 1. CONFIG & PARAMETERS
# ==========================================
CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",           # Perpetual Futures
    "native_timeframes": ["15m", "30m", "1h"],
    "also_build_45m": True,
    "candles_to_fetch": 200,
    "top_n_coins": 300,              # Top 300 Crypto Coins

    "use_htf_filter": False,         # HTF Filter
    "htf_timeframe": "1h",
    "use_cmf_filter": True,          # Institutional CMF Filter
    "use_div_filter": True,          # Institutional Divergence Filter
    "require_whale_vol": False,      # Require Whale Volume?
    "use_confirmation": True,        # Confirmation Candle Logic

    "minBodyRatio": 0.30,
    "inst_cmf_len": 20,
    "cmf_smooth_len": 5,
    "div_lookback": 10,

    "ema_fast_len": 9,
    "ema_slow_len": 20,

    "vol_ema_length": 20,
    "min_rel_vol": 1.3,              # High Volume Threshold
    "whale_rel_vol": 2.0,            # Whale Volume Threshold

    "atr_length": 14,
    "atr_buffer_mult": 0.2,

    "min_stars_to_show": 2,          # Minimum 2 Stars required for Email Alert
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "pgmq hgoz kkwc dcwg"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_ebad_v3.json"

# ==========================================
# 2. HELPER FUNCTIONS & DATA FETCH
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

# ==========================================
# 3. TECHNICAL INDICATORS & LOGIC
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

def build_indicators_v3(df, cfg):
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

    df["pass_cmf_long"] = df["is_accum"] if cfg["use_cmf_filter"] else True
    df["pass_cmf_short"] = df["is_distrib"] if cfg["use_cmf_filter"] else True

    # Institutional Divergence Filter
    lb = cfg["div_lookback"]
    bearish_div = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    bullish_div = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))

    df["pass_div_long"] = ~bearish_div if cfg["use_div_filter"] else True
    df["pass_div_short"] = ~bullish_div if cfg["use_div_filter"] else True

    # Candle Solid Body Filter
    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["minBodyRatio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    df["break_both_ema_long"] = (
        df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"])
        & (df["close"] > df["ema_slow"]) & (df["close"] > df["high"].shift(1))
    )
    df["break_both_ema_short"] = (
        df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"])
        & (df["close"] < df["ema_slow"]) & (df["close"] < df["low"].shift(1))
    )

    vol_break = df["is_whale_vol"] if cfg["require_whale_vol"] else df["is_high_vol"]

    df["setup_long"] = (
        df["break_both_ema_long"] & df["ema_align_long"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_long"] & df["pass_div_long"]
    )
    df["setup_short"] = (
        df["break_both_ema_short"] & df["ema_align_short"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_short"] & df["pass_div_short"]
    )

    # Confirmation Logic
    prev_setup_long = df["setup_long"].shift(1).fillna(False)
    prev_setup_short = df["setup_short"].shift(1).fillna(False)

    if cfg["use_confirmation"]:
        df["confirm_long"] = prev_setup_long & (df["close"] > df["high"].shift(1)) & df["is_bull"]
        df["confirm_short"] = prev_setup_short & (df["close"] < df["low"].shift(1)) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

    # Institutional Score (Max 3)
    df["score_long"] = (
        df["is_accum"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int)
    )

    df["score_short"] = (
        df["is_distrib"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int)
    )

    def calc_stars(score):
        if score == 3:
            return 3
        elif score == 2:
            return 2
        elif score == 1:
            return 1
        return 0

    df["stars_long"] = df["score_long"].apply(calc_stars)
    df["stars_short"] = df["score_short"].apply(calc_stars)

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
# 4. EMAIL & STATE MANAGEMENT
# ==========================================
def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print("  -> Email sent:", subject)
        return True
    except Exception as e:
        print("  -> Email FAIL:", e)
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
# 5. EXECUTION & CHECK
# ==========================================
def check_one(df, symbol, timeframe, cfg, state, state_key):
    df = build_indicators_v3(df, cfg)
    
    # Real-Time Fix: Latest closed candle (Index len - 1)
    i = len(df) - 1
    if i < cfg["div_lookback"] + 5:
        return

    ts_str = str(df["timestamp"].iloc[i])
    if state.get(state_key) == ts_str:
        return

    # Exact PKT Conversion
    ts_pkt = df["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (PKT)"
    usd_vol = df["volume"].iloc[i] * df["close"].iloc[i]
    usd_vol_str = f"${usd_vol/1e6:.2f}M" if usd_vol >= 1e6 else f"${usd_vol/1e3:.1f}K"

    all_sent_ok = True

    if df["confirm_long"].iloc[i]:
        stars = int(df["stars_long"].iloc[i])
        if stars >= cfg["min_stars_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            star_str = "★" * stars + "☆" * (3 - stars)
            body = (f"COINGLASS MASTER MODEL V3 - LONG SIGNAL\n"
                    f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                    f"Inst Score: {star_str} ({stars}/3 Stars)\nVol USD: {usd_vol_str}")
            print(f"V3 LONG {symbol} {timeframe} Stars={star_str}")
            ok = send_email(f"V3 LONG {symbol} ({timeframe}) {star_str}", body)
            all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i]:
        stars = int(df["stars_short"].iloc[i])
        if stars >= cfg["min_stars_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            star_str = "★" * stars + "☆" * (3 - stars)
            body = (f"COINGLASS MASTER MODEL V3 - SHORT SIGNAL\n"
                    f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                    f"Inst Score: {star_str} ({stars}/3 Stars)\nVol USD: {usd_vol_str}")
            print(f"V3 SHORT {symbol} {timeframe} Stars={star_str}")
            ok = send_email(f"V3 SHORT {symbol} ({timeframe}) {star_str}", body)
            all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        state[state_key] = ts_str

def run_live(cfg):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    print("Fetching top 300 coins for CoinGlass Master Model v3...")
    symbols = get_top_coins(ex, cfg)
    print(f"Checking {len(symbols)} coins across 15m, 30m, 45m, 1h timeframes...")

    state = load_state()

    for symbol in symbols:
        for tf in cfg["native_timeframes"]:
            try:
                df = fetch_ohlcv_df(ex, symbol, tf, cfg["candles_to_fetch"])
                check_one(df, symbol, tf, cfg, state, f"{symbol}_{tf}")
            except Exception as e:
                print(f"{symbol} {tf}: fetch fail -> {e}")
                continue

        if cfg["also_build_45m"]:
            try:
                df15 = fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])
                df45 = resample_to_45m(df15)
                check_one(df45, symbol, "45m", cfg, state, f"{symbol}_45m")
            except Exception as e:
                print(f"{symbol} 45m: fetch fail -> {e}")

    save_state(state)
    print("Scan for CoinGlass Master Model v3 completed successfully.")

if __name__ == "__main__":
    run_live(CONFIG)
