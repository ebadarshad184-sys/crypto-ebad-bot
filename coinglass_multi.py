"""
CoinGlass Master Model v3 - Confirmed Institutional Flow (Python Multi-Coin Scanner)
Binance USDT-M Futures, Top 300 Crypto Coins, 15m/30m/45m/60m timeframes, PKT Time.
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
# 1. INPUTS & CONFIGURATIONS
# ==========================================
CONFIG = {
    "exchange": "binance",             # Binance Futures (Best for GitHub Actions execution speed)
    "market_type": "swap",            # USDT Perpetual Futures
    "native_timeframes": ["15m", "30m", "1h"],  # 1h = 60m
    "also_build_45m": True,           # Resample 15m -> 45m
    "candles_to_fetch": 200,
    "top_coin_count": 300,            # Dynamically fetch Top 300 Crypto Coins

    # Strategy Toggles
    "use_htf_filter": False,
    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,

    # Parameters (Pine Script Exact Match)
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

    "min_star_score": 2,  # Alert only on 2-star or 3-star signals (>= 2)
}

# Email Credentials
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "arshadebad5@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "pgmq hgoz kkwc dcwg")
TO_EMAIL = os.environ.get("TO_EMAIL", "arshadebad5@gmail.com")

STATE_FILE = "alert_state.json"

# ==========================================
# 2. DYNAMIC TOP 300 CRYPTO COINS FETCH (BINANCE)
# ==========================================
def get_top_300_crypto_coins(ex, cfg):
    """
    Fetches Top 300 active USDT crypto perpetual contracts sorted by 24h volume on Binance.
    Excludes non-crypto and non-USDT pairs completely.
    """
    try:
        markets = ex.load_markets()
        tickers = ex.fetch_tickers()
        
        valid_markets = []
        for symbol, market in markets.items():
            # Standard Binance USDT perpetual futures filter
            if (market.get("swap", False) and 
                market.get("quote") == "USDT" and 
                market.get("active", True) and
                ":USDT" in symbol):
                
                vol = 0
                if symbol in tickers:
                    t = tickers[symbol]
                    vol = t.get("quoteVolume") or t.get("baseVolume") or 0
                
                valid_markets.append({"symbol": symbol, "volume": vol})

        # Sort by 24h Volume descending
        valid_markets.sort(key=lambda x: x["volume"], reverse=True)
        top_coins = [m["symbol"] for m in valid_markets[:cfg["top_coin_count"]]]
        print(f"Total Binance Crypto Coins Loaded: {len(top_coins)}")
        return top_coins
    except Exception as e:
        print(f"Error fetching top coins: {e}")
        return []

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
# 3. INDICATORS & STRATEGY ENGINE
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

    # EMAs
    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])

    # ATR
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    # Relative Volume
    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["min_rel_vol"]
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    # Smoothed CMF
    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    # Divergence Check
    lb = cfg["div_lookback"]
    bearish_div = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    bullish_div = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))

    df["pass_div_long"] = ~bearish_div if cfg["use_div_filter"] else True
    df["pass_div_short"] = ~bullish_div if cfg["use_div_filter"] else True

    df["pass_cmf_long"] = df["is_accum"] if cfg["use_cmf_filter"] else True
    df["pass_cmf_short"] = df["is_distrib"] if cfg["use_cmf_filter"] else True

    # Candle Filters
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

    # Raw Setup Signals
    df["setup_long"] = (
        df["break_both_ema_long"] & df["ema_align_long"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_long"] & df["pass_div_long"]
    )
    df["setup_short"] = (
        df["break_both_ema_short"] & df["ema_align_short"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_short"] & df["pass_div_short"]
    )

    # Confirmation Candle Logic
    if cfg["use_confirmation"]:
        prev_setup_long = df["setup_long"].shift(1).fillna(False)
        prev_setup_short = df["setup_short"].shift(1).fillna(False)
        df["confirm_long"] = prev_setup_long & (df["close"] > prev_high) & df["is_bull"]
        df["confirm_short"] = prev_setup_short & (df["close"] < prev_low) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

    # Stars Calculation
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
# 4. FAST EMAIL ALERT (WITH PKT TIME)
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
        print("  -> Email bhej diya:", subject)
        return True
    except Exception as e:
        print("  -> Email FAIL:", e)
        return False

# ==========================================
# 5. STATE & EXECUTOR
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

def check_one(df, symbol, timeframe, cfg, state, state_key):
    df = build_indicators(df, cfg)
    i = len(df) - 2  # Last CLOSED candle
    if i < cfg["div_lookback"] + 5:
        return

    ts = str(df["timestamp"].iloc[i])
    if state.get(state_key) == ts:
        return  # Already alerted for this candle

    # Convert UTC candle close timestamp to Exact PKT Time (UTC+5)
    ts_pkt = (df["timestamp"].iloc[i] + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %I:%M %p") + " PKT"
    clean_symbol = symbol.split(":")[0]  # Standard format like BTC/USDT
    
    # Display 60m as '60m' instead of '1h' in email title
    tf_display = "60m" if timeframe == "1h" else timeframe
    all_sent_ok = True

    # LONG Check
    if df["confirm_long"].iloc[i]:
        score = int(df["inst_score_long"].iloc[i])
        if score >= cfg["min_star_score"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            stars = "★" * score + "☆" * (3 - score)
            body = (f"Coin: {clean_symbol}\nTimeframe: {tf_display}\nCandle Closed (PKT): {ts_pkt}\n\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars}")
            ok = send_email(f"🚀 LONG {clean_symbol} ({tf_display}) {stars}", body)
            all_sent_ok = all_sent_ok and ok

    # SHORT Check
    if df["confirm_short"].iloc[i]:
        score = int(df["inst_score_short"].iloc[i])
        if score >= cfg["min_star_score"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            stars = "★" * score + "☆" * (3 - score)
            body = (f"Coin: {clean_symbol}\nTimeframe: {tf_display}\nCandle Closed (PKT): {ts_pkt}\n\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars}")
            ok = send_email(f"🔻 SHORT {clean_symbol} ({tf_display}) {stars}", body)
            all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        state[state_key] = ts

def run_live(cfg):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    symbols = get_top_300_crypto_coins(ex, cfg)
    state = load_state()

    for symbol in symbols:
        try:
            df15 = fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])
        except Exception:
            continue

        for tf in cfg["native_timeframes"]:
            try:
                df = df15 if tf == "15m" else fetch_ohlcv_df(ex, symbol, tf, cfg["candles_to_fetch"])
            except Exception:
                continue
            key = f"{symbol}_{tf}"
            check_one(df, symbol, tf, cfg, state, key)

        if cfg["also_build_45m"]:
            df45 = resample_to_45m(df15)
            key = f"{symbol}_45m"
            check_one(df45, symbol, "45m", cfg, state, key)

    save_state(state)

if __name__ == "__main__":
    run_live(CONFIG)
