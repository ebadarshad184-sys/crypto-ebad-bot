"""
CoinGlass Strategy #2 - Python Multi-Coin, Multi-Timeframe Bot (Top 300 Coins)
====================================================================================
Top 300 USDT perpetual futures coins, 4 timeframes (15m, 30m, 45m, 1h),
Filter: Minimum Score 5/6 or 6/6 (3-Star Only) for Alerts.
Timezone: Pakistan Standard Time (PKT) Exact Real-Time Fix.
Separate State File: alert_state_ebad1.json
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
    "market_type": "swap",           # perpetual futures (USDT-M)
    "native_timeframes": ["15m", "30m", "1h"],
    "also_build_45m": True,          # 45m resampled from 15m base fetch
    "candles_to_fetch": 200,
    "top_n_coins": 300,              # Top 300 coins filter

    # Mode: "Balanced", "Conservative", "More Signals"
    "signal_mode": "Balanced",
    "use_cmf_filter": True,
    "use_div_filter": True,
    "require_whale_vol": False,
    "use_confirmation": True,
    "use_htf_filter": False,

    "min_score_to_show": 5,          # Minimum Score 5/6 or 6/6 (3-Star) for alerts
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
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

# Separate JSON state file
STATE_FILE = "alert_state_ebad1.json"

mode_min_rel_vol = CONFIG["min_rel_vol_input"]
if CONFIG["signal_mode"] == "Conservative":
    mode_min_rel_vol = max(CONFIG["min_rel_vol_input"], 1.30)
elif CONFIG["signal_mode"] == "Balanced":
    mode_min_rel_vol = max(CONFIG["min_rel_vol_input"], 1.10)
elif CONFIG["signal_mode"] == "More Signals":
    mode_min_rel_vol = max(CONFIG["min_rel_vol_input"], 1.00)

CONFIG["mode_min_rel_vol"] = mode_min_rel_vol

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

def build_indicators_v5(df, cfg):
    df = df.copy()
    
    df["ema_fast"] = ema(df["close"], cfg["ema_fast_len"])
    df["ema_slow"] = ema(df["close"], cfg["ema_slow_len"])
    df["atr"] = wilder_atr(df, cfg["atr_length"])
    df["step"] = df["atr"] * 0.4

    df["vol_ema"] = ema(df["volume"], cfg["vol_ema_length"])
    df["rel_vol"] = np.where(df["vol_ema"] > 0, df["volume"] / df["vol_ema"], 1.0)
    df["is_high_vol"] = df["rel_vol"] >= cfg["mode_min_rel_vol"]
    df["is_whale_vol"] = df["rel_vol"] >= cfg["whale_rel_vol"]

    vol_break = df["is_whale_vol"] if cfg["require_whale_vol"] else df["is_high_vol"]

    hl_range = (df["high"] - df["low"]).replace(0, 1)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range * df["volume"]
    cmf_raw = sma(mfv, cfg["inst_cmf_len"]) / sma(df["volume"], cfg["inst_cmf_len"])
    df["cmf"] = ema(cmf_raw, cfg["cmf_smooth_len"])

    df["is_accum"] = df["cmf"] > 0
    df["is_distrib"] = df["cmf"] < 0

    flexible_flow = cfg["signal_mode"] != "Conservative"
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
    bearish_div = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    bullish_div = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))
    
    df["bearish_div"] = bearish_div
    df["bullish_div"] = bullish_div

    df["pass_div_long"] = ~bearish_div if cfg["use_div_filter"] else True
    df["pass_div_short"] = ~bullish_div if cfg["use_div_filter"] else True

    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["min_body_ratio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    strict_breakout = cfg["signal_mode"] == "Conservative"
    if strict_breakout:
        df["break_long"] = df["close"] > df["high"].shift(1)
        df["break_short"] = df["close"] < df["low"].shift(1)
    else:
        df["break_long"] = df["close"] > df["close"].shift(1)
        df["break_short"] = df["close"] < df["close"].shift(1)

    df["break_both_ema_long"] = (
        df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"])
        & (df["close"] > df["ema_slow"]) & df["break_long"]
    )
    df["break_both_ema_short"] = (
        df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"])
        & (df["close"] < df["ema_slow"]) & df["break_short"]
    )

    df["setup_long"] = (
        df["break_both_ema_long"] & df["ema_align_long"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_long"] & df["pass_div_long"]
    )
    df["setup_short"] = (
        df["break_both_ema_short"] & df["ema_align_short"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_short"] & df["pass_div_short"]
    )

    strict_conf = cfg["signal_mode"] == "Conservative"
    prev_setup_long = df["setup_long"].shift(1).fillna(False)
    prev_setup_short = df["setup_short"].shift(1).fillna(False)

    if cfg["use_confirmation"]:
        if strict_conf:
            df["confirm_long"] = prev_setup_long & (df["close"] > df["high"].shift(1)) & df["is_bull"]
            df["confirm_short"] = prev_setup_short & (df["close"] < df["low"].shift(1)) & df["is_bear"]
        else:
            df["confirm_long"] = prev_setup_long & (df["close"] > df["close"].shift(1)) & df["is_bull"]
            df["confirm_short"] = prev_setup_short & (df["close"] < df["low"].shift(1)) & df["is_bear"]
    else:
        df["confirm_long"] = df["setup_long"]
        df["confirm_short"] = df["setup_short"]

    df["score_long"] = (
        df["is_accum"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int) +
        df["ema_align_long"].astype(int) +
        (~df["bearish_div"]).astype(int)
    )

    df["score_short"] = (
        df["is_distrib"].astype(int) +
        df["is_high_vol"].astype(int) +
        df["is_whale_vol"].astype(int) +
        df["ema_align_short"].astype(int) +
        (~df["bullish_div"]).astype(int)
    )

    def calc_stars(score):
        if score >= 5:
            return 3
        elif score >= 3:
            return 2
        return 1

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
    df = build_indicators_v5(df, cfg)
    
    # Latest closed candle check (Fixing old candle delay)
    i = len(df) - 1
    if i < cfg["div_lookback"] + 5:
        return

    ts_str = str(df["timestamp"].iloc[i])
    if state.get(state_key) == ts_str:
        return

    # Real-time Pakistan Time Localizer
    ts_pkt = df["timestamp"].iloc[i].tz_convert("Asia/Karachi").strftime("%Y-%m-%d %I:%M %p") + " (PKT)"
    usd_vol = df["volume"].iloc[i] * df["close"].iloc[i]
    usd_vol_str = f"${usd_vol/1e6:.2f}M" if usd_vol >= 1e6 else f"${usd_vol/1e3:.1f}K"

    all_sent_ok = True

    if df["confirm_long"].iloc[i]:
        stars = int(df["stars_long"].iloc[i])
        score = int(df["score_long"].iloc[i])
        if score >= cfg["min_score_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            star_str = "★" * stars + "☆" * (3 - stars)
            body = (f"STRATEGY #2 LONG SIGNAL ({cfg['signal_mode']} Mode)\n"
                    f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                    f"Score: {score}/6 | Quality: {star_str}\nVol USD: {usd_vol_str}")
            print(f"STRAT2 LONG {symbol} {timeframe} Score={score}/6 Stars={star_str}")
            ok = send_email(f"STRAT2 LONG {symbol} ({timeframe}) Score {score}/6", body)
            all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i]:
        stars = int(df["stars_short"].iloc[i])
        score = int(df["score_short"].iloc[i])
        if score >= cfg["min_score_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            star_str = "★" * stars + "☆" * (3 - stars)
            body = (f"STRATEGY #2 SHORT SIGNAL ({cfg['signal_mode']} Mode)\n"
                    f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\n"
                    f"Score: {score}/6 | Quality: {star_str}\nVol USD: {usd_vol_str}")
            print(f"STRAT2 SHORT {symbol} {timeframe} Score={score}/6 Stars={star_str}")
            ok = send_email(f"STRAT2 SHORT {symbol} ({timeframe}) Score {score}/6", body)
            all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        state[state_key] = ts_str

def run_live(cfg):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    print("Fetching top 300 coins for Strategy #2...")
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
    print("Scan for Strategy #2 completed successfully.")

def run_backtest(cfg, symbol, timeframe):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    df = resample_to_45m(fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])) if timeframe == "45m" else fetch_ohlcv_df(ex, symbol, timeframe, cfg["candles_to_fetch"])
    df = build_indicators_v5(df, cfg)

    signals = []
    for i in range(len(df)):
        if df["confirm_long"].iloc[i] and df["score_long"].iloc[i] >= cfg["min_score_to_show"]:
            signals.append((df["timestamp"].iloc[i], "LONG", df["stars_long"].iloc[i], df["score_long"].iloc[i]))
        if df["confirm_short"].iloc[i] and df["score_short"].iloc[i] >= cfg["min_score_to_show"]:
            signals.append((df["timestamp"].iloc[i], "SHORT", df["stars_short"].iloc[i], df["score_short"].iloc[i]))

    print(f"\n{symbol} ({timeframe}) - {len(signals)} High Quality Signals (Score 5+):")
    for ts, side, stars, score in signals:
        star_str = "★" * stars + "☆" * (3 - stars)
        print(f"  {ts}  {side}  Score={score}/6  Stars={star_str}")

# ==========================================
# 6. ENTRY POINT
# ==========================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    if mode == "live":
        run_live(CONFIG)
    elif mode == "backtest":
        sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT:USDT"
        tf = sys.argv[3] if len(sys.argv) > 3 else "30m"
        run_backtest(CONFIG, sym, tf)
    else:
        print("Usage: python3 <script_name>.py [live|backtest SYMBOL TIMEFRAME]")
