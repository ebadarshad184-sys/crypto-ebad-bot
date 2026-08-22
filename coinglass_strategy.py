//@version=5
indicator("CoinGlass Master Model v3 - Confirmed Institutional Flow (Fixed)", overlay=true, max_boxes_count=500, max_labels_count=500, max_lines_count=500)

// ==========================================
// 1. INPUTS & CONFIGURATIONS
// ==========================================
use_htf_filter    = input.bool(false, title="Enable HTF Trend Filter? (Uncheck for MORE TRADES)")
use_cmf_filter    = input.bool(true,  title="Enable Institutional Flow (CMF) Filter?")
use_div_filter    = input.bool(true,  title="Enable Institutional Divergence Filter? (Blocks fakeouts)")
require_whale_vol = input.bool(false, title="Require WHALE Volume for Signal? (Fewer, Stronger Trades)")
use_confirmation  = input.bool(true,  title="Wait for Confirmation Candle? (Avoids fake breakouts)")

htf_timeframe   = input.timeframe("60", title="Higher Timeframe (HTF Filter)")
minBodyRatio    = input.float(0.30, title="Min Body Size Ratio (Soft Doji Filter)", step=0.05)

inst_cmf_len    = input.int(20, title="Institutional Flow Period (CMF)")
cmf_smooth_len  = input.int(5,  title="CMF Smoothing (reduces noise)")
div_lookback    = input.int(10, title="Divergence Lookback (bars)")

ema_fast_len    = input.int(9,  title="Fast EMA Length (9 EMA)")
ema_slow_len    = input.int(20, title="Slow EMA Length (20 EMA)")

vol_ema_length  = input.int(20,  title="Institutional Volume EMA Length")
min_rel_vol     = input.float(1.3, title="Min Relative Volume (x avg) - High Volume", step=0.1)
whale_rel_vol   = input.float(2.0, title="Whale Relative Volume (x avg) - Whale Volume", step=0.1)

atr_length      = input.int(14, title="ATR Length")
atr_buffer_mult = input.float(0.2, title="SL Buffer Multiplier (ATR)", step=0.05)
obBoxExtend     = input.int(8, title="Order Block Extension (Bars)", minval=1, maxval=50)
show_text       = input.bool(true, title="Show USD Volume & Institutional Score inside Labels?")

// ==========================================
// 2. INDICATORS & HTF CALCULATIONS
// ==========================================
ema_fast = ta.ema(close, ema_fast_len)
ema_slow = ta.ema(close, ema_slow_len)

plot(ema_fast, color=color.blue, title="9 EMA (Fast)", linewidth=2)
plot(ema_slow, color=color.orange, title="20 EMA (Slow Baseline)", linewidth=2)

vol_ema = ta.ema(volume, vol_ema_length)

// FIX #1: lookahead_off use kiya (lookahead_on = repainting bug, future data leak).
htf_ema   = request.security(syminfo.tickerid, htf_timeframe, ta.ema(close, ema_slow_len), barmerge.gaps_off, barmerge.lookahead_off)
htf_close = request.security(syminfo.tickerid, htf_timeframe, close, barmerge.gaps_off, barmerge.lookahead_off)

bool isHtfBullish = htf_close >= htf_ema
bool isHtfBearish = htf_close < htf_ema

bool passHtfLong  = use_htf_filter ? isHtfBullish : true
bool passHtfShort = use_htf_filter ? isHtfBearish : true

atr  = ta.atr(atr_length)
step = atr * 0.4

// --- INSTITUTIONAL VOLUME (RELATIVE VOLUME) ---
relVol         = vol_ema > 0 ? volume / vol_ema : 1.0
bool isHighVol   = relVol >= min_rel_vol
bool isWhaleVol  = relVol >= whale_rel_vol

// --- INSTITUTIONAL FLOW (SMOOTHED CMF) ---
mfv        = ((close - low) - (high - close)) / (high - low > 0 ? high - low : 1) * volume
volSmaLen  = ta.sma(volume, inst_cmf_len)
// FIX #3: division-by-zero guard
cmfRaw     = volSmaLen > 0 ? ta.sma(mfv, inst_cmf_len) / volSmaLen : 0.0
cmf        = ta.ema(cmfRaw, cmf_smooth_len)

bool isAccumulation = cmf > 0
bool isDistribution  = cmf < 0

bool passCmfLong  = use_cmf_filter ? isAccumulation : true
bool passCmfShort = use_cmf_filter ? isDistribution : true

// --- INSTITUTIONAL DIVERGENCE ---
bool bearishInstDivergence = (close > close[div_lookback]) and (cmf < cmf[div_lookback]) and (cmf < cmf[1])
bool bullishInstDivergence = (close < close[div_lookback]) and (cmf > cmf[div_lookback]) and (cmf > cmf[1])

bool passDivLong  = use_div_filter ? not bearishInstDivergence : true
bool passDivShort = use_div_filter ? not bullishInstDivergence : true

format_usd(val) =>
    val >= 1000000 ? "$" + str.tostring(val / 1000000, "#.#") + "M" :
     val >= 1000 ? "$" + str.tostring(val / 1000, "#.#") + "K" :
     "$" + str.tostring(val, "#")

// --- CANDLE FILTERS ---
candleRange  = high - low
bodySize     = math.abs(close - open)
isSolidBody  = (candleRange > 0) and ((bodySize / candleRange) >= minBodyRatio)

// ==========================================
// 3. STRATEGY CONDITIONS (SETUP CANDLE)
// ==========================================
bool isBullishCandle = close > open
bool isBearishCandle = close < open

bool emaAlignmentLong  = ema_fast > ema_slow
bool emaAlignmentShort = ema_fast < ema_slow

bool breakBothEmaLong  = isBullishCandle and (open < ema_slow) and (close > ema_fast) and (close > ema_slow) and (close > high[1])
bool breakBothEmaShort = isBearishCandle and (open > ema_slow) and (close < ema_fast) and (close < ema_slow) and (close < low[1])

bool volBreak = isHighVol and (require_whale_vol ? isWhaleVol : true)

bool setupLong  = breakBothEmaLong  and emaAlignmentLong  and volBreak and isSolidBody and passCmfLong  and passHtfLong  and passDivLong
bool setupShort = breakBothEmaShort and emaAlignmentShort and volBreak and isSolidBody and passCmfShort and passHtfShort and passDivShort

// ==========================================
// 3B. CONFIRMATION LOGIC (waits for NEXT candle)
// ==========================================
bool confirmLong  = use_confirmation ? (setupLong[1]  and close > high[1] and close > open) : setupLong
bool confirmShort = use_confirmation ? (setupShort[1] and close < low[1]  and close < open) : setupShort

instScoreLong  = (isAccumulation ? 1 : 0) + (isHighVol ? 1 : 0) + (isWhaleVol ? 1 : 0)
instScoreShort = (isDistribution ? 1 : 0) + (isHighVol ? 1 : 0) + (isWhaleVol ? 1 : 0)

stars(score) =>
    score == 3 ? "★★★" : score == 2 ? "★★☆" : score == 1 ? "★☆☆" : "☆☆☆"

bool regularLong  = confirmLong
bool regularShort = confirmShort

// ==========================================
// 4. DRAWING SIGNALS
// ==========================================
// FIX #2: barstate.isconfirmed add kiya - ab sirf bar close pe ek baar draw hoga
if not barstate.isfirst and barstate.isconfirmed
    vol_info = show_text ? " (" + format_usd(volume * close) + ")" : ""

    if regularLong
        setupBarIdx = use_confirmation ? bar_index - 1 : bar_index
        setupHigh   = use_confirmation ? high[1] : high
        setupLow    = use_confirmation ? low[1]  : low
        setupOpen   = use_confirmation ? open[1] : open
        setupClose  = use_confirmation ? close[1] : close

        bottom_buy = setupLow - step
        long_sl    = bottom_buy - (atr * atr_buffer_mult)
        long_tp    = close + (4 * step)
        score_info = show_text ? "\nInst Score: " + stars(instScoreLong) : ""

        box.new(left=setupBarIdx, top=math.max(setupOpen, setupClose), right=setupBarIdx + obBoxExtend, bottom=setupLow, border_color=color.teal, bgcolor=color.new(color.teal, 80))
        label.new(bar_index + 1, bottom_buy, "STRATEGY LONG (Confirmed)" + vol_info + score_info + "\nSL: " + str.tostring(long_sl, "#.##"), style=label.style_label_left, color=color.teal, textcolor=color.white, size=size.small)
        line.new(bar_index, long_sl, bar_index + 8, long_sl, color=color.red, width=2, style=line.style_dashed)
        line.new(bar_index, long_tp, bar_index + 8, long_tp, color=color.green, width=2, style=line.style_dashed)

    if regularShort
        setupBarIdx = use_confirmation ? bar_index - 1 : bar_index
        setupHigh   = use_confirmation ? high[1] : high
        setupLow    = use_confirmation ? low[1]  : low
        setupOpen   = use_confirmation ? open[1] : open
        setupClose  = use_confirmation ? close[1] : close

        top_sell   = setupHigh + step
        short_sl   = top_sell + (atr * atr_buffer_mult)
        short_tp   = close - (4 * step)
        score_info = show_text ? "\nInst Score: " + stars(instScoreShort) : ""

        box.new(left=setupBarIdx, top=setupHigh, right=setupBarIdx + obBoxExtend, bottom=math.min(setupOpen, setupClose), border_color=color.purple, bgcolor=color.new(color.purple, 80))
        label.new(bar_index + 1, top_sell, "STRATEGY SHORT (Confirmed)" + vol_info + score_info + "\nSL: " + str.tostring(short_sl, "#.##"), style=label.style_label_left, color=color.purple, textcolor=color.white, size=size.small)
        line.new(bar_index, short_sl, bar_index + 8, short_sl, color=color.red, width=2, style=line.style_dashed)
        line.new(bar_index, short_tp, bar_index + 8, short_tp, color=color.green, width=2, style=line.style_dashed)

// FIX #4: Long/Short alag alertcondition
alertcondition(regularLong,  title="LONG Signal",  message="STRATEGY LONG confirmed on {{ticker}} ({{interval}})")
alertcondition(regularShort, title="SHORT Signal", message="STRATEGY SHORT confirmed on {{ticker}} ({{interval}})")