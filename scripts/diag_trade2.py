# ruff: noqa: E501
"""诊断 603127 #2 持仓期间盈利锁定状态。"""

from __future__ import annotations

import sys

CODE = "603127"
COUNT = 500

sys.path.insert(0, "strategies")
from shadow_yang_v2 import (  # noqa: E402
    ShadowYangStrategy as Strategy,
    STRONG_TREND_ADX,
    ADX_RAPID_THRESHOLD,
    PROFIT_LOCKIN_TRIGGER1,
    PROFIT_LOCKIN_TRIGGER2,
    PROFIT_LOCKIN_TRIGGER3,
    PROFIT_LOCKIN_FLOOR2,
    PROFIT_LOCKIN_FLOOR3,
)


def fetch_data():
    """获取 K 线数据。"""
    from easy_tdx import Market, Period
    from easy_tdx.mac.client import MacClient

    client = MacClient.from_best_host()
    try:
        client.connect()
        df = client.get_stock_kline(Market.SH, CODE, period=Period.DAILY, start=0, count=COUNT)
    finally:
        client.close()
    return df


def main() -> None:
    df = fetch_data()
    strategy = Strategy()
    strategy._bind_data(df)
    strategy._call_init()

    # #2: 2025-03-14 买入 @21.87 -> 2025-03-31 卖出
    buy_date = "2025-03-14"
    sell_date = "2025-03-31"

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
        return

    entry_price = df["close"].iloc[buy_i]
    entry_adx = strategy._adx[buy_i]
    entry_bullish = strategy._is_ma_bullish(buy_i)
    is_entry_strong_trend = bool(
        entry_adx == entry_adx and entry_adx >= STRONG_TREND_ADX and entry_bullish
    )

    print(f"=== #2 持仓诊断: {buy_date} -> {sell_date} ===")
    print(f"买入价={entry_price:.2f}, 买入 ADX={entry_adx:.1f}, 多头={entry_bullish}")
    print(f"is_entry_strong_trend={is_entry_strong_trend}")
    print()

    # 遍历持仓期间每一天
    highest_close = entry_price
    for j in range(buy_i, sell_i + 1):
        dt = str(df["datetime"].iloc[j])[:10]
        close = df["close"].iloc[j]
        adx = strategy._adx[j]
        pdi = strategy._pdi[j]
        mdi = strategy._mdi[j]
        bullish = strategy._is_ma_bullish(j)

        # is_strong_trend (卖出时实时判断)
        is_strong_trend = bool(
            adx == adx and pdi == pdi and mdi == mdi
            and adx >= STRONG_TREND_ADX and pdi > mdi
        )

        # 最高浮盈
        if close > highest_close:
            highest_close = close
        max_profit = (highest_close - entry_price) / entry_price

        # profit_floor 计算
        profit_floor = strategy._calc_profit_floor(is_strong_trend, is_entry_strong_trend)
        profit_floor_price = entry_price * (1 + profit_floor) if profit_floor > 0 else 0

        # 是否触发盈利锁定
        trigger_lock = profit_floor > 0 and close <= profit_floor_price

        print(
            f"{dt} | C:{close:.2f} | ADX:{adx:.1f} +DI:{pdi:.1f} -DI:{mdi:.1f} "
            f"| 多头:{bullish} ST:{is_strong_trend} "
            f"| maxP:{max_profit*100:.2f}% "
            f"| floor:{profit_floor*100:.1f}%({profit_floor_price:.2f}) "
            f"| 触发:{trigger_lock}"
        )

    print()
    print("=== 常量参考 ===")
    print(f"STRONG_TREND_ADX={STRONG_TREND_ADX}")
    print(f"ADX_RAPID_THRESHOLD={ADX_RAPID_THRESHOLD}")
    print(f"Tier1: trigger={PROFIT_LOCKIN_TRIGGER1} floor=0.0(保本)")
    print(f"Tier2: trigger={PROFIT_LOCKIN_TRIGGER2} floor={PROFIT_LOCKIN_FLOOR2}")
    print(f"Tier3: trigger={PROFIT_LOCKIN_TRIGGER3} floor={PROFIT_LOCKIN_FLOOR3}")


if __name__ == "__main__":
    main()
