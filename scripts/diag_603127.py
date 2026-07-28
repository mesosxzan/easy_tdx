# ruff: noqa: E501, E741
"""603127 回测诊断脚本。

逐 bar 分析策略决策，找出：
1. 哪些交易日满足基本买入条件但被某个过滤器拦截
2. 3 笔交易的详细买卖点指标值
3. 交易次数过少的根因
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd

from easy_tdx import MyTT

# 导入策略常量
sys.path.insert(0, "strategies")
from shadow_yang_v2 import (  # noqa: E402
    ADX_MID_RANGE_MAX,
    ADX_MID_RANGE_MIN,
    ADX_MIN_THRESHOLD,
    ATR_PERIOD,
    BODY_RATIO_MIN,
    BOLL_DEVIATION,
    BOLL_PCTB_OVERBOUGHT,
    BOLL_PERIOD,
    BOLL_WIDTH_DEATH_ZONE_HIGH,
    BOLL_WIDTH_DEATH_ZONE_LOW,
    CCI_OVERBOUGHT,
    CCI_PERIOD,
    KDJ_J_OVERBOUGHT,
    KDJ_PERIOD,
    LOWER_SHADOW_RATIO_MAX,
    MA20_DEVIATION_MAX,
    MA60_DROP_TOLERANCE,
    MA60_EXCEPTION_DROP_MAX,
    MA60_EXCEPTION_GAIN_MIN,
    MA60_FLAT_LOOKBACK,
    MFI_OVERBOUGHT,
    MFI_PERIOD,
    RECENT_DROP_LOOKBACK,
    RECENT_DROP_MAX,
    RECENT_SURGE_DAYS,
    RECENT_SURGE_MAX,
    SHORT_SURGE_DAYS,
    SHORT_SURGE_MAX,
    STRONG_TREND_ADX,
    STRONG_TREND_DEV_MAX,
    STRONG_TREND_SHADOW_RATIO,
    STRONG_TREND_SURGE3_MAX,
    STRONG_TREND_SURGE5_MAX,
    UPPER_SHADOW_RATIO,
    VOLATILITY_MAX_PCT,
    VOLUME_RATIO_MIN,
    WEAK_REBOUND_BODY_RATIO,
)

MARKET = "SH"
CODE = "603127"
COUNT = 500


def fetch_data() -> pd.DataFrame:
    """获取 K 线数据（与回测引擎一致）。"""
    from easy_tdx.cli.conn import get_mac_client
    from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period

    with get_mac_client() as client:
        df = client.get_stock_kline(
            parse_market(MARKET),
            CODE,
            period=parse_period("DAILY"),
            start=0,
            count=COUNT,
            adjust=parse_adjust("NONE"),
        )
    return df


def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算策略使用的所有指标。"""
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    df["open"].to_numpy(dtype=float)
    vol = df["vol"].to_numpy(dtype=float)

    df["ma5"] = MyTT.MA(close, 5)
    df["ma10"] = MyTT.MA(close, 10)
    df["ma20"] = MyTT.MA(close, 20)
    df["ma60"] = MyTT.MA(close, 60)

    pdi, mdi, adx, adxr = MyTT.DMI(close, high, low)
    df["pdi"] = pdi
    df["mdi"] = mdi
    df["adx"] = adx
    df["adxr"] = adxr

    df["cci"] = MyTT.CCI(close, high, low, CCI_PERIOD)
    df["mfi"] = MyTT.MFI(close, high, low, vol, MFI_PERIOD)

    boll_upper, boll_mid, boll_lower = MyTT.BOLL(close, BOLL_PERIOD, BOLL_DEVIATION)
    df["boll_upper"] = boll_upper
    df["boll_mid"] = boll_mid
    df["boll_lower"] = boll_lower

    kdj_k, kdj_d, kdj_j = MyTT.KDJ(close, high, low, KDJ_PERIOD, 3, 3)
    df["kdj_k"] = kdj_k
    df["kdj_d"] = kdj_d
    df["kdj_j"] = kdj_j

    # ATR
    tr_list = []
    for i in range(len(close)):
        if i < ATR_PERIOD:
            tr_list.append(close[i] * 0.03)
            continue
        tr_sum = 0.0
        for j in range(ATR_PERIOD):
            offset = i - j
            h = high[offset]
            l = low[offset]
            prev_c = close[offset - 1]
            tr1 = h - l
            tr2 = abs(h - prev_c)
            tr3 = abs(l - prev_c)
            tr_sum += max(tr1, tr2, tr3)
        tr_list.append(tr_sum / ATR_PERIOD)
    df["atr"] = tr_list

    return df


def is_nan(v: Any) -> bool:
    """安全 NaN 检查。"""
    try:
        return bool(np.isnan(v))
    except (TypeError, ValueError):
        return True


def diagnose_buy_filters(df: pd.DataFrame) -> list[dict]:
    """逐 bar 检查买入条件，记录拦截原因。

    返回所有「当日是阳线 + 前日是阴线」的候选日（基本形态满足），
    以及被拦截的原因。
    """
    results: list[dict] = []
    n = len(df)

    for i in range(n):
        if i < 60:  # MA60 需要足够数据
            continue

        row = df.iloc[i]
        prev = df.iloc[i - 1]

        cur_close = float(row["close"])
        cur_open = float(row["open"])
        cur_high = float(row["high"])
        cur_low = float(row["low"])
        prev_close = float(prev["close"])
        prev_open = float(prev["open"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        is_yang = cur_close > cur_open
        is_prev_yin = prev_close < prev_open

        # 只分析基本形态满足的候选日（阳线 + 前日阴线）
        if not (is_yang and is_prev_yin and cur_close > prev_low):
            continue

        # 计算指标
        adx_now = float(row["adx"])
        pdi_now = float(row["pdi"])
        mdi_now = float(row["mdi"])
        pdi_prev = float(df.iloc[i - 1]["pdi"])
        mdi_prev = float(df.iloc[i - 1]["mdi"])
        ma5_now = float(row["ma5"])
        ma5_prev = float(df.iloc[i - 1]["ma5"])
        ma20_now = float(row["ma20"])
        ma60_now = float(row["ma60"])
        cci_now = float(row["cci"])
        mfi_now = float(row["mfi"])
        kdj_j_now = float(row["kdj_j"])
        atr_now = float(row["atr"])
        boll_upper = float(row["boll_upper"])
        boll_lower = float(row["boll_lower"])
        boll_mid = float(row["boll_mid"])

        is_strong_trend = (
            not is_nan(adx_now)
            and not is_nan(pdi_now)
            and not is_nan(mdi_now)
            and adx_now >= STRONG_TREND_ADX
            and pdi_now > mdi_now
        )

        date = row["datetime"]
        reasons: list[str] = []
        passed: list[str] = []

        # 1. 波动率过滤
        if cur_close > 0 and atr_now / cur_close * 100 > VOLATILITY_MAX_PCT:
            reasons.append(f"波动率过滤(ATR/价={atr_now / cur_close * 100:.1f}%>{VOLATILITY_MAX_PCT}%)")
        else:
            passed.append("波动率")

        # 2. 趋势过滤 - 收盘价须在 MA20 之上（或回调反转）
        is_close_above_ma20 = not is_nan(ma20_now) and cur_close > ma20_now
        is_pullback_reversal = False
        if not is_close_above_ma20:
            ma60_now_v = ma60_now
            is_close_above_ma60 = not is_nan(ma60_now_v) and cur_close > ma60_now_v
            # 强反转阳线检查
            prev_body = prev_open - prev_close
            cur_body = cur_close - cur_open
            is_strong_reversal = (
                is_prev_yin
                and cur_close > cur_open
                and cur_close >= prev_open
                and prev_body > 0
                and cur_body >= prev_body * 2.0
            )
            ma20_prev = float(df.iloc[i - 1]["ma20"])
            is_ma20_not_falling = (
                not is_nan(ma20_prev) and not is_nan(ma20_now) and ma20_now >= ma20_prev
            )
            if is_close_above_ma60 and is_strong_reversal and is_ma20_not_falling:
                is_pullback_reversal = True
                passed.append("回调反转买入")
            else:
                reasons.append(
                    f"收盘价<MA20({cur_close:.2f}<{ma20_now:.2f})且不满足回调反转"
                )

        # 3. MA60 趋势方向过滤
        if i >= MA60_FLAT_LOOKBACK:
            ma60_ago = float(df.iloc[i - MA60_FLAT_LOOKBACK]["ma60"])
            if not is_nan(ma60_now) and not is_nan(ma60_ago) and ma60_ago > 0:
                ma60_drop = (ma60_ago - ma60_now) / ma60_ago
                if ma60_drop > MA60_DROP_TOLERANCE:
                    gain_pct = (cur_close - prev_close) / prev_close if prev_close > 0 else 0
                    is_limit_up = gain_pct >= MA60_EXCEPTION_GAIN_MIN / 100
                    if not (is_strong_trend and is_limit_up and ma60_drop < MA60_EXCEPTION_DROP_MAX):
                        reasons.append(f"MA60下行(跌{ma60_drop * 100:.1f}%)")

        # 4. ADX 震荡市过滤
        if i >= 5 and not is_nan(adx_now):
            adx_5ago = float(df.iloc[i - 5]["adx"])
            ma20_5ago = float(df.iloc[i - 5]["ma20"])
            if (
                not is_nan(adx_5ago)
                and not is_nan(ma20_5ago)
                and adx_now < ADX_MIN_THRESHOLD
                and adx_now < adx_5ago
                and ma20_now <= ma20_5ago
            ):
                reasons.append(f"震荡市(ADX={adx_now:.1f}<20且下行,MA20下行)")

        # 5. ADX 中等趋势过滤
        if not is_nan(adx_now) and ADX_MID_RANGE_MIN <= adx_now < ADX_MID_RANGE_MAX:
            prev_body = prev_open - prev_close
            cur_body = cur_close - cur_open
            is_strong_reversal_yang = (
                is_prev_yin
                and cur_close > cur_open
                and cur_close >= prev_open
                and prev_body > 0
                and cur_body >= prev_body * 2.0
            )
            if not is_strong_reversal_yang:
                reasons.append(f"ADX中等趋势({adx_now:.1f},15-30)且非强反转阳线")

        # 6. 追高保护 - 偏离 MA20
        dev_max = STRONG_TREND_DEV_MAX if is_strong_trend else MA20_DEVIATION_MAX
        if ma20_now > 0:
            deviation = (cur_close - ma20_now) / ma20_now
            if deviation > dev_max:
                reasons.append(f"偏离MA20过大({deviation * 100:.1f}%>{dev_max * 100:.0f}%)")

        # 7. 追高保护 - 近 5 日涨幅
        surge5_max = STRONG_TREND_SURGE5_MAX if is_strong_trend else RECENT_SURGE_MAX
        if i >= RECENT_SURGE_DAYS:
            close_5ago = float(df.iloc[i - RECENT_SURGE_DAYS]["close"])
            if close_5ago > 0:
                recent_gain = (cur_close - close_5ago) / close_5ago
                if recent_gain > surge5_max:
                    reasons.append(f"近5日涨幅过大({recent_gain * 100:.1f}%>{surge5_max * 100:.0f}%)")

        # 8. 追高保护 - 近 3 日涨幅
        ma5 = ma5_now
        ma10 = float(row["ma10"])
        ma20 = ma20_now
        ma60 = ma60_now
        is_bullish = (
            not is_nan(ma5)
            and not is_nan(ma10)
            and not is_nan(ma20)
            and not is_nan(ma60)
            and ma5 > ma20 > ma60
            and ma10 > ma20 > ma60
        )
        surge3_max = STRONG_TREND_SURGE3_MAX if (is_strong_trend or is_bullish) else SHORT_SURGE_MAX
        if i >= SHORT_SURGE_DAYS:
            close_3ago = float(df.iloc[i - SHORT_SURGE_DAYS]["close"])
            if close_3ago > 0:
                short_gain = (cur_close - close_3ago) / close_3ago
                if short_gain > surge3_max:
                    reasons.append(f"近3日涨幅过大({short_gain * 100:.1f}%>{surge3_max * 100:.0f}%)")

        # 9. 成交量确认
        if i >= 5:
            vol_now = float(row["vol"])
            vol_ma5 = sum(float(df.iloc[i - j]["vol"]) for j in range(1, 6)) / 5.0
            if vol_ma5 > 0 and vol_now < vol_ma5 * VOLUME_RATIO_MIN:
                reasons.append(f"成交量不足(量比={vol_now / vol_ma5:.2f}<1.0)")

        # 10. 急跌弱反弹过滤
        if i >= RECENT_DROP_LOOKBACK:
            close_3ago = float(df.iloc[i - 3]["close"])
            if close_3ago > 0:
                recent_drop = (prev_close - close_3ago) / close_3ago
                if recent_drop < -RECENT_DROP_MAX:
                    cur_body_v = cur_close - cur_open
                    prev_body_v = prev_open - prev_close
                    if prev_body_v > 0 and cur_body_v < prev_body_v * WEAK_REBOUND_BODY_RATIO:
                        reasons.append("急跌弱反弹")

        # 11. CCI 超买
        if not is_nan(cci_now) and cci_now > CCI_OVERBOUGHT:
            reasons.append(f"CCI超买({cci_now:.0f}>{CCI_OVERBOUGHT})")

        # 12. MFI 超买
        if not is_nan(mfi_now) and mfi_now > MFI_OVERBOUGHT:
            reasons.append(f"MFI超买({mfi_now:.0f}>{MFI_OVERBOUGHT})")

        # 13. BOLL %b 超买
        if not is_strong_trend and not is_nan(boll_upper) and not is_nan(boll_lower) and boll_upper > boll_lower:
            boll_pctb = (cur_close - boll_lower) / (boll_upper - boll_lower)
            if boll_pctb > BOLL_PCTB_OVERBOUGHT:
                reasons.append(f"BOLL%b超买({boll_pctb:.2f}>{BOLL_PCTB_OVERBOUGHT})")

        # 14. 布林带宽死亡区间
        if not is_nan(boll_mid) and boll_mid > 0 and not is_nan(boll_upper) and not is_nan(boll_lower):
            boll_width_pct = (boll_upper - boll_lower) / boll_mid * 100
            if BOLL_WIDTH_DEATH_ZONE_LOW <= boll_width_pct <= BOLL_WIDTH_DEATH_ZONE_HIGH:
                reasons.append(f"布林带宽死亡区间({boll_width_pct:.1f}%)")

        # 15. 下影线过长
        lower_shadow = min(cur_open, cur_close) - cur_low
        cur_body_abs = abs(cur_close - cur_open)
        if cur_body_abs > 0 and lower_shadow > cur_body_abs * LOWER_SHADOW_RATIO_MAX:
            reasons.append("下影线过长")

        # 16. KDJ-J 极端超买
        if not is_nan(kdj_j_now) and kdj_j_now > KDJ_J_OVERBOUGHT:
            reasons.append(f"KDJ-J超买({kdj_j_now:.0f}>{KDJ_J_OVERBOUGHT})")

        # 17. 阳线实体占比过小
        full_range = cur_high - cur_low
        if full_range > 0 and cur_body_abs / full_range < BODY_RATIO_MIN:
            reasons.append(f"实体占比小({cur_body_abs / full_range:.2f}<{BODY_RATIO_MIN})")

        # 18. 空头强趋势过滤
        is_bearish_strong = (
            not is_nan(adx_now)
            and not is_nan(pdi_prev)
            and not is_nan(mdi_prev)
            and adx_now >= STRONG_TREND_ADX
            and mdi_prev >= pdi_prev * 2.0
        )
        if is_bearish_strong and not is_pullback_reversal:
            reasons.append(f"空头强趋势(-DI/+DI={mdi_prev / pdi_prev:.1f}>2.0)")

        # 19. 假突破过滤
        if i >= 2 and is_yang:
            cur_body = cur_close - cur_open
            if cur_body > 0:
                upper_shadow = cur_high - cur_close
                if upper_shadow > cur_body * 1.0:
                    prev2_open = float(df.iloc[i - 2]["open"])
                    prev2_close = float(df.iloc[i - 2]["close"])
                    prev_two_max = max(prev_open, prev_close, prev2_open, prev2_close)
                    if prev_two_max > 0:
                        deviation = abs(cur_close - prev_two_max) / prev_two_max
                        if deviation <= 0.02:
                            reasons.append("假突破(长上影+收盘近前高)")

        # 上影线检查
        shadow_ratio = STRONG_TREND_SHADOW_RATIO if is_strong_trend else UPPER_SHADOW_RATIO
        body = prev_open - prev_close
        upper_shadow_prev = prev_high - prev_open
        is_prev_long_upper_shadow = upper_shadow_prev > body * shadow_ratio if body > 0 else False
        if is_prev_long_upper_shadow:
            reasons.append("前日长上影阴线")

        # MA5 方向 + 看涨吞没 + 强反转阳线
        is_ma5_up = ma5_now > ma5_prev if not is_nan(ma5_prev) else False
        is_bullish_engulfing = (
            is_prev_yin
            and cur_close > cur_open
            and cur_open <= prev_close
            and cur_close >= prev_open
        )
        prev_body_v2 = prev_open - prev_close
        cur_body_v2 = cur_close - cur_open
        is_strong_reversal_yang = (
            is_prev_yin
            and cur_close > cur_open
            and cur_close >= prev_open
            and prev_body_v2 > 0
            and cur_body_v2 >= prev_body_v2 * 2.0
        )
        is_ma_condition_met = is_ma5_up or is_bullish_engulfing or is_strong_reversal_yang

        if not is_ma_condition_met:
            reasons.append("MA条件不满足(MA5未上行+非吞没+非强反转)")

        results.append(
            {
                "date": date,
                "close": cur_close,
                "adx": adx_now,
                "is_strong_trend": is_strong_trend,
                "is_bullish": is_bullish,
                "reasons": reasons,
                "passed": passed,
                "can_buy": len(reasons) == 0,
                "boll_pctb": (cur_close - boll_lower) / (boll_upper - boll_lower)
                if (not is_nan(boll_upper) and not is_nan(boll_lower) and boll_upper > boll_lower)
                else float("nan"),
            }
        )

    return results


def analyze_trades(df: pd.DataFrame) -> None:
    """分析 3 笔交易的关键指标。"""
    # 交易日期
    trades = [
        ("2024-10-18", "买入", 16.94, "影线后收阳"),
        ("2024-10-30", "卖出", 17.42, "盈利锁定"),
        ("2025-03-14", "买入", 21.87, "影线后收阳"),
        ("2025-03-25", "卖出", 20.82, "止损清仓"),
        ("2025-08-13", "买入", 34.20, "多头排列形成"),
        ("2025-08-20", "卖出", 31.14, "止损清仓"),
    ]

    print("\n" + "=" * 80)
    print("=== 3 笔交易关键指标分析 ===")
    print("=" * 80)

    for date_str, action, price, reason in trades:
        # 找到对应日期
        mask = df["datetime"].astype(str).str.startswith(date_str)
        matched = df[mask]
        if matched.empty:
            print(f"\n[{date_str}] {action} @{price} ({reason}) -- 未找到数据")
            continue
        idx = matched.index[0]
        row = df.loc[idx]

        print(f"\n[{date_str}] {action} @{price:.2f} ({reason})")
        print(f"  收盘={row['close']:.2f} 开盘={row['open']:.2f} 最高={row['high']:.2f} 最低={row['low']:.2f}")
        print(f"  MA5={row['ma5']:.2f} MA10={row['ma10']:.2f} MA20={row['ma20']:.2f} MA60={row['ma60']:.2f}")
        print(f"  ADX={row['adx']:.1f} +DI={row['pdi']:.1f} -DI={row['mdi']:.1f}")
        print(f"  CCI={row['cci']:.0f} MFI={row['mfi']:.0f} KDJ-J={row['kdj_j']:.0f}")
        print(f"  ATR={row['atr']:.2f} (ATR%={row['atr'] / row['close'] * 100:.1f}%)")
        boll_u = row["boll_upper"]
        boll_l = row["boll_lower"]
        boll_m = row["boll_mid"]
        if not is_nan(boll_u) and not is_nan(boll_l) and boll_u > boll_l:
            pctb = (row["close"] - boll_l) / (boll_u - boll_l)
            bw = (boll_u - boll_l) / boll_m * 100 if boll_m > 0 else 0
            print(f"  BOLL%b={pctb:.2f} 带宽={bw:.1f}%")

        # 多头排列检查
        ma5, ma10, ma20, ma60 = row["ma5"], row["ma10"], row["ma20"], row["ma60"]
        is_bullish = (
            not is_nan(ma5)
            and not is_nan(ma10)
            and not is_nan(ma20)
            and not is_nan(ma60)
            and ma5 > ma20 > ma60
            and ma10 > ma20 > ma60
        )
        print(f"  多头排列={is_bullish}")

        # 如果是买入日，分析后续走势
        if action == "买入":
            entry_idx = idx
            entry_price = price
            # 找后续最高价
            after = df.loc[entry_idx:]
            max_high = after["high"].max()
            max_high_date = after.loc[after["high"].idxmax(), "datetime"]
            max_gain = (max_high - entry_price) / entry_price * 100
            print(f"  >>> 买入后最高价={max_high:.2f} (日期={max_high_date}), 最大浮盈={max_gain:.1f}%")


def main() -> None:
    print(">>> 获取 603127 K 线数据...")
    df = fetch_data()
    print(f"   获取 {len(df)} 根 K 线，范围: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

    print("\n>>> 计算指标...")
    df = calc_indicators(df)

    # 整体走势
    first_close = df["close"].iloc[0]
    last_close = df["close"].iloc[-1]
    total_return = (last_close - first_close) / first_close * 100
    max_high = df["high"].max()
    min_low = df["low"].min()
    print(f"   期初价={first_close:.2f}, 期末价={last_close:.2f}, 区间收益={total_return:.1f}%")
    print(f"   最高价={max_high:.2f}, 最低价={min_low:.2f}")

    # 分析 3 笔交易
    analyze_trades(df)

    # 分析买入候选日
    print("\n" + "=" * 80)
    print("=== 买入候选日分析（阳线+前日阴线+收盘>前低）===")
    print("=" * 80)

    results = diagnose_buy_filters(df)

    can_buy_days = [r for r in results if r["can_buy"]]
    blocked_days = [r for r in results if not r["can_buy"]]

    print(f"\n候选日总数: {len(results)}")
    print(f"  可买入日: {len(can_buy_days)}")
    print(f"  被拦截日: {len(blocked_days)}")

    # 统计拦截原因频次
    from collections import Counter

    reason_counter: Counter[str] = Counter()
    for r in blocked_days:
        for reason in r["reasons"]:
            # 提取原因名称（去掉括号内的细节）
            reason_name = reason.split("(")[0]
            reason_counter[reason_name] += 1

    print("\n--- 拦截原因频次 ---")
    for reason, count in reason_counter.most_common(20):
        print(f"  {reason}: {count} 次")

    # 显示被拦截的候选日（重点关注可买入日附近）
    print("\n--- 可买入日详情 ---")
    for r in can_buy_days:
        print(
            f"  {r['date']} 收盘={r['close']:.2f} ADX={r['adx']:.1f} "
            f"强趋势={r['is_strong_trend']} 多头排列={r['is_bullish']}"
        )

    # 显示被拦截日（前 30 个）
    print("\n--- 被拦截候选日（前 30 个）---")
    for r in blocked_days[:30]:
        reason_str = "; ".join(r["reasons"])
        print(f"  {r['date']} 收盘={r['close']:.2f} ADX={r['adx']:.1f} | {reason_str}")

    # 特别关注：大牛股的主升浪期间，有多少候选日被拦截
    # 603127 买入持有 +245%，主升浪期间应该有很多买入机会
    print("\n--- 主升浪期间候选日分析 ---")
    # 找到涨幅最大的区间
    df["ret_from_start"] = (df["close"] - first_close) / first_close * 100
    # 找到价格翻倍以上的区间
    surge_mask = df["ret_from_start"] > 50
    if surge_mask.any():
        surge_start = df.loc[surge_mask, "datetime"].iloc[0]
        surge_end = df.loc[surge_mask, "datetime"].iloc[-1]
        print(f"  主升浪区间: {surge_start} ~ {surge_end}")
        surge_results = [r for r in results if str(r["date"]) >= str(surge_start) and str(r["date"]) <= str(surge_end)]
        surge_can_buy = [r for r in surge_results if r["can_buy"]]
        surge_blocked = [r for r in surge_results if not r["can_buy"]]
        print(f"  主升浪候选日: {len(surge_results)} (可买入 {len(surge_can_buy)}, 被拦截 {len(surge_blocked)})")
        print("\n  主升浪被拦截日:")
        for r in surge_blocked:
            reason_str = "; ".join(r["reasons"])
            print(f"    {r['date']} 收盘={r['close']:.2f} ADX={r['adx']:.1f} | {reason_str}")


if __name__ == "__main__":
    main()
