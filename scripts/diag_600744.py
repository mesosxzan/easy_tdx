# ruff: noqa: E501
"""诊断 600744 #3 退化原因。"""

from __future__ import annotations

import sys

CODE = "600744"
COUNT = 500

sys.path.insert(0, "strategies")
from shadow_yang_v2 import (  # noqa: E402
    ShadowYangStrategy as Strategy,
    STRONG_TREND_ADX,
    ADX_RAPID_THRESHOLD,
    MAX_LOSS_PCT,
)


def fetch_data():
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

    # #3: 2025-05-06 买入 @4.86 -> 基线 2025-05-15 卖出 / 优化 2025-06-12 卖出
    buy_date = "2025-05-06"
    sell_date_baseline = "2025-05-15"
    sell_date_opt = "2025-06-12"

    buy_i = None
    sell_baseline_i = None
    sell_opt_i = None
    for i in range(len(df)):
        dt_str = str(df["datetime"].iloc[i])[:10]
        if dt_str == buy_date:
            buy_i = i
        if dt_str == sell_date_baseline:
            sell_baseline_i = i
        if dt_str == sell_date_opt:
            sell_opt_i = i

    if buy_i is None:
        print(f"无法找到 {buy_date}")
        return

    entry_price = df["close"].iloc[buy_i]
    entry_adx = strategy._adx[buy_i]
    is_entry_strong_trend = bool(
        entry_adx == entry_adx and entry_adx >= STRONG_TREND_ADX
    )

    print(f"=== 600744 #3 诊断 ===")
    print(f"买入日={buy_date}, 买入价={entry_price:.2f}")
    print(f"买入 ADX={entry_adx:.1f}, is_entry_strong_trend={is_entry_strong_trend}")
    print()

    # 遍历持仓期间每一天（到优化后卖出日）
    end_i = sell_opt_i if sell_opt_i else buy_i + 30
    highest_close = entry_price
    for j in range(buy_i, end_i + 1):
        dt = str(df["datetime"].iloc[j])[:10]
        close = df["close"].iloc[j]
        open_ = df["open"].iloc[j]
        adx = strategy._adx[j]
        pdi = strategy._pdi[j]
        mdi = strategy._mdi[j]
        bullish = strategy._is_ma_bullish(j)
        is_ranging = adx == adx and adx < ADX_RAPID_THRESHOLD

        if close > highest_close:
            highest_close = close
        max_profit = (highest_close - entry_price) / entry_price
        profit_pct = (close - entry_price) / entry_price
        loss_pct = (entry_price - close) / entry_price * 100

        # 盈利锁定
        profit_floor = strategy._calc_profit_floor(is_entry_strong_trend)
        profit_floor_price = entry_price * (1 + profit_floor) if profit_floor > 0 else 0
        trigger_lock = profit_floor > 0 and close <= profit_floor_price

        # 最大亏损
        trigger_max_loss = loss_pct >= MAX_LOSS_PCT

        # 震荡市时间止损
        hold_days = j - buy_i
        trigger_rapid_stop = (
            is_ranging and not bullish and not is_entry_strong_trend
            and hold_days >= 3 and profit_pct < 0.02
        )

        marker = ""
        if dt == sell_date_baseline:
            marker += " <== 基线卖出日"
        if dt == sell_date_opt:
            marker += " <== 优化卖出日"

        print(
            f"{dt} | O:{open_:.2f} C:{close:.2f} | ADX:{adx:.1f} "
            f"+DI:{pdi:.1f} -DI:{mdi:.1f} | 多头:{bullish} 震荡:{is_ranging} "
            f"| maxP:{max_profit*100:.2f}% P:{profit_pct*100:.2f}% L:{loss_pct:.2f}% "
            f"| floor:{profit_floor*100:.1f}%({profit_floor_price:.2f}) "
            f"| 锁定:{trigger_lock} 亏损:{trigger_max_loss} 震荡止:{trigger_rapid_stop}"
            f"{marker}"
        )


if __name__ == "__main__":
    main()
