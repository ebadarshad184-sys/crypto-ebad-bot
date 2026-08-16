"""
CoinGlass Master Model v3 - Python Version
===========================================
Pine Script indicator ka exact-logic Python conversion.

Do modes hain:
  1) BACKTEST  -> pehle history pe check karo, signals + win-rate dekho
  2) LIVE      -> jab naya CONFIRMED signal bane to Telegram pe message bhejo

PythonAnywhere pe setup:
  pip3.10 install --user ccxt pandas requests

Pehle "python3 coinglass_strategy.py backtest" chalao, result dekho.
Jab satisfied ho jao tab "Tasks" tab mein scheduled task laga do:
  python3.10 /home/USERNAME/coinglass_strategy.py live
Har timeframe ke hisaab se schedule karo (e.g. 15m candle to har 15 min).
"""

import sys
import time
import json
import os
import pandas as pd
import numpy as np
import requests
import ccxt

# ==========================================
# 1. CONFIG (Pine inputs ke equivalent)
# ==========================================
CONFIG = {
    "exchange": "binance",
    "symbol": "BTC/USDT",
    "timeframe": "15m",        # Pine chart timeframe jaisa
    "candles_to_fetch": 500,

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
}

# ntfy.sh notification topic (sirf LIVE mode ke liye zaroori) - free, no signup
NTFY_TOPIC = "ebad_arshad_04"

STATE_FILE = "last_signal_state.json"

# ==========================================
# 2. DATA FETCH
# ==========================================
def fetch_ohlcv(cfg):
    ex_class = getattr(ccxt, cfg["exchange"])
    ex = ex_class({"enableRateLimit": True})
    raw = ex.fetch_ohlcv(cfg["symbol"], timeframe=cfg["timeframe"], limit=cfg["candles_to_fetch"])
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# ==========================================
# 3. INDICATORS (Pine ta.* functions replicate)
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

# ==========================================
# 4. SL / TP calculation (Pine jaisa)
# ==========================================
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
# 5. BACKTEST MODE
# ==========================================
def run_backtest(cfg):
    print(f"Fetching {cfg['candles_to_fetch']} candles for {cfg['symbol']} ({cfg['timeframe']})...")
    df = fetch_ohlcv(cfg)
    df = build_indicators(df, cfg)

    trades = []
    for i in range(len(df)):
        if df["confirm_long"].iloc[i]:
            sl, tp = compute_levels(df, i, cfg, "long")
            entry = df["close"].iloc[i]
            result = simulate_trade(df, i, entry, sl, tp, "long")
            trades.append({"time": df["timestamp"].iloc[i], "side": "LONG", "entry": entry, "sl": sl, "tp": tp, **result})

        if df["confirm_short"].iloc[i]:
            sl, tp = compute_levels(df, i, cfg, "short")
            entry = df["close"].iloc[i]
            result = simulate_trade(df, i, entry, sl, tp, "short")
            trades.append({"time": df["timestamp"].iloc[i], "side": "SHORT", "entry": entry, "sl": sl, "tp": tp, **result})

    if not trades:
        print("Is period mein koi confirmed signal nahi mila. Filters relax karke dobara try karo.")
        return

    res_df = pd.DataFrame(trades)
    closed = res_df[res_df["outcome"] != "open"]
    wins = (closed["outcome"] == "TP").sum()
    losses = (closed["outcome"] == "SL").sum()
    win_rate = (wins / len(closed) * 100) if len(closed) else 0

    print("\n=== BACKTEST RESULT ===")
    print(res_df.to_string(index=False))
    print(f"\nTotal signals: {len(trades)} | Closed: {len(closed)} | Wins: {wins} | Losses: {losses} | Win rate: {win_rate:.1f}%")
    print("\nNOTE: Yeh sirf indicator ke SL/TP levels ka simple simulation hai (spread/fees include nahi).")

def simulate_trade(df, entry_idx, entry, sl, tp, side):
    for j in range(entry_idx + 1, len(df)):
        high = df["high"].iloc[j]
        low = df["low"].iloc[j]
        if side == "long":
            if low <= sl:
                return {"outcome": "SL", "exit_bar": j}
            if high >= tp:
                return {"outcome": "TP", "exit_bar": j}
        else:
            if high >= sl:
                return {"outcome": "SL", "exit_bar": j}
            if low <= tp:
                return {"outcome": "TP", "exit_bar": j}
    return {"outcome": "open", "exit_bar": None}

# ==========================================
# 6. LIVE MODE (ntfy.sh alert - free, no signup)
# ==========================================
def send_ntfy(title, msg):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    try:
        r = requests.post(
            url,
            data=msg.encode("utf-8"),
            headers={"Title": title, "Priority": "high"},  # Title mein emoji mat daalna, error deta hai
            timeout=10,
        )
        if r.status_code != 200:
            print("ntfy error:", r.text)
    except Exception as e:
        print("ntfy send failed:", e)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_alerted_ts": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def run_live(cfg):
    df = fetch_ohlcv(cfg)
    df = build_indicators(df, cfg)

    # Sirf last CLOSED candle check karo (last row abhi bani ho sakti hai / incomplete)
    i = len(df) - 2
    ts = str(df["timestamp"].iloc[i])
    state = load_state()

    if state.get("last_alerted_ts") == ts:
        print("Yeh candle already check ho chuki hai, kuch naya nahi.")
        return

    fired = False
    if df["confirm_long"].iloc[i]:
        sl, tp = compute_levels(df, i, cfg, "long")
        entry = df["close"].iloc[i]
        stars = "*" * int(df["inst_score_long"].iloc[i])
        msg = (f"LONG SIGNAL - {cfg['symbol']} ({cfg['timeframe']})\n"
               f"Time: {ts}\nEntry~: {entry:.2f}\nSL: {sl:.2f}\nTP: {tp:.2f}\nInst Score: {stars}")
        print(msg)
        send_ntfy("LONG Signal", msg)
        fired = True

    if df["confirm_short"].iloc[i]:
        sl, tp = compute_levels(df, i, cfg, "short")
        entry = df["close"].iloc[i]
        stars = "*" * int(df["inst_score_short"].iloc[i])
        msg = (f"SHORT SIGNAL - {cfg['symbol']} ({cfg['timeframe']})\n"
               f"Time: {ts}\nEntry~: {entry:.2f}\nSL: {sl:.2f}\nTP: {tp:.2f}\nInst Score: {stars}")
        print(msg)
        send_ntfy("SHORT Signal", msg)
        fired = True

    if not fired:
        print(f"[{ts}] Koi signal nahi. (rel_vol={df['rel_vol'].iloc[i]:.2f}, cmf={df['cmf'].iloc[i]:.4f})")

    state["last_alerted_ts"] = ts
    save_state(state)

# ==========================================
# 7. ENTRY POINT
# ==========================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if mode == "backtest":
        run_backtest(CONFIG)
    elif mode == "live":
        run_live(CONFIG)
    else:
        print("Usage: python3 coinglass_strategy.py [backtest|live]")
