# ruff: noqa: E501
"""诊断 603127 优化效果：检查 is_entry_strong_trend 计算。"""

from __future__ import annotations

import sys

import pandas as pd

CODE = "603127"
MARKET = "SH"
COUNT = 500

sys.path.insert(0, "strategies")
from shadow_yang_v2 import ShadowYangStrategy as Strategy, STRONG_TREND_ADX  # noqa: E402


def fetch_data() -> pd.DataFrame:
    """获取 K 线数据（与回测引擎一致）。"""
    from easy_tdx import Market, Period
    from easy_tdx.mac.client import MacClient

    client = MacClient.from_best_host()
    try:
        client.connect()
        df = client.get_stock_kline(
            Market.SH,
            CODE,
            period=Period.DAILY,
            start=0,
            count=COUNT,
        )
    finally:
        client.close()
    return df


def main() -> None:
    df = fetch_data()
    print(f"数据: {len(df)} 根 K 线")
    print(f"列: {list(df.columns)}")
    print(f"索引类型: {type(df.index)}")
    # 检查是否有 date/datetime 列
    date_col = None
    for col in ["date", "datetime", "time"]:
        if col in df.columns:
            date_col = col
            break
    if date_col:
        print(f"日期列: {date_col}, 示例: {df[date_col].iloc[0]} ~ {df[date_col].iloc[-1]}")
    else:
        print(f"索引示例: {df.index[0]} ~ {df.index[-1]}")

    # 导入更多常量
    from shadow_yang_v2 import (  # noqa: E402
        ATR_STOP_MULTIPLIER,
        RAPID_STOP_MULTIPLIER,
        ADX_RAPID_THRESHOLD,
        YIN_DROP_ATR_RATIO,
        STRONG_TREND_YIN_DROP_RATIO,
        STRONG_TREND_YIN_MIN_PCT,
        RAPID_MAX_HOLD_DAYS,
        TREND_ENTRY_RAPID_HOLD_DAYS,
        RAPID_MIN_PROFIT_KEEP,
        MAX_LOSS_PCT,
    )

    strategy = Strategy()
    strategy._bind_data(df)
    strategy._call_init()

    # 买入和卖出日期（从回测结果获取）
    buy_dates = ["2024-10-18", "2025-03-14", "2025-08-13"]
    sell_dates = ["2024-10-30", "2025-03-31", "2025-09-04"]

    # 配对买卖
    pairs = list(zip(buy_dates, sell_dates))

    for buy_date, sell_date in pairs:
        buy_i = None
        sell_i = None
        for i in range(len(df)):
            dt_str = str(df["datetime"].iloc[i])[:10]
            if dt_str == buy_date:
                buy_i = i
            if dt_str == sell_date:
                sell_i = i

        if buy_i is None or sell_i is None:
            print(f"无法找到 {buy_date} 或 {sell_date}")
            continue

        entry_price = df["close"].iloc[buy_i]
        exit_close = df["close"].iloc[sell_i]
        exit_open = df["open"].iloc[sell_i]
        prev_close = df["close"].iloc[sell_i - 1] if sell_i > 0 else 0
        profit_pct = (exit_close - entry_price) / entry_price
        loss_pct = (entry_price - exit_close) / entry_price * 100

        # 买入时指标
        entry_adx = strategy._adx[buy_i]
        entry_pdi = strategy._pdi[buy_i]
        entry_mdi = strategy._mdi[buy_i]
        entry_bullish = strategy._is_ma_bullish(buy_i)
        is_entry_strong_trend = bool(
            entry_adx == entry_adx
            and entry_adx >= STRONG_TREND_ADX
            and entry_bullish
        )

        # 卖出时指标
        sell_adx = strategy._adx[sell_i]
        sell_pdi = strategy._pdi[sell_i]
        sell_mdi = strategy._mdi[sell_i]
        sell_bullish = strategy._is_ma_bullish(sell_i)
        is_ranging = sell_adx == sell_adx and sell_adx < ADX_RAPID_THRESHOLD
        is_strong_trend_sell = bool(
            sell_adx == sell_adx
            and sell_pdi == sell_pdi
            and sell_mdi == sell_mdi
            and sell_adx >= STRONG_TREND_ADX
            and sell_pdi > sell_mdi
        )

        # ATR
        strategy._entry_atr = strategy._calc_atr(buy_i)
        atr = strategy._entry_atr
        stop_mult = RAPID_STOP_MULTIPLIER if is_ranging else ATR_STOP_MULTIPLIER
        hard_stop = entry_price - stop_mult * atr

        # K 线形态
        cur_yin = exit_close < exit_open
        is_low_open = exit_open < prev_close * 0.99 if prev_close > 0 else False
        yin_drop_pct = (exit_open - exit_close) / exit_open * 100 if exit_open > 0 else 0
        atr_pct = atr / exit_open * 100 if exit_open > 0 else 3.0

        # 最高浮盈（模拟：检查买入到卖出期间的最高收盘价）
        highest_close = entry_price
        for j in range(buy_i, sell_i + 1):
            c = df["close"].iloc[j]
            if c > highest_close:
                highest_close = c
        max_profit = (highest_close - entry_price) / entry_price

        # 持仓天数
        holding_days = sell_i - buy_i

        print(f"\n{'='*60}")
        print(f"交易 {buy_date} -> {sell_date}")
        print(f"  买入价={entry_price:.2f}, 卖出价={exit_close:.2f}")
        print(f"  收益={profit_pct*100:.2f}%, 亏损={loss_pct:.2f}%")
        print(f"  最高收盘={highest_close:.2f}, 最高浮盈={max_profit*100:.2f}%")
        print(f"  持仓天数={holding_days}")
        print(f"  买入: ADX={entry_adx:.1f} +DI={entry_pdi:.1f} -DI={entry_mdi:.1f} 多头={entry_bullish}")
        print(f"  买入: is_entry_strong_trend={is_entry_strong_trend}")
        print(f"  卖出: ADX={sell_adx:.1f} +DI={sell_pdi:.1f} -DI={sell_mdi:.1f} 多头={sell_bullish}")
        print(f"  卖出: is_ranging={is_ranging}, is_strong_trend={is_strong_trend_sell}")
        print(f"  ATR={atr:.2f}, stop_mult={stop_mult}, hard_stop={hard_stop:.2f}")
        print(f"  触发ATR硬止损: {exit_close <= hard_stop}")
        print(f"  K线: open={exit_open:.2f} close={exit_close:.2f} 阴线={cur_yin}")
        print(f"  低开={is_low_open}, 阴线跌幅={yin_drop_pct:.2f}%")
        print(f"  最大亏损保护阈值: {MAX_LOSS_PCT}%")
        print(f"  触发最大亏损: {loss_pct >= MAX_LOSS_PCT}")
        # 震荡市时间止损
        hold_threshold = TREND_ENTRY_RAPID_HOLD_DAYS if (entry_adx >= ADX_RAPID_THRESHOLD and entry_bullish) else RAPID_MAX_HOLD_DAYS
        print(f"  震荡市时间止损: is_ranging={is_ranging} not_bullish={not sell_bullish} holding={holding_days}>={hold_threshold} profit={profit_pct*100:.2f}%<{RAPID_MIN_PROFIT_KEEP*100:.1f}%")
        print(f"  触发震荡市时间止损: {is_ranging and not sell_bullish and holding_days >= hold_threshold and profit_pct < RAPID_MIN_PROFIT_KEEP}")


if __name__ == "__main__":
    main()
