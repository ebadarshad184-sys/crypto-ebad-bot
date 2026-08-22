"""
CoinGlass Master Model v5 - FAST Version (100 coins, single-fetch per coin)
===============================================================================
Speed fix: har coin ke liye sirf EK baar 15m data mangwaya jata hai,
baaki saare timeframes (30m/45m/60m/2h) usi se resample karke banaye
jate hain. Isse API calls ~100 tak rehti hain (pehle ~400-1200 thi),
matlab email bohot jaldi aati hai.

Coins: top 100 by volume (high liquidity = zyada clear/reliable trend,
kam noise) - MEXC USDT perpetual swap, no stablecoins/leveraged tokens.

Timeframes: 15m, 30m, 45m, 60m, 2h
Time: Pakistan time (PKT) mein hi likha aata hai, candle ka EXACT close
time hota hai (koi estimate nahi).

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
    "max_coins": 100,                 # high-volume/liquid coins hi lega
    "timeframes": ["15m", "30m", "45m", "60m", "120m"],   # 120m = 2 hour
    "candles_to_fetch_15m": 500,      # 120m resample ke liye kaafi 15m bars chahiye

    "signal_mode": "Balanced",        # "Conservative" / "Balanced" / "More Signals"
    "min_score_to_show": 4,           # score /6 - sirf 4,5,6 wale pe alert (3 ya kam ignore)
    "htf_multiplier": 4,              # HTF = current resample x 4 (extra API call nahi lagta)

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
    "min_rel_vol_input": 1.1,
    "whale_rel_vol": 2.0,

    "atr_length": 14,
    "atr_buffer_mult": 0.2,

    "exclude_patterns": ["3L", "3S", "5L", "5S", "BULL", "BEAR", "UP/", "DOWN/"],
    "exclude_bases": ["USDC", "DAI", "TUSD", "BUSD", "FDUSD", "USDT"],
}

GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "pgmq hgoz kkwc dcwg"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_v5.json"

# ==========================================
# 2. TOP 100 COINS (high volume = high liquidity/clarity)
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
# 3. DATA FETCH (sirf 15m, baaki sab resample se)
# ==========================================
def fetch_15m_df(ex, symbol, limit):
    raw = ex.fetch_ohlcv(symbol, timeframe="15m", limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def resample_df(df15, target_minutes):
    if target_minutes == 15:
        return df15
    df = df15.set_index("timestamp")
    out = df.resample(f"{target_minutes}min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    return out

TF_MINUTES = {"15m": 15, "30m": 30, "45m": 45, "60m": 60, "120m": 120}

def compute_htf_trend(df15, base_minutes, multiplier, ema_slow_len):
    """HTF trend nikalta hai bina koi extra API call kiye - already fetched
    15m data se hi ek bada timeframe resample kar leta hai."""
    htf_minutes = base_minutes * multiplier
    df_htf = resample_df(df15, htf_minutes)
    if len(df_htf) < ema_slow_len + 2:
        return df_htf, None
    df_htf = df_htf.copy()
    df_htf["htf_ema"] = ema(df_htf["close"], ema_slow_len)
    df_htf["is_htf_bullish"] = df_htf["close"] >= df_htf["htf_ema"]
    return df_htf, htf_minutes

def lookup_htf_trend(df_htf, candle_time):
    """Signal candle ke waqt jo bhi HTF bar us se pehle/us waqt close hui thi,
    uska trend dhoondta hai."""
    if df_htf is None or len(df_htf) == 0:
        return None
    matching = df_htf[df_htf["timestamp"] <= candle_time]
    if len(matching) == 0:
        return None
    return bool(matching["is_htf_bullish"].iloc[-1])

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
# 7. CHECK ONE SYMBOL+TIMEFRAME
# ==========================================
def check_one(df, symbol, timeframe, cfg, state, state_key, now_utc, df15=None, base_minutes=None):
    df = build_indicators(df, cfg)
    i = len(df) - 2   # last CLOSED candle
    if i < cfg["div_lookback"] + 5:
        return

    candle_time = df["timestamp"].iloc[i]
    ts = str(candle_time)

    # Purani candle ho to skip (retroactive alert kabhi nahi)
    age_minutes = (now_utc - candle_time).total_seconds() / 60
    tf_minutes = TF_MINUTES.get(timeframe, 60)
    if age_minutes > tf_minutes * 2.5:
        state[state_key] = ts
        return

    if state.get(state_key) == ts:
        return

    # HTF trend nikalo (extra API call nahi, already fetched data se)
    htf_bullish = None
    if df15 is not None and base_minutes is not None:
        df_htf, _ = compute_htf_trend(df15, base_minutes, cfg["htf_multiplier"], cfg["ema_slow_len"])
        htf_bullish = lookup_htf_trend(df_htf, candle_time)

    score_long_6 = int(df["score_long"].iloc[i])
    score_short_6 = int(df["score_short"].iloc[i])
    if htf_bullish is True:
        score_long_6 += 1
    if htf_bullish is False:
        score_short_6 += 1

    ts_pkt = (candle_time + pd.Timedelta(hours=5)).strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time)"
    all_sent_ok = True

    if df["confirm_long"].iloc[i]:
        if score_long_6 >= cfg["min_score_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            star_str = "*" * min(score_long_6 // 2, 3) + "-" * (3 - min(score_long_6 // 2, 3))
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {score_long_6}/6")
            ok = send_email(f"LONG {symbol} ({timeframe}) {score_long_6}/6", body)
            all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i]:
        if score_short_6 >= cfg["min_score_to_show"]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                    f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {score_short_6}/6")
            ok = send_email(f"SHORT {symbol} ({timeframe}) {score_short_6}/6", body)
            all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        state[state_key] = ts

# ==========================================
# 8. LIVE MODE (FAST - ek hi fetch per coin)
# ==========================================
def run_live(cfg):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    print("Top 100 high-liquidity coins fetch kar raha hoon...")
    symbols = get_top_coins(ex, cfg)
    print(f"{len(symbols)} coins mile. Timeframes: {cfg['timeframes']}. Mode: {cfg['signal_mode']}")

    state = load_state()

    for idx, symbol in enumerate(symbols):
        try:
            df15 = fetch_15m_df(ex, symbol, cfg["candles_to_fetch_15m"])
        except Exception as e:
            print(f"{symbol}: fetch fail -> {e}")
            continue

        for tf in cfg["timeframes"]:
            minutes = TF_MINUTES[tf]
            df_tf = resample_df(df15, minutes)
            check_one(df_tf, symbol, tf, cfg, state, f"{symbol}_{tf}", now_utc, df15=df15, base_minutes=minutes)

        if (idx + 1) % 20 == 0:
            print(f"  ...{idx + 1}/{len(symbols)} coins check ho chuke")
            save_state(state)

    save_state(state)
    print("Sab coins check ho gaye.")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    if mode == "live":
        run_live(CONFIG)
    else:
        print("Usage: python3 coinglass_v5.py live")
