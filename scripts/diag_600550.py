"""600550 大波段诊断：复现 _check_buy 逻辑，逐日输出买入条件状态。"""
import sys
import math
import numpy as np
import pandas as pd
from easy_tdx import MyTT
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_adjust, parse_period

# ── 常量（与策略一致）─────────────────────────────────────────────────────
ATR_PERIOD = 14
UPPER_SHADOW_RATIO = 1.5
STRONG_TREND_SHADOW_RATIO = 2.0
MA20_DEVIATION_MAX = 0.25
STRONG_TREND_DEV_MAX = 0.35
RECENT_SURGE_DAYS = 5
RECENT_SURGE_MAX = 0.20
STRONG_TREND_SURGE5_MAX = 0.30
SHORT_SURGE_DAYS = 3
SHORT_SURGE_MAX = 0.12
STRONG_TREND_SURGE3_MAX = 0.18
VOLUME_RATIO_MIN = 1.0
STRONG_TREND_ADX = 30
ADX_MIN_THRESHOLD = 20
ADX_MID_RANGE_MIN = 15
ADX_MID_RANGE_MAX = 30
MA60_FLAT_LOOKBACK = 20
MA60_DROP_TOLERANCE = 0.005
MA60_EXCEPTION_GAIN_MIN = 9.5
MA60_EXCEPTION_DROP_MAX = 0.03
VOLATILITY_MAX_PCT = 8.0
RECENT_DROP_LOOKBACK = 3
RECENT_DROP_MAX = 0.06
WEAK_REBOUND_BODY_RATIO = 0.5
BEAR_STRONG_TREND_DI_RATIO = 2.0
FAKE_BREAKOUT_SHADOW_RATIO = 1.0
FAKE_BREAKOUT_DEVIATION_MAX = 0.02
STRONG_REVERSAL_BODY_RATIO = 2.0
MA_BULLISH_MA5_DEVIATION = 0.01


def calc_atr(df, i):
    if i < ATR_PERIOD:
        return df['close'].iloc[i] * 0.03
    tr_sum = 0.0
    for j in range(ATR_PERIOD):
        offset = i - j
        high = df['high'].iloc[offset]
        low = df['low'].iloc[offset]
        prev_c = df['close'].iloc[offset - 1]
        tr_sum += max(high - low, abs(high - prev_c), abs(low - prev_c))
    return tr_sum / ATR_PERIOD


def is_long_upper_shadow_yin(prev_open, prev_high, prev_close, shadow_ratio=UPPER_SHADOW_RATIO):
    if prev_close >= prev_open:
        return False
    body = prev_open - prev_close
    upper_shadow = prev_high - prev_open
    return upper_shadow > body * shadow_ratio


def is_bullish_engulfing(cur_open, cur_close, prev_open, prev_close):
    if prev_close >= prev_open:
        return False
    if cur_close <= cur_open:
        return False
    return cur_open <= prev_close and cur_close >= prev_open


def is_strong_reversal_yang(cur_open, cur_close, prev_open, prev_close):
    if prev_close >= prev_open:
        return False
    if cur_close <= cur_open:
        return False
    if cur_close < prev_open:
        return False
    prev_body = prev_open - prev_close
    cur_body = cur_close - cur_open
    return cur_body >= prev_body * STRONG_REVERSAL_BODY_RATIO


def is_ma_bullish(ma5, ma10, ma20, ma60, ma5_prev, ma10_prev=None):
    if not (ma5 > ma20 > ma60 and ma10 > ma20 > ma60):
        return False
    if ma5 < ma10 and ma5 < ma10 * (1 - MA_BULLISH_MA5_DEVIATION):
        return False
    return True


def diagnose(df, i, label=""):
    """复现 _check_buy，输出每个条件状态。"""
    if i < 60:
        print(f"  {label}: i={i} < 60，数据不足")
        return
    cur_close = df['close'].iloc[i]
    cur_open = df['open'].iloc[i]
    cur_high = df['high'].iloc[i]
    prev_close = df['close'].iloc[i - 1]
    prev_open = df['open'].iloc[i - 1]
    prev_high = df['high'].iloc[i - 1]
    prev_low = df['low'].iloc[i - 1]
    prev2_open = df['open'].iloc[i - 2]
    prev2_close = df['close'].iloc[i - 2]

    ma5 = df['ma5'].iloc[i]; ma5_prev = df['ma5'].iloc[i - 1]
    ma10 = df['ma10'].iloc[i]
    ma20 = df['ma20'].iloc[i]; ma20_prev = df['ma20'].iloc[i - 1]; ma20_5ago = df['ma20'].iloc[i - 5]
    ma60 = df['ma60'].iloc[i]; ma60_ago = df['ma60'].iloc[i - MA60_FLAT_LOOKBACK]
    adx = df['adx'].iloc[i]; adx_5ago = df['adx'].iloc[i - 5]
    pdi = df['pdi'].iloc[i]; mdi = df['mdi'].iloc[i]
    pdi_prev = df['pdi'].iloc[i - 1]; mdi_prev = df['mdi'].iloc[i - 1]
    atr = calc_atr(df, i)
    vol = df['vol'].iloc[i]
    vol_ma5 = df['vol'].iloc[i-5:i].mean()

    date = df['date'].iloc[i]
    is_yang = cur_close > cur_open
    is_prev_yin = prev_close < prev_open

    # 强趋势
    is_strong_trend = (adx == adx and pdi == pdi and mdi == mdi
                       and adx >= STRONG_TREND_ADX and pdi > mdi)
    # 多头排列
    is_bullish = is_ma_bullish(ma5, ma10, ma20, ma60, ma5_prev)
    is_bullish_prev = is_ma_bullish(df['ma5'].iloc[i-1], df['ma10'].iloc[i-1],
                                     df['ma20'].iloc[i-1], df['ma60'].iloc[i-1],
                                     df['ma5'].iloc[i-2])

    blocks = []

    # 1. 波动率
    if cur_close > 0 and atr / cur_close * 100 > VOLATILITY_MAX_PCT:
        blocks.append(f"[1]波动率{atr/cur_close*100:.1f}%>8%")

    # 2. MA20 趋势过滤
    is_close_above_ma20 = ma20 == ma20 and cur_close > ma20
    is_pullback = False
    if not is_close_above_ma20:
        is_close_above_ma60 = ma60 == ma60 and cur_close > ma60
        is_sr = is_strong_reversal_yang(cur_open, cur_close, prev_open, prev_close)
        is_ma20_not_falling = ma20_prev == ma20_prev and ma20 == ma20 and ma20 >= ma20_prev
        if not (is_close_above_ma60 and is_sr and is_ma20_not_falling):
            blocks.append(f"[2]收盘{cur_close:.2f}<=MA20{ma20:.2f}(回调反转:above_ma60={is_close_above_ma60},sr={is_sr},ma20不跌={is_ma20_not_falling})")
        else:
            is_pullback = True

    # 3. MA60 方向
    if i >= MA60_FLAT_LOOKBACK and ma60_ago == ma60_ago and ma60 == ma60 and ma60_ago > 0:
        ma60_drop = (ma60_ago - ma60) / ma60_ago
        if ma60_drop > MA60_DROP_TOLERANCE:
            gain_pct = (cur_close - prev_close) / prev_close if prev_close > 0 else 0
            is_limit_up = gain_pct >= MA60_EXCEPTION_GAIN_MIN / 100
            if not (is_strong_trend and is_limit_up and ma60_drop < MA60_EXCEPTION_DROP_MAX):
                blocks.append(f"[3]MA60跌{ma60_drop*100:.2f}%>0.5%(涨停={is_limit_up},强趋势={is_strong_trend})")

    # 4. ADX 震荡市过滤
    if i >= 5 and adx == adx and adx_5ago == adx_5ago and ma20_5ago == ma20_5ago:
        if adx < ADX_MIN_THRESHOLD and adx < adx_5ago and ma20 <= ma20_5ago:
            blocks.append(f"[4]震荡市ADX{adx:.1f}<20且下行,MA20下行")

    # 5. ADX 中等趋势过滤
    if adx == adx and ADX_MID_RANGE_MIN <= adx < ADX_MID_RANGE_MAX:
        is_sr = is_strong_reversal_yang(cur_open, cur_close, prev_open, prev_close)
        if not is_sr:
            blocks.append(f"[5]中等趋势ADX{adx:.1f}∈[15,30)且非强反转阳线")

    # 6. 追高-偏离MA20
    dev_max = STRONG_TREND_DEV_MAX if is_strong_trend else MA20_DEVIATION_MAX
    if ma20 > 0:
        dev = (cur_close - ma20) / ma20
        if dev > dev_max:
            blocks.append(f"[6]偏离MA20 {dev*100:.1f}%>{dev_max*100:.0f}%(强趋势={is_strong_trend})")

    # 7. 追高-近5日涨幅
    surge5_max = STRONG_TREND_SURGE5_MAX if is_strong_trend else RECENT_SURGE_MAX
    if i >= RECENT_SURGE_DAYS:
        c5 = df['close'].iloc[i - RECENT_SURGE_DAYS]
        if c5 > 0:
            g5 = (cur_close - c5) / c5
            if g5 > surge5_max:
                blocks.append(f"[7]近5日涨{g5*100:.1f}%>{surge5_max*100:.0f}%")

    # 8. 追高-近3日涨幅
    surge3_max = STRONG_TREND_SURGE3_MAX if (is_strong_trend or is_bullish) else SHORT_SURGE_MAX
    if i >= SHORT_SURGE_DAYS:
        c3 = df['close'].iloc[i - SHORT_SURGE_DAYS]
        if c3 > 0:
            g3 = (cur_close - c3) / c3
            if g3 > surge3_max:
                blocks.append(f"[8]近3日涨{g3*100:.1f}%>{surge3_max*100:.0f}%(强趋势={is_strong_trend},多头={is_bullish})")

    # 9. 成交量
    if i >= 5 and vol_ma5 > 0 and vol < vol_ma5 * VOLUME_RATIO_MIN:
        blocks.append(f"[9]量{vol/1e6:.0f}M<5日均{vol_ma5/1e6:.0f}M")

    # 10. 急跌弱反弹
    if i >= RECENT_DROP_LOOKBACK:
        c3d = df['close'].iloc[i - 3]
        if c3d > 0:
            rd = (prev_close - c3d) / c3d
            if rd < -RECENT_DROP_MAX:
                cur_body = cur_close - cur_open
                prev_body = prev_open - prev_close
                if prev_body > 0 and cur_body < prev_body * WEAK_REBOUND_BODY_RATIO:
                    blocks.append(f"[10]急跌弱反弹")

    # 11. 空头强趋势
    is_bear_strong = (adx == adx and pdi_prev == pdi_prev and mdi_prev == mdi_prev
                      and adx >= STRONG_TREND_ADX and mdi_prev >= pdi_prev * BEAR_STRONG_TREND_DI_RATIO)
    if is_bear_strong and not is_pullback:
        blocks.append(f"[11]空头强趋势 -DI/-DI={mdi_prev/pdi_prev:.2f}>=2(前日)")

    # 12. 假突破
    is_fake = False
    if i >= 2 and is_yang:
        cur_body = cur_close - cur_open
        if cur_body > 0:
            us = cur_high - cur_close
            if us > cur_body * FAKE_BREAKOUT_SHADOW_RATIO:
                ptm = max(prev_open, prev_close, prev2_open, prev2_close)
                if ptm > 0:
                    dev = abs(cur_close - ptm) / ptm
                    is_fake = dev <= FAKE_BREAKOUT_DEVIATION_MAX
                    if is_fake:
                        blocks.append(f"[12]假突破 上影{us:.2f}>实体{cur_body:.2f},偏离前两日高{dev*100:.1f}%<=2%")

    # ── 原始买入条件 ──
    is_ma5_up = ma5 > ma5_prev
    is_be = is_bullish_engulfing(cur_open, cur_close, prev_open, prev_close)
    is_sr = is_strong_reversal_yang(cur_open, cur_close, prev_open, prev_close)
    is_ma_cond = is_ma5_up or is_be or is_sr
    is_above_prev_low = cur_close > prev_low
    sr = STRONG_TREND_SHADOW_RATIO if is_strong_trend else UPPER_SHADOW_RATIO
    is_prev_no_shadow = not is_long_upper_shadow_yin(prev_open, prev_high, prev_close, sr)

    main_buy = (is_yang and is_ma_cond and is_above_prev_low and is_prev_yin
                and is_prev_no_shadow and (is_pullback or not is_bear_strong)
                and not is_fake)
    # 多头排列形成
    bullish_buy = is_yang and is_bullish and not is_bullish_prev and not blocks

    status = "可买入(影线后收阳)" if (main_buy and not blocks) else \
             ("可买入(多头排列形成)" if bullish_buy else "无信号")
    if blocks and (main_buy or bullish_buy):
        status = "形态满足但被过滤"

    print(f"\n  {date} {label}")
    print(f"    O{cur_open:.2f} H{cur_high:.2f} L{df['low'].iloc[i]:.2f} C{cur_close:.2f} | "
          f"MA5{ma5:.2f} MA10{ma10:.2f} MA20{ma20:.2f} MA60{ma60:.2f} | "
          f"ADX{adx:.1f} +DI{pdi:.1f} -DI{mdi:.1f} ATR{atr:.2f}")
    print(f"    阳线={is_yang} 前日阴={is_prev_yin} MA5上={is_ma5_up} 吞没={is_be} "
          f"强反转={is_sr} 多头={is_bullish}(前日{is_bullish_prev}) 强趋势={is_strong_trend}")
    print(f"    形态: 主买={main_buy} 多头买={bullish_buy} -> {status}")
    if blocks:
        for b in blocks:
            print(f"    阻断: {b}")


def main():
    with get_mac_client() as client:
        df = client.get_stock_kline(
            parse_market("1"), "600550",
            period=parse_period("daily"), start=0, count=300,
            adjust=parse_adjust("qfq"),
        )
    df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
    df['ma5'] = MyTT.MA(df['close'].values, 5)
    df['ma10'] = MyTT.MA(df['close'].values, 10)
    df['ma20'] = MyTT.MA(df['close'].values, 20)
    df['ma60'] = MyTT.MA(df['close'].values, 60)
    pdi, mdi, adx, adxr = MyTT.DMI(df['close'].values, df['high'].values, df['low'].values)
    df['pdi'] = pdi; df['mdi'] = mdi; df['adx'] = adx
    df = df.reset_index(drop=True)

    # 找到关键日期的 index
    def idx(d): 
        m = df.index[df['date'] == d]
        return m[0] if len(m) else -1

    print("=" * 80)
    print("【区间1】01-22止盈后 ~ 02-05（为什么没买回，02-03却追高）")
    print("=" * 80)
    for d in ['2026-01-22','2026-01-23','2026-01-26','2026-01-27','2026-01-28',
              '2026-01-29','2026-01-30','2026-02-02','2026-02-03','2026-02-04','2026-02-05','2026-02-06']:
        i = idx(d)
        if i > 0:
            diagnose(df, i, "")

    print("\n" + "=" * 80)
    print("【区间2】03-03盈利锁定卖出后 ~ 03-10（为什么错过03-09顶点19.58）")
    print("=" * 80)
    for d in ['2026-03-03','2026-03-04','2026-03-05','2026-03-06','2026-03-09','2026-03-10']:
        i = idx(d)
        if i > 0:
            diagnose(df, i, "")


if __name__ == "__main__":
    main()
