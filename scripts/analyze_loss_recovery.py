"""亏损单卖出后 10 个交易日恢复概率分析。

对当前策略回测产生的亏损单（profit_pct < 0），计算如果继续持有，
在卖出后 10 个交易日内是否能扭亏为盈（最高价 >= 买入价）。

分析维度：
  - 整体恢复概率
  - 按卖出原因分组（止损清仓 / 盈利锁定 / 震荡市时间止损）
  - 按亏损幅度分组
  - 恢复所需天数分布
  - 10 日内最大潜在收益分布

用法::

    python scripts/analyze_loss_recovery.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period  # noqa: E402
from easy_tdx.mac.client import MacClient  # noqa: E402

# ── 配置 ────────────────────────────────────────────────────────────────────

CSV_PATH = PROJECT_ROOT / "scripts" / "factor_analysis_results.csv"
KLINE_COUNT = 800  # 足够覆盖卖出日后 10 个交易日
POST_SELL_DAYS = 10  # 卖出后观察的交易日数

# 股票池（与 analyze_factor_discrimination.py 一致，用于 market 映射）
STOCK_POOL: list[tuple[str, str]] = [
    ("600206", "SH"),
    ("002407", "SZ"),
    ("002156", "SZ"),
    ("600539", "SH"),
    ("000001", "SZ"),
    ("600519", "SH"),
    ("000858", "SZ"),
    ("600550", "SH"),
    ("600176", "SH"),
    ("600403", "SH"),
    ("300207", "SZ"),
    ("000725", "SZ"),
    ("601991", "SH"),
    ("600664", "SH"),
    ("000100", "SZ"),
    ("601678", "SH"),
    ("002185", "SZ"),
    ("600157", "SH"),
    ("601288", "SH"),
    ("600744", "SH"),
    ("002498", "SZ"),
    ("001301", "SZ"),
    ("600004", "SH"),
    ("301035", "SZ"),
    ("300199", "SZ"),
    ("002518", "SZ"),
    ("300821", "SZ"),
    ("603833", "SH"),
    ("002461", "SZ"),
    ("002402", "SZ"),
    ("600339", "SH"),
    ("603823", "SH"),
    ("920725", "BJ"),
    ("920699", "BJ"),
    ("603580", "SH"),
    ("301319", "SZ"),
    ("301520", "SZ"),
    ("603137", "SH"),
    ("002189", "SZ"),
    ("002853", "SZ"),
    ("600188", "SH"),
    ("603057", "SH"),
    ("002879", "SZ"),
    ("002270", "SZ"),
    ("600671", "SH"),
    ("002810", "SZ"),
    ("600729", "SH"),
    ("002265", "SZ"),
    ("920088", "BJ"),
    ("300164", "SZ"),
]


def _build_market_map() -> dict[str, str]:
    """构建 stock -> market 映射。"""
    return {code: market for code, market in STOCK_POOL}


def _find_kline_index(df: pd.DataFrame, target_date: pd.Timestamp) -> int:
    """根据日期在 K 线中定位索引，找不到时取最近日期。"""
    dt_col = pd.to_datetime(df["datetime"])
    matches = dt_col[dt_col == target_date].index.tolist()
    if matches:
        return int(matches[0])
    diffs = (dt_col - target_date).abs()
    return int(diffs.idxmin())


def _analyze_single_stock(
    client: MacClient,
    code: str,
    market: str,
    loss_trades: pd.DataFrame,
) -> list[dict]:
    """分析单只股票的所有亏损单。

    Returns:
        每笔亏损单的恢复分析结果列表
    """
    results: list[dict] = []

    df = client.get_stock_kline(
        parse_market(market),
        code,
        period=parse_period("DAILY"),
        start=0,
        count=KLINE_COUNT,
        adjust=parse_adjust("QFQ"),
    )
    if df is None or len(df) < 60:
        print(f"  [SKIP] {code}: K线数据不足")
        return results

    for _, trade in loss_trades.iterrows():
        sell_date = pd.Timestamp(trade["sell_date"])
        buy_price = float(trade["buy_price"])
        sell_price = float(trade["sell_price"])
        profit_pct = float(trade["profit_pct"])

        sell_idx = _find_kline_index(df, sell_date)

        # 卖出后 POST_SELL_DAYS 个交易日的 K 线
        post_start = sell_idx + 1
        post_end = min(post_start + POST_SELL_DAYS, len(df))
        post_segment = df.iloc[post_start:post_end]

        if post_segment.empty:
            print(f"  [SKIP] {code} {sell_date.date()}: 卖出日后无足够 K 线")
            continue

        actual_post_days = len(post_segment)
        max_high = float(post_segment["high"].max())

        # 是否扭亏为盈（最高价 >= 买入价）
        recovered = max_high >= buy_price
        # 10 日内最大潜在收益（相对买入价）
        max_potential_pct = (max_high - buy_price) / buy_price * 100
        # 卖出后最大涨幅（相对卖出价，衡量"卖早了"程度）
        post_gain_pct = (max_high - sell_price) / sell_price * 100

        # 恢复所需天数（如果恢复）
        recover_days = 0
        if recovered:
            for day_offset in range(actual_post_days):
                day_high = float(df.iloc[post_start + day_offset]["high"])
                if day_high >= buy_price:
                    recover_days = day_offset + 1
                    break

        results.append(
            {
                "stock": code,
                "buy_date": str(trade["buy_date"]).split(" ")[0],
                "sell_date": str(trade["sell_date"]).split(" ")[0],
                "buy_reason": trade["buy_reason"],
                "sell_reason": trade["sell_reason"],
                "buy_price": buy_price,
                "sell_price": sell_price,
                "loss_pct": profit_pct,
                "actual_post_days": actual_post_days,
                "recovered": recovered,
                "recover_days": recover_days,
                "max_potential_pct": max_potential_pct,
                "post_gain_pct": post_gain_pct,
            }
        )

    return results


def _print_results(results: list[dict]) -> None:
    """打印分析结果。"""
    if not results:
        print("无亏损单数据可分析")
        return

    df = pd.DataFrame(results)
    total = len(df)
    recovered_count = int(df["recovered"].sum())
    recover_rate = recovered_count / total * 100

    print(f"\n{'=' * 100}")
    print(f"亏损单卖出后 {POST_SELL_DAYS} 个交易日恢复概率分析")
    print(f"{'=' * 100}")
    print(f"\n  亏损单总数: {total} 笔")
    print(f"  10 日内扭亏为盈: {recovered_count} 笔 ({recover_rate:.1f}%)")
    print(f"  10 日内仍亏损: {total - recovered_count} 笔 ({100 - recover_rate:.1f}%)")
    print(f"  10 日内最大潜在收益均值: {df['max_potential_pct'].mean():+.2f}%")
    print(f"  10 日内最大潜在收益中位数: {df['max_potential_pct'].median():+.2f}%")
    print(f"  卖出后最大涨幅均值（相对卖出价）: {df['post_gain_pct'].mean():+.2f}%")

    # 按卖出原因分组
    print(f"\n{'=' * 100}")
    print("按卖出原因分组")
    print(f"{'=' * 100}")
    print(
        f"\n{'卖出原因':<16} {'笔数':>6} {'恢复笔数':>8} {'恢复概率':>10} "
        f"{'均潜在收益%':>12} {'均亏损%':>10}"
    )
    print("-" * 70)
    for reason in sorted(df["sell_reason"].unique()):
        subset = df[df["sell_reason"] == reason]
        r_count = int(subset["recovered"].sum())
        r_rate = r_count / len(subset) * 100
        avg_pot = subset["max_potential_pct"].mean()
        avg_loss = subset["loss_pct"].mean()
        print(
            f"{reason:<16} {len(subset):>6} {r_count:>8} {r_rate:>9.1f}% "
            f"{avg_pot:>+11.2f} {avg_loss:>+9.2f}"
        )

    # 按亏损幅度分组
    print(f"\n{'=' * 100}")
    print("按亏损幅度分组")
    print(f"{'=' * 100}")
    print(f"\n{'亏损幅度':<16} {'笔数':>6} {'恢复笔数':>8} {'恢复概率':>10} {'均潜在收益%':>12}")
    print("-" * 60)
    loss_bins = [(-999, -5), (-5, -3), (-3, -1), (-1, 0)]
    loss_labels = ["<= -5%", "(-5%, -3%]", "(-3%, -1%]", "(-1%, 0%)"]
    for (lo, hi), label in zip(loss_bins, loss_labels, strict=True):
        subset = df[(df["loss_pct"] > lo) & (df["loss_pct"] <= hi)]
        if subset.empty:
            continue
        r_count = int(subset["recovered"].sum())
        r_rate = r_count / len(subset) * 100
        avg_pot = subset["max_potential_pct"].mean()
        print(f"{label:<16} {len(subset):>6} {r_count:>8} {r_rate:>9.1f}% {avg_pot:>+11.2f}")

    # 恢复所需天数分布
    recovered_df = df[df["recovered"]]
    if not recovered_df.empty:
        print(f"\n{'=' * 100}")
        print(f"恢复所需天数分布（共 {len(recovered_df)} 笔恢复）")
        print(f"{'=' * 100}")
        print(f"\n{'天数':<10} {'笔数':>6} {'占比':>8} {'累计占比':>10}")
        print("-" * 40)
        for day in range(1, POST_SELL_DAYS + 1):
            count = int((recovered_df["recover_days"] == day).sum())
            if count == 0:
                continue
            pct = count / len(recovered_df) * 100
            cumul = int((recovered_df["recover_days"] <= day).sum())
            cumul_pct = cumul / len(recovered_df) * 100
            print(f"第 {day:>2} 天{'':<3} {count:>6} {pct:>7.1f}% {cumul_pct:>9.1f}%")

    # 恢复交易明细
    print(f"\n{'=' * 100}")
    print("亏损单恢复明细")
    print(f"{'=' * 100}")
    print(
        f"\n{'股票':<8} {'买入日':>12} {'卖出日':>12} {'卖出原因':<14} {'买入价':>8} "
        f"{'卖出价':>8} {'亏损%':>7} {'10日最高':>9} {'潜在%':>7} {'恢复':>5} {'天数':>5}"
    )
    print("-" * 110)
    for _, row in df.sort_values("loss_pct").iterrows():
        recover_str = "是" if row["recovered"] else "否"
        days_str = str(int(row["recover_days"])) if row["recovered"] else "-"
        print(
            f"{row['stock']:<8} {row['buy_date']:>12} {row['sell_date']:>12} "
            f"{row['sell_reason']:<14} {row['buy_price']:>8.2f} {row['sell_price']:>8.2f} "
            f"{row['loss_pct']:>+6.2f}% {row['max_potential_pct']:>+8.2f}% "
            f"{row['post_gain_pct']:>+6.2f}% {recover_str:>4} {days_str:>5}"
        )


def main() -> None:
    print("=" * 100)
    print(f"亏损单卖出后 {POST_SELL_DAYS} 个交易日恢复概率分析")
    print(f"数据源: {CSV_PATH.name}")
    print("=" * 100)

    # 读取亏损单（stock 列保持字符串，避免前导零丢失）
    trades_df = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype={"stock": str})
    trades_df["stock"] = trades_df["stock"].str.zfill(6)
    loss_trades = trades_df[trades_df["profit_pct"] < 0].copy()
    print(f"\n亏损单总数: {len(loss_trades)} 笔")

    market_map = _build_market_map()

    # 按股票分组
    loss_by_stock = {code: group for code, group in loss_trades.groupby("stock")}

    print(f"涉及股票: {len(loss_by_stock)} 只")
    print("\n正在连接行情服务器...")

    client = MacClient.from_best_host()
    all_results: list[dict] = []

    try:
        for code, group in loss_by_stock.items():
            market = market_map.get(code)
            if market is None:
                print(f"  [SKIP] {code}: 无 market 映射")
                continue
            print(f"  [{code}] 分析 {len(group)} 笔亏损单...")
            stock_results = _analyze_single_stock(client, code, market, group)
            all_results.extend(stock_results)
    finally:
        client.close()

    _print_results(all_results)

    # 保存结果
    if all_results:
        output_path = PROJECT_ROOT / "scripts" / "loss_recovery_analysis.csv"
        pd.DataFrame(all_results).to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"\n结果已保存至: {output_path}")


if __name__ == "__main__":
    main()
