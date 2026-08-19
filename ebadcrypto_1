"""
CoinGlass Master Model v3 - Multi-Coin, Multi-Timeframe Version
===================================================================
Top 30 USDT perpetual futures coins, 4 timeframes (15m/30m/45m/60m),
sirf 2-star aur 3-star (Inst Score) signals pe alert bhejta hai.

PythonAnywhere / GitHub Actions pe chalane se pehle:
  pip install ccxt pandas numpy --break-system-packages

Email setup (Gmail App Password zaroori hai):
  neeche GMAIL_ADDRESS, GMAIL_APP_PASSWORD, TO_EMAIL fill karo.

Command:
  python3 coinglass_multi.py live
  python3 coinglass_multi.py backtest BTC/USDT:USDT 15m   (single-coin test)
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
    "market_type": "swap",           # perpetual futures (USDT-M)
    "native_timeframes": ["5m", "15m", "30m", "1h"],
    "also_build_45m": True,          # 45m ko 15m data se resample karke banayega
    "candles_to_fetch": 300,

    # Fixed list - top 100 jani-pehchani crypto coins, koi stock ya anjaan coin nahi
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
        "STRK/USDT:USDT", "ORDI/USDT:USDT", "RUNE/USDT:USDT", "GRT/USDT:USDT",
        "THETA/USDT:USDT", "FTM/USDT:USDT", "EOS/USDT:USDT", "FLOW/USDT:USDT",
        "AR/USDT:USDT", "MKR/USDT:USDT", "KAS/USDT:USDT", "NOT/USDT:USDT",
        "CORE/USDT:USDT", "CFX/USDT:USDT", "MANA/USDT:USDT", "AXS/USDT:USDT",
        "CHZ/USDT:USDT", "DYDX/USDT:USDT", "CRV/USDT:USDT", "COMP/USDT:USDT",
        "SNX/USDT:USDT", "1INCH/USDT:USDT", "ENS/USDT:USDT", "AGIX/USDT:USDT",
        "OCEAN/USDT:USDT", "ALT/USDT:USDT", "PORTAL/USDT:USDT", "MEME/USDT:USDT",
        "SATS/USDT:USDT", "RATS/USDT:USDT", "BOME/USDT:USDT", "MEW/USDT:USDT",
        "POPCAT/USDT:USDT", "BRETT/USDT:USDT", "NEO/USDT:USDT", "IOTA/USDT:USDT",
        "XMR/USDT:USDT", "ZEC/USDT:USDT", "DASH/USDT:USDT", "EGLD/USDT:USDT",
        "KAVA/USDT:USDT", "MINA/USDT:USDT", "ROSE/USDT:USDT", "WOO/USDT:USDT",
        "JTO/USDT:USDT", "BLUR/USDT:USDT", "PIXEL/USDT:USDT", "MYRO/USDT:USDT",
        "BEAM/USDT:USDT", "GMX/USDT:USDT", "ZRO/USDT:USDT", "IO/USDT:USDT",
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

    "min_star_score": 2,   # sirf 2 aur 3 star signals pe alert (0,1 ignore)
}

# Email (Gmail App Password)
GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state.json"

# ==========================================
# 2. TOP 30 COINS - FIXED LIST (sirf jani-pehchani coins)
# ==========================================
def get_top_coins(ex, cfg):
    markets = ex.load_markets()
    valid = []
    for sym in cfg["fixed_coin_list"]:
        if sym in markets and markets[sym].get("active", True):
            valid.append(sym)
        else:
            print(f"  (skip - {sym} is exchange pe available nahi)")
    return valid

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
# 4. INDICATORS (same logic as Pine)
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
# 5. EMAIL ALERT
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
# 6. STATE (duplicate alerts rokne ke liye)
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
# 7. EK SYMBOL + TIMEFRAME CHECK KARO
# ==========================================
def check_one(df, symbol, timeframe, cfg, state, state_key):
    df = build_indicators(df, cfg)
    i = len(df) - 2   # last CLOSED candle
    if i < cfg["div_lookback"] + 5:
        return

    ts = str(df["timestamp"].iloc[i])
    if state.get(state_key) == ts:
        return  # already checked/alerted yeh candle

    # Pakistan time mein dikhane ke liye (UTC + 5 ghante)
    ts_pkt = (df["timestamp"].iloc[i] + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"

    all_sent_ok = True

    if df["confirm_long"].iloc[i]:
        score = int(df["inst_score_long"].iloc[i])
        if score >= cfg["min_star_score"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            stars = "*" * score + "-" * (3 - score)
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars}")
            print(f"LONG  {symbol} {timeframe} score={score}")
            ok = send_email(f"LONG {symbol} ({timeframe}) {stars}", body)
            all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i]:
        score = int(df["inst_score_short"].iloc[i])
        if score >= cfg["min_star_score"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            stars = "*" * score + "-" * (3 - score)
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.4f}\nSL: {sl:.4f}\nTP: {tp:.4f}\nInst Score: {stars}")
            print(f"SHORT {symbol} {timeframe} score={score}")
            ok = send_email(f"SHORT {symbol} ({timeframe}) {stars}", body)
            all_sent_ok = all_sent_ok and ok

    # Sirf tabhi "done" mark karo jab email(s) safaltapoorvak bhej di gayi ho,
    # warna agli run mein dobara try hoga.
    if all_sent_ok:
        state[state_key] = ts

# ==========================================
# 8. LIVE MODE - saare coins x saare timeframes
# ==========================================
def run_live(cfg):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    print("Top coins fetch kar raha hoon...")
    symbols = get_top_coins(ex, cfg)
    print(f"{len(symbols)} coins mile:")
    for s in symbols:
        print(f"  - {s}")
    print("Timeframes: 5m, 15m, 30m, 45m, 60m")
    print("Check shuru...")

    state = load_state()

    for symbol in symbols:
        try:
            df15 = fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])
        except Exception as e:
            print(f"{symbol}: 15m data fail -> {e}")
            continue

        for tf in cfg["native_timeframes"]:
            try:
                if tf == "15m":
                    df = df15
                else:
                    df = fetch_ohlcv_df(ex, symbol, tf, cfg["candles_to_fetch"])
            except Exception as e:
                print(f"{symbol} {tf}: fetch fail -> {e}")
                continue
            key = f"{symbol}_{tf}"
            check_one(df, symbol, tf, cfg, state, key)

        if cfg["also_build_45m"]:
            df45 = resample_to_45m(df15)
            key = f"{symbol}_45m"
            check_one(df45, symbol, "45m", cfg, state, key)

    save_state(state)
    print("Sab coins check ho gaye.")

# ==========================================
# 9. BACKTEST MODE (single coin, quick check)
# ==========================================
def run_backtest(cfg, symbol, timeframe):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    if timeframe == "45m":
        df15 = fetch_ohlcv_df(ex, symbol, "15m", cfg["candles_to_fetch"])
        df = resample_to_45m(df15)
    else:
        df = fetch_ohlcv_df(ex, symbol, timeframe, cfg["candles_to_fetch"])

    df = build_indicators(df, cfg)
    signals = []
    for i in range(len(df)):
        if df["confirm_long"].iloc[i]:
            score = int(df["inst_score_long"].iloc[i])
            if score >= cfg["min_star_score"]:
                signals.append((df["timestamp"].iloc[i], "LONG", score))
        if df["confirm_short"].iloc[i]:
            score = int(df["inst_score_short"].iloc[i])
            if score >= cfg["min_star_score"]:
                signals.append((df["timestamp"].iloc[i], "SHORT", score))

    print(f"\n{symbol} ({timeframe}) - {len(signals)} qualifying signals (score >= {cfg['min_star_score']}):")
    for ts, side, score in signals:
        print(f"  {ts}  {side}  score={score}")

# ==========================================
# 10. ENTRY POINT
# ==========================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    if mode == "live":
        run_live(CONFIG)
    elif mode == "backtest":
        sym = sys.argv[2] if len(sys.argv) > 2 else "BTC/USDT:USDT"
        tf = sys.argv[3] if len(sys.argv) > 3 else "15m"
        run_backtest(CONFIG, sym, tf)
    else:
        print("Usage: python3 coinglass_multi.py [live|backtest SYMBOL TIMEFRAME]")
