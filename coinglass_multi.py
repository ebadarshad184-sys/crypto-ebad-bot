import sys
import json
import os
import smtplib
import concurrent.futures
from email.mime.text import MIMEText
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import ccxt

CONFIG = {
    "exchange": "mexc",
    "market_type": "swap",
    "max_coins": 100,
    "timeframes": ["15m", "30m", "45m", "60m", "120m"],
    "candles_to_fetch_15m": 200,  # Speed optimize: Fast data fetch

    "signal_mode": "Balanced",
    "min_score_to_show": 4,  # Strictly filter out 1/6, 2/6, 3/6
    "htf_multiplier": 4,

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
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

STATE_FILE = "alert_state_v5.json"


def get_top_coins(ex, cfg):
    markets = ex.load_markets()
    candidates = []
    for sym, m in markets.items():
        if not m.get("swap") or m.get("quote") != "USDT" or not m.get("active", True):
            continue
        base = m.get("base", "")
        if base in cfg["exclude_bases"] or any(pat in sym for pat in cfg["exclude_patterns"]):
            continue
        candidates.append(sym)

    tickers = ex.fetch_tickers(candidates)
    ranked = sorted(
        tickers.items(),
        key=lambda kv: (kv[1].get("quoteVolume") or 0),
        reverse=True,
    )
    return [sym for sym, _ in ranked[: cfg["max_coins"]]]


def fetch_15m_df(ex, symbol, limit):
    raw = ex.fetch_ohlcv(symbol, timeframe="15m", limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def resample_df(df15, target_minutes):
    if target_minutes == 15:
        return df15.copy()
    df = df15.set_index("timestamp")
    out = df.resample(f"{target_minutes}min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    return out


TF_MINUTES = {"15m": 15, "30m": 30, "45m": 45, "60m": 60, "120m": 120}


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


def compute_htf_trend(df15, base_minutes, multiplier, ema_slow_len):
    htf_minutes = base_minutes * multiplier
    df_htf = resample_df(df15, htf_minutes)
    if len(df_htf) < ema_slow_len + 2:
        return df_htf, None
    df_htf = df_htf.copy()
    df_htf["htf_ema"] = ema(df_htf["close"], ema_slow_len)
    df_htf["is_htf_bullish"] = df_htf["close"] >= df_htf["htf_ema"]
    return df_htf, htf_minutes


def lookup_htf_trend(df_htf, candle_time):
    if df_htf is None or len(df_htf) == 0 or "is_htf_bullish" not in df_htf.columns:
        return None
    matching = df_htf[df_htf["timestamp"] <= candle_time]
    if len(matching) == 0:
        return None
    return bool(matching["is_htf_bullish"].iloc[-1])


def build_indicators(df, cfg):
    df = df.copy()
    mode = cfg["signal_mode"]
    mode_min_rel_vol = cfg["min_rel_vol_input"]

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

    df["pass_cmf_long"] = df["is_accum"] | df["is_high_vol"]
    df["pass_cmf_short"] = df["is_distrib"] | df["is_high_vol"]

    lb = cfg["div_lookback"]
    df["bearish_div"] = (df["close"] > df["close"].shift(lb)) & (df["cmf"] < df["cmf"].shift(lb)) & (df["cmf"] < df["cmf"].shift(1))
    df["bullish_div"] = (df["close"] < df["close"].shift(lb)) & (df["cmf"] > df["cmf"].shift(lb)) & (df["cmf"] > df["cmf"].shift(1))

    df["pass_div_long"] = ~df["bearish_div"]
    df["pass_div_short"] = ~df["bullish_div"]

    candle_range = df["high"] - df["low"]
    body = (df["close"] - df["open"]).abs()
    df["is_solid_body"] = (candle_range > 0) & ((body / candle_range) >= cfg["min_body_ratio"])

    df["is_bull"] = df["close"] > df["open"]
    df["is_bear"] = df["close"] < df["open"]

    df["ema_align_long"] = df["ema_fast"] > df["ema_slow"]
    df["ema_align_short"] = df["ema_fast"] < df["ema_slow"]

    prev_close = df["close"].shift(1)

    df["break_both_ema_long"] = (
        df["is_bull"] & (df["open"] < df["ema_slow"]) & (df["close"] > df["ema_fast"])
        & (df["close"] > df["ema_slow"]) & (df["close"] > prev_close)
    )
    df["break_both_ema_short"] = (
        df["is_bear"] & (df["open"] > df["ema_slow"]) & (df["close"] < df["ema_fast"])
        & (df["close"] < df["ema_slow"]) & (df["close"] < prev_close)
    )

    df["setup_long"] = (
        df["break_both_ema_long"] & df["ema_align_long"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_long"] & df["pass_div_long"]
    )
    df["setup_short"] = (
        df["break_both_ema_short"] & df["ema_align_short"] & vol_break
        & df["is_solid_body"] & df["pass_cmf_short"] & df["pass_div_short"]
    )

    prev_setup_long = df["setup_long"].shift(1).fillna(False)
    prev_setup_short = df["setup_short"].shift(1).fillna(False)
    df["confirm_long"] = prev_setup_long & (df["close"] > prev_close) & df["is_bull"]
    df["confirm_short"] = prev_setup_short & (df["close"] < prev_close) & df["is_bear"]

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


def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
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
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def check_one(df, symbol, timeframe, cfg, state, state_key, now_utc, df15=None, base_minutes=None):
    df = build_indicators(df, cfg)
    i = len(df) - 2
    if i < cfg["div_lookback"] + 5:
        return state_key, None

    candle_time = df["timestamp"].iloc[i]
    ts = str(candle_time)

    age_minutes = (now_utc - candle_time).total_seconds() / 60
    tf_minutes = TF_MINUTES.get(timeframe, 60)
    if age_minutes > tf_minutes * 2.5:
        return state_key, ts

    if state.get(state_key) == ts:
        return state_key, None

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

    # Filter strictly for score >= 4 (Only 4/6, 5/6, 6/6)
    if df["confirm_long"].iloc[i] and score_long_6 >= 4:
        sl, tp = compute_levels(df, i, cfg, "long")
        entry = df["close"].iloc[i]
        body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {score_long_6}/6")
        ok = send_email(f"LONG {symbol} ({timeframe}) {score_long_6}/6", body)
        all_sent_ok = all_sent_ok and ok

    if df["confirm_short"].iloc[i] and score_short_6 >= 4:
        sl, tp = compute_levels(df, i, cfg, "short")
        entry = df["close"].iloc[i]
        body = (f"Coin: {symbol}\nTimeframe: {timeframe}\nMode: {cfg['signal_mode']}\nTime: {ts_pkt}\n"
                f"Entry~: {entry:.5f}\nSL: {sl:.5f}\nTP: {tp:.5f}\nInst Score: {score_short_6}/6")
        ok = send_email(f"SHORT {symbol} ({timeframe}) {score_short_6}/6", body)
        all_sent_ok = all_sent_ok and ok

    if all_sent_ok:
        return state_key, ts
    return state_key, None


def run_live(cfg):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})

    symbols = get_top_coins(ex, cfg)
    state = load_state()

    def fetch_one(symbol):
        try:
            local_ex = ex_class({"enableRateLimit": True, "options": {"defaultType": cfg["market_type"]}})
            df15 = fetch_15m_df(local_ex, symbol, cfg["candles_to_fetch_15m"])
            return symbol, df15, None
        except Exception as e:
            return symbol, None, e

    PARALLEL_WORKERS = 25  # Increased concurrency for instant execution

    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        futures = [pool.submit(fetch_one, sym) for sym in symbols]
        for future in concurrent.futures.as_completed(futures):
            symbol, df15, err = future.result()
            if err is not None:
                continue

            for tf in cfg["timeframes"]:
                minutes = TF_MINUTES[tf]
                df_tf = resample_df(df15, minutes)
                s_key, s_ts = check_one(df_tf, symbol, tf, cfg, state, f"{symbol}_{tf}", now_utc, df15=df15, base_minutes=minutes)
                if s_ts:
                    state[s_key] = s_ts

    save_state(state)
    print("Done scanning.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "live"
    if mode == "live":
        run_live(CONFIG)
    else:
        print("Usage: python3 coinglass_multi.py live")
