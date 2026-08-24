import ccxt
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime
import smtplib
from email.mime.text import MIMEText

# =====================================================
# 1. SETTINGS & INPUTS
# =====================================================
SYMBOL = 'BTC/USDT'
TIMEFRAMES = ['15m', '30m', '45m', '1h']
MIN_SCORE_TO_TRADE = 4
POLL_INTERVAL = 10 

EMA_FAST = 9
EMA_SLOW = 20

# 🔴 GMAIL SETTINGS - Yahan apni details dalein 🔴
SENDER_EMAIL = "ebadarshad184@gmail.com"  # Jis email se message bhejna hai
APP_PASSWORD = "ondd zmuv exqj csrh" # Gmail App Password (regular password nahi chalega)
RECEIVER_EMAIL = "arshadebad5@gmail.com" # Jis par alert receive karna hai

exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
last_processed_candle_time = None

# =====================================================
# 2. HELPER FUNCTIONS
# =====================================================
def send_gmail_alert(signal_type, symbol, score, entry, sl, tp, tf_trend):
    """Email bhejny ka function"""
    try:
        body = f"""
        CoinGlass Master Alert!
        
        Signal: {signal_type}
        Symbol: {symbol}
        Score: {score}/6
        Entry Price: {entry:.2f}
        Stop Loss: {sl:.2f}
        Take Profit: {tp:.2f}
        MTF Trend: {tf_trend}
        Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        msg = MIMEText(body)
        msg['Subject'] = f"🚀 LIVE TRADE: {signal_type} on {symbol} ({score}/6)"
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("📧 GMAIL ALERT SENT SUCCESSFULLY!")
    except Exception as e:
        print(f"❌ Gmail alert failed: {e}")

def fetch_ohlcv_data(symbol, timeframe, limit=100):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['ema_fast'] = ta.ema(df['close'], length=EMA_FAST)
    df['ema_slow'] = ta.ema(df['close'], length=EMA_SLOW)
    df['vol_ema'] = ta.ema(df['volume'], length=20)
    df['rel_vol'] = df['volume'] / df['vol_ema']
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    mfv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / (df['high'] - df['low']) * df['volume']
    cmf_raw = mfv.rolling(20).sum() / df['volume'].rolling(20).sum()
    df['cmf'] = ta.ema(cmf_raw, length=5)
    return df

def check_multi_tf_alignment(symbol):
    alignments = {}
    for tf in TIMEFRAMES:
        df = fetch_ohlcv_data(symbol, tf, limit=50)
        last_fast = df['ema_fast'].iloc[-2]
        last_slow = df['ema_slow'].iloc[-2]
        if last_fast > last_slow: alignments[tf] = 'BULLISH'
        elif last_fast < last_slow: alignments[tf] = 'BEARISH'
        else: alignments[tf] = 'NEUTRAL'
    if all(v == 'BULLISH' for v in alignments.values()): return 'BULLISH'
    elif all(v == 'BEARISH' for v in alignments.values()): return 'BEARISH'
    return 'MIXED'

def calculate_score(df, tf_alignment, direction):
    score = 0
    row = df.iloc[-2]
    cmf_accum = row['cmf'] > 0
    cmf_dist = row['cmf'] < 0
    is_high_vol = row['rel_vol'] >= 1.10
    is_whale_vol = row['rel_vol'] >= 2.00
    
    if direction == 'LONG':
        if cmf_accum: score += 1
        if is_high_vol: score += 1
        if is_whale_vol: score += 1
        if row['ema_fast'] > row['ema_slow']: score += 1
        if tf_alignment == 'BULLISH': score += 1
        score += 1
    elif direction == 'SHORT':
        if cmf_dist: score += 1
        if is_high_vol: score += 1
        if is_whale_vol: score += 1
        if row['ema_fast'] < row['ema_slow']: score += 1
        if tf_alignment == 'BEARISH': score += 1
        score += 1
    return score

# =====================================================
# 3. LIVE SCANNER ENGINE
# =====================================================
print("🚀 Real-Time Scanner with GMAIL ALERTS Started...")

while True:
    try:
        df_15m = fetch_ohlcv_data(SYMBOL, '15m', limit=50)
        current_candle_time = df_15m['timestamp'].iloc[-1]
        
        if last_processed_candle_time != current_candle_time:
            tf_trend = check_multi_tf_alignment(SYMBOL)
            closed_bar = df_15m.iloc[-2]
            
            is_bullish_break = (closed_bar['open'] < closed_bar['ema_slow']) and (closed_bar['close'] > closed_bar['ema_fast']) and (closed_bar['close'] > closed_bar['ema_slow'])
            is_bearish_break = (closed_bar['open'] > closed_bar['ema_slow']) and (closed_bar['close'] < closed_bar['ema_fast']) and (closed_bar['close'] < closed_bar['ema_slow'])

            # LONG SETUP
            if is_bullish_break and tf_trend == 'BULLISH':
                score = calculate_score(df_15m, tf_trend, 'LONG')
                if score >= MIN_SCORE_TO_TRADE:
                    step = closed_bar['atr'] * 0.4
                    sl = closed_bar['low'] - (closed_bar['atr'] * 0.2)
                    tp = closed_bar['close'] + (4 * step)
                    
                    print(f"\n🟢 LONG DETECTED! Score: {score}/6")
                    send_gmail_alert("LONG", SYMBOL, score, closed_bar['close'], sl, tp, tf_trend)
                    last_processed_candle_time = current_candle_time

            # SHORT SETUP
            elif is_bearish_break and tf_trend == 'BEARISH':
                score = calculate_score(df_15m, tf_trend, 'SHORT')
                if score >= MIN_SCORE_TO_TRADE:
                    step = closed_bar['atr'] * 0.4
                    sl = closed_bar['high'] + (closed_bar['atr'] * 0.2)
                    tp = closed_bar['close'] - (4 * step)
                    
                    print(f"\n🔴 SHORT DETECTED! Score: {score}/6")
                    send_gmail_alert("SHORT", SYMBOL, score, closed_bar['close'], sl, tp, tf_trend)
                    last_processed_candle_time = current_candle_time
                    
        time.sleep(POLL_INTERVAL)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(5)
