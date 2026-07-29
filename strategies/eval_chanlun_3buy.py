"""缠论三类买点策略 -- 增量分析器全面评测脚本。

评测目标
========
在刚实现的 IncrementalAnalyser（彻底消除5层前瞻偏差）下，评测缠论3买策略的
真实表现，并与旧版批量分析器（仍有ZS前瞻偏差）对比，量化前瞻偏差的影响。

评测维度
========
1. 增量 vs 批量：策略收益、胜率、交易笔数、信号数量对比
2. vs 买入持有：超额收益、跑赢比例
3. 风险指标：最大回撤、夏普比率
4. 信号质量：每笔交易盈亏分布、持仓天数分布
5. 分行业/市值分组表现

用法::

    python strategies/eval_chanlun_3buy.py
    python strategies/eval_chanlun_3buy.py --count 500 --top 5
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.chanlun.analyser import ChanlunAnalyser  # noqa: E402
from easy_tdx.chanlun.incremental import IncrementalAnalyser  # noqa: E402
from easy_tdx.cli.conn import get_mac_client  # noqa: E402
from easy_tdx.mac.enums import Adjust, Period  # noqa: E402
from easy_tdx.models.enums import Market  # noqa: E402
from strategies.chanlun_3buy import Chanlun3BuyStrategy  # noqa: E402

# ── 评测标的池（覆盖不同行业/市值/风格） ─────────────────────────────────────

# 格式: (代码, 市场, 名称, 分组)
EVAL_STOCKS: list[tuple[str, str, str, str]] = [
    # 白酒/消费
    ("600519", "SH", "贵州茅台", "白酒消费"),
    ("000858", "SZ", "五粮液", "白酒消费"),
    ("000568", "SZ", "泸州老窖", "白酒消费"),
    ("002304", "SZ", "洋河股份", "白酒消费"),
    # 金融
    ("601318", "SH", "中国平安", "金融"),
    ("600036", "SH", "招商银行", "金融"),
    ("601166", "SH", "兴业银行", "金融"),
    ("000776", "SZ", "广发证券", "金融"),
    # 科技/电子
    ("000725", "SZ", "京东方A", "科技电子"),
    ("002475", "SZ", "立讯精密", "科技电子"),
    ("300059", "SZ", "东方财富", "科技电子"),
    ("002405", "SZ", "四维图新", "科技电子"),
    # 医药
    ("600276", "SH", "恒瑞医药", "医药"),
    ("000538", "SZ", "云南白药", "医药"),
    ("300015", "SZ", "爱尔眼科", "医药"),
    # 新能源/汽车
    ("300750", "SZ", "宁德时代", "新能源"),
    ("002594", "SZ", "比亚迪", "新能源"),
    ("601012", "SH", "隆基绿能", "新能源"),
    # 周期/资源
    ("601899", "SH", "紫金矿业", "周期资源"),
    ("600585", "SH", "海螺水泥", "周期资源"),
    ("601600", "SH", "中国铝业", "周期资源"),
    # 中小盘
    ("002407", "SZ", "多氟多", "中小盘"),
    ("300296", "SZ", "利亚德", "中小盘"),
    ("002236", "SZ", "大华股份", "中小盘"),
    # 大盘蓝筹
    ("601668", "SH", "中国建筑", "大盘蓝筹"),
    ("601111", "SH", "中国国航", "大盘蓝筹"),
    ("600028", "SH", "中国石化", "大盘蓝筹"),
    # 超跌/低价
    ("601666", "SH", "平煤股份", "超跌低价"),
    ("600206", "SH", "有研新材", "超跌低价"),
    ("000703", "SZ", "正虹科技", "超跌低价"),
]

# ── 回测参数 ──────────────────────────────────────────────────────────────────

CASH = 1_000_000.0
COMMISSION = 0.0003
WARMUP_BARS = 60
KLINE_COUNT = 800


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    """单只股票的评测结果。"""

    symbol: str
    market: str
    name: str
    group: str

    # 增量分析器结果
    incr_return: float = 0.0
    incr_drawdown: float = 0.0
    incr_sharpe: float = 0.0
    incr_win_rate: float = 0.0
    incr_trades: int = 0
    incr_signals: int = 0  # 3buy 信号数

    # 批量分析器结果
    batch_return: float = 0.0
    batch_drawdown: float = 0.0
    batch_sharpe: float = 0.0
    batch_win_rate: float = 0.0
    batch_trades: int = 0
    batch_signals: int = 0

    # 基准
    buy_hold_return: float = 0.0

    # 衍生
    incr_excess: float = 0.0
    batch_excess: float = 0.0
    lookahead_gain: float = 0.0  # 批量虚增 = 批量收益 - 增量收益

    # 状态
    has_error: bool = False
    error_msg: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def compute_derived(self) -> None:
        self.incr_excess = self.incr_return - self.buy_hold_return
        self.batch_excess = self.batch_return - self.buy_hold_return
        self.lookahead_gain = self.batch_return - self.incr_return


@dataclass
class GroupSummary:
    """分组汇总。"""

    name: str
    results: list[EvalResult] = field(default_factory=list)

    # 增量汇总
    incr_avg_return: float = 0.0
    incr_avg_drawdown: float = 0.0
    incr_avg_sharpe: float = 0.0
    incr_avg_win_rate: float = 0.0
    incr_total_trades: int = 0
    incr_beat_count: int = 0
    incr_positive_count: int = 0

    # 批量汇总
    batch_avg_return: float = 0.0
    batch_avg_win_rate: float = 0.0
    batch_total_trades: int = 0
    batch_beat_count: int = 0

    # 基准
    avg_buy_hold: float = 0.0

    valid_count: int = 0

    def compute(self) -> None:
        valid = [r for r in self.results if not r.has_error and not r.skipped]
        self.valid_count = len(valid)
        if not valid:
            return
        n = len(valid)
        self.incr_avg_return = sum(r.incr_return for r in valid) / n
        self.incr_avg_drawdown = sum(r.incr_drawdown for r in valid) / n
        self.incr_avg_sharpe = sum(r.incr_sharpe for r in valid) / n
        self.incr_avg_win_rate = sum(r.incr_win_rate for r in valid) / n
        self.incr_total_trades = sum(r.incr_trades for r in valid)
        self.incr_beat_count = sum(1 for r in valid if r.incr_excess > 0)
        self.incr_positive_count = sum(1 for r in valid if r.incr_return > 0)

        self.batch_avg_return = sum(r.batch_return for r in valid) / n
        self.batch_avg_win_rate = sum(r.batch_win_rate for r in valid) / n
        self.batch_total_trades = sum(r.batch_trades for r in valid)
        self.batch_beat_count = sum(1 for r in valid if r.batch_excess > 0)

        self.avg_buy_hold = sum(r.buy_hold_return for r in valid) / n


# ── 核心函数 ──────────────────────────────────────────────────────────────────


def _market_from_code(market_str: str) -> Market:
    market_map = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
    return market_map.get(market_str, Market.SH)


def compute_buy_hold(df: Any) -> float:
    closes = df["close"].values
    if len(closes) < 2:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0


def count_buy3_signals(chanlun_result: Any) -> int:
    """统计3buy信号数量。"""
    from easy_tdx.chanlun.types import MMDType

    # 优先使用 signals_by_bar（增量分析器）
    signals_by_bar = getattr(chanlun_result, "signals_by_bar", None)
    if signals_by_bar:
        count = 0
        for mmds in signals_by_bar.values():
            count += sum(1 for m in mmds if m.mmd_type == MMDType.BUY_3)
        return count

    # 回退：批量分析器的 mmds
    return sum(1 for m in chanlun_result.mmds if m.mmd_type == MMDType.BUY_3)


def run_backtest(
    df: Any,
    chanlun_result: Any | None,
    cash: float,
    warmup_bars: int,
) -> dict[str, Any]:
    """运行单次回测。"""
    Chanlun3BuyStrategy.stop_loss_pct = 10.0
    Chanlun3BuyStrategy.take_profit_pct = 0.0
    Chanlun3BuyStrategy.ma_exit_period = 0
    Chanlun3BuyStrategy.trailing_stop_pct = 0.0
    Chanlun3BuyStrategy.max_hold_days = 15
    Chanlun3BuyStrategy.use_chanlun_sell = True

    engine = BacktestEngine(
        strategy=Chanlun3BuyStrategy,
        cash=cash,
        commission=COMMISSION,
        chanlun_level=None,  # 不自动计算，手动注入
        warmup_bars=warmup_bars,
    )
    result = engine.run(df, chanlun_result=chanlun_result)
    perf = result.performance
    return {
        "total_return": perf.get("total_return", 0.0),
        "max_drawdown": perf.get("max_drawdown", 0.0),
        "sharpe_ratio": perf.get("sharpe_ratio", 0.0),
        "win_rate": perf.get("win_rate", 0.0),
        "trade_count": len(result.trades),
    }


def evaluate_single(
    df: Any,
    cash: float,
    warmup_bars: int,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    """对单只股票运行增量+批量两种回测。

    Returns:
        (incr_metrics, batch_metrics, incr_buy3_count, batch_buy3_count)
    """
    # 增量分析器
    incr_analyser = IncrementalAnalyser(frequency="DAILY")
    incr_result = incr_analyser.process_klines(df)
    incr_buy3 = count_buy3_signals(incr_result)
    incr_metrics = run_backtest(df, incr_result, cash, warmup_bars)

    # 批量分析器
    batch_analyser = ChanlunAnalyser()
    batch_result = batch_analyser.process_klines(df)
    batch_buy3 = count_buy3_signals(batch_result)
    batch_metrics = run_backtest(df, batch_result, cash, warmup_bars)

    return incr_metrics, batch_metrics, incr_buy3, batch_buy3


def run_evaluation(
    stocks: list[tuple[str, str, str, str]],
    count: int,
    cash: float,
    warmup_bars: int,
) -> list[EvalResult]:
    """对所有标的运行评测。"""
    results: list[EvalResult] = []
    total = len(stocks)

    with get_mac_client() as client:
        for idx, (symbol, market_str, name, group) in enumerate(stocks, 1):
            er = EvalResult(symbol=symbol, market=market_str, name=name, group=group)

            print(
                f"  [{idx}/{total}] {name}({symbol}.{market_str}) [{group}] ...",
                end=" ",
                flush=True,
            )

            try:
                market = _market_from_code(market_str)
                df = client.get_stock_kline(
                    market,
                    symbol,
                    period=Period.DAILY,
                    start=0,
                    count=count,
                    adjust=Adjust.QFQ,
                )

                if df is None or len(df) < warmup_bars + 20:
                    er.skipped = True
                    er.skip_reason = f"K线不足 ({len(df) if df is not None else 0} 根)"
                    print(f"SKIP ({er.skip_reason})")
                    results.append(er)
                    continue

                # 买入持有
                er.buy_hold_return = compute_buy_hold(df)

                # 双引擎评测
                incr_m, batch_m, incr_buy3, batch_buy3 = evaluate_single(df, cash, warmup_bars)

                er.incr_return = incr_m["total_return"]
                er.incr_drawdown = incr_m["max_drawdown"]
                er.incr_sharpe = incr_m["sharpe_ratio"]
                er.incr_win_rate = incr_m["win_rate"]
                er.incr_trades = incr_m["trade_count"]
                er.incr_signals = incr_buy3

                er.batch_return = batch_m["total_return"]
                er.batch_drawdown = batch_m["max_drawdown"]
                er.batch_sharpe = batch_m["sharpe_ratio"]
                er.batch_win_rate = batch_m["win_rate"]
                er.batch_trades = batch_m["trade_count"]
                er.batch_signals = batch_buy3

                er.compute_derived()

                print(
                    f"增量={er.incr_return * 100:+7.2f}% "
                    f"批量={er.batch_return * 100:+7.2f}% "
                    f"持有={er.buy_hold_return * 100:+7.2f}% "
                    f"虚增={er.lookahead_gain * 100:+6.2f}pp "
                    f"信号(增/批)={er.incr_signals}/{er.batch_signals}"
                )

            except Exception as exc:
                er.has_error = True
                er.error_msg = str(exc)
                print(f"ERROR ({exc})")

            results.append(er)

    return results


def group_results(results: list[EvalResult]) -> list[GroupSummary]:
    groups: dict[str, GroupSummary] = {}
    for r in results:
        if r.group not in groups:
            groups[r.group] = GroupSummary(name=r.group)
        groups[r.group].results.append(r)
    for gs in groups.values():
        gs.compute()
    return list(groups.values())


# ── 报表输出 ──────────────────────────────────────────────────────────────────


def print_detail_table(results: list[EvalResult]) -> None:
    print(f"\n{'=' * 150}")
    print("  逐股明细：增量 vs 批量 vs 买入持有")
    print(f"{'=' * 150}")

    header = (
        f"{'股票':<12} {'代码':<8} {'分组':<8} "
        f"{'增量收益':>9} {'批量收益':>9} {'买入持有':>9} "
        f"{'前瞻虚增':>8} {'增量回撤':>8} {'增量夏普':>8} "
        f"{'增量胜率':>8} {'增量交易':>6} {'批量交易':>6} "
        f"{'信号(增)':>7} {'信号(批)':>7}"
    )
    print(header)
    print("-" * 150)

    for r in results:
        if r.has_error or r.skipped:
            status = "错误" if r.has_error else "跳过"
            print(
                f"{r.name:<12} {r.symbol:<8} {r.group:<8} "
                f"{'N/A':>9} {'N/A':>9} {'N/A':>9} "
                f"{'N/A':>8} {'N/A':>8} {'N/A':>8} "
                f"{'N/A':>8} {'N/A':>6} {'N/A':>6} "
                f"{'N/A':>7} {'N/A':>7} {status}"
            )
            continue

        print(
            f"{r.name:<12} {r.symbol:<8} {r.group:<8} "
            f"{r.incr_return * 100:>+8.2f}% {r.batch_return * 100:>+8.2f}% "
            f"{r.buy_hold_return * 100:>+8.2f}% "
            f"{r.lookahead_gain * 100:>+7.2f}pp "
            f"{r.incr_drawdown * 100:>7.2f}% {r.incr_sharpe:>8.3f} "
            f"{r.incr_win_rate * 100:>7.1f}% {r.incr_trades:>6} {r.batch_trades:>6} "
            f"{r.incr_signals:>7} {r.batch_signals:>7}"
        )


def print_group_summary(groups: list[GroupSummary]) -> None:
    print(f"\n{'=' * 130}")
    print("  分组汇总")
    print(f"{'=' * 130}")

    header = (
        f"{'分组':<10} {'数量':>4} "
        f"{'增量均收':>10} {'批量均收':>10} {'买入持有':>10} "
        f"{'前瞻虚增':>9} {'增量回撤':>9} {'增量夏普':>9} "
        f"{'增量胜率':>9} {'增量交易':>7} {'批量交易':>7} "
        f"{'增量跑赢':>8} {'批量跑赢':>8}"
    )
    print(header)
    print("-" * 130)

    for gs in groups:
        print(
            f"{gs.name:<10} {gs.valid_count:>4} "
            f"{gs.incr_avg_return * 100:>+9.2f}% {gs.batch_avg_return * 100:>+9.2f}% "
            f"{gs.avg_buy_hold * 100:>+9.2f}% "
            f"{(gs.batch_avg_return - gs.incr_avg_return) * 100:>+8.2f}pp "
            f"{gs.incr_avg_drawdown * 100:>8.2f}% {gs.incr_avg_sharpe:>9.3f} "
            f"{gs.incr_avg_win_rate * 100:>8.1f}% "
            f"{gs.incr_total_trades:>7} {gs.batch_total_trades:>7} "
            f"{gs.incr_beat_count:>4}/{gs.valid_count:<3} "
            f"{gs.batch_beat_count:>4}/{gs.valid_count:<3}"
        )


def print_overall_summary(results: list[EvalResult]) -> None:
    valid = [r for r in results if not r.has_error and not r.skipped]

    print(f"\n{'=' * 100}")
    print("  ★ 全局汇总：增量分析器 vs 批量分析器 ★")
    print(f"{'=' * 100}")

    if not valid:
        print("  无有效结果")
        return

    n = len(valid)

    # ── 核心指标对比 ──
    incr_avg_ret = sum(r.incr_return for r in valid) / n
    batch_avg_ret = sum(r.batch_return for r in valid) / n
    bh_avg_ret = sum(r.buy_hold_return for r in valid) / n
    incr_avg_dd = sum(r.incr_drawdown for r in valid) / n
    incr_avg_sharpe = sum(r.incr_sharpe for r in valid) / n
    incr_avg_win = sum(r.incr_win_rate for r in valid) / n
    batch_avg_win = sum(r.batch_win_rate for r in valid) / n
    incr_total_trades = sum(r.incr_trades for r in valid)
    batch_total_trades = sum(r.batch_trades for r in valid)
    incr_total_signals = sum(r.incr_signals for r in valid)
    batch_total_signals = sum(r.batch_signals for r in valid)

    incr_positive = sum(1 for r in valid if r.incr_return > 0)
    batch_positive = sum(1 for r in valid if r.batch_return > 0)
    incr_beat = sum(1 for r in valid if r.incr_excess > 0)
    batch_beat = sum(1 for r in valid if r.batch_excess > 0)

    headers = (
        f"  {'指标':<20} {'增量分析器':>15} {'批量分析器':>15} "
        f"{'买入持有':>15} {'差异(批-增)':>12}"
    )
    print(f"\n{headers}")
    print(f"  {'-' * 80}")
    print(
        f"  {'平均收益率':<20} {incr_avg_ret * 100:>+14.2f}% {batch_avg_ret * 100:>+14.2f}% "
        f"{bh_avg_ret * 100:>+14.2f}% {(batch_avg_ret - incr_avg_ret) * 100:>+11.2f}pp"
    )
    incr_pos_pct = incr_positive / n * 100
    batch_pos_pct = batch_positive / n * 100
    print(
        f"  {'正收益比例':<20} {incr_positive}/{n} ({incr_pos_pct:>5.1f}%)  "
        f"{batch_positive}/{n} ({batch_pos_pct:>5.1f}%)"
    )
    incr_beat_pct = incr_beat / n * 100
    batch_beat_pct = batch_beat / n * 100
    print(
        f"  {'跑赢买入持有':<20} {incr_beat}/{n} ({incr_beat_pct:>5.1f}%)  "
        f"{batch_beat}/{n} ({batch_beat_pct:>5.1f}%)"
    )
    print(
        f"  {'平均最大回撤':<20} {incr_avg_dd * 100:>14.2f}% {'':>15}"
    )
    print(
        f"  {'平均夏普比率':<20} {incr_avg_sharpe:>15.3f} {'':>15}"
    )
    print(
        f"  {'平均胜率':<20} {incr_avg_win * 100:>14.1f}% {batch_avg_win * 100:>14.1f}% "
        f"{'':>15} {(batch_avg_win - incr_avg_win) * 100:>+11.1f}pp"
    )
    print(
        f"  {'总交易笔数':<20} {incr_total_trades:>15} {batch_total_trades:>15} "
        f"{'':>15} {batch_total_trades - incr_total_trades:>+12}"
    )
    print(
        f"  {'总3buy信号数':<20} {incr_total_signals:>15} {batch_total_signals:>15} "
        f"{'':>15} {batch_total_signals - incr_total_signals:>+12}"
    )

    # ── 前瞻偏差影响分析 ──
    print("\n  --- 前瞻偏差影响分析 ---")
    avg_lookahead = sum(r.lookahead_gain for r in valid) / n
    max_lookahead = max(r.lookahead_gain for r in valid)
    min_lookahead = min(r.lookahead_gain for r in valid)
    lookahead_positive = sum(1 for r in valid if r.lookahead_gain > 0.01)
    lookahead_negative = sum(1 for r in valid if r.lookahead_gain < -0.01)

    print(f"  平均前瞻虚增:     {avg_lookahead * 100:+.2f}pp")
    print(f"  最大前瞻虚增:     {max_lookahead * 100:+.2f}pp")
    print(f"  最小前瞻虚增:     {min_lookahead * 100:+.2f}pp")
    print(f"  批量优于增量:     {lookahead_positive}/{n} ({lookahead_positive / n * 100:.1f}%)")
    print(f"  增量优于批量:     {lookahead_negative}/{n} ({lookahead_negative / n * 100:.1f}%)")
    print(f"  两者接近(±1%):    {n - lookahead_positive - lookahead_negative}/{n}")

    # ── 增量策略收益率分布 ──
    print("\n  --- 增量策略收益率分布 ---")
    brackets = [
        (-999, -0.30, "< -30%"),
        (-0.30, -0.10, "-30% ~ -10%"),
        (-0.10, 0.0, "-10% ~ 0%"),
        (0.0, 0.10, "0% ~ 10%"),
        (0.10, 0.30, "10% ~ 30%"),
        (0.30, 0.50, "30% ~ 50%"),
        (0.50, 0.80, "50% ~ 80%"),
        (0.80, 999, "> 80%"),
    ]
    for lo, hi, label in brackets:
        cnt = sum(1 for r in valid if lo <= r.incr_return < hi)
        if cnt > 0:
            bar = "#" * cnt
            print(f"    {label:<14} {cnt:>3} {bar}")

    # ── 信号过滤效果 ──
    print("\n  --- 信号过滤效果（增量 vs 批量）---")
    if batch_total_signals > 0:
        filter_ratio = (batch_total_signals - incr_total_signals) / batch_total_signals * 100
        print(f"  批量3buy信号总数:  {batch_total_signals}")
        print(f"  增量3buy信号总数:  {incr_total_signals}")
        print(f"  过滤掉信号数:      {batch_total_signals - incr_total_signals}")
        print(f"  信号过滤比例:      {filter_ratio:.1f}%")
    if batch_total_trades > 0:
        trade_filter = (batch_total_trades - incr_total_trades) / batch_total_trades * 100
        print(f"  批量交易总数:      {batch_total_trades}")
        print(f"  增量交易总数:      {incr_total_trades}")
        print(f"  交易减少比例:      {trade_filter:.1f}%")


def main(
    count: int = KLINE_COUNT,
    cash: float = CASH,
    warmup_bars: int = WARMUP_BARS,
) -> None:
    print("=" * 130)
    print("  缠论三类买点策略 -- 增量分析器全面评测")
    print(f"  标的数: {len(EVAL_STOCKS)} 只 | K线: {count} 根 | "
          f"资金: {cash:,.0f} 元 | 预热: {warmup_bars} 根")
    print("  策略参数: stop_loss=10%, max_hold_days=15, use_chanlun_sell=True")
    print("  分析器: IncrementalAnalyser（增量，无前瞻偏差）"
          " vs ChanlunAnalyser（批量，有ZS前瞻偏差）")
    print("=" * 130)

    print(f"\n>> Step 1: 逐股双引擎回测 ({len(EVAL_STOCKS)} 只)")
    t0 = time.time()
    results = run_evaluation(EVAL_STOCKS, count, cash, warmup_bars)
    print(f"\n  回测完成 (耗时 {time.time() - t0:.1f}s)")

    print("\n>> Step 2: 汇总分析")
    groups = group_results(results)

    print_detail_table(results)
    print_group_summary(groups)
    print_overall_summary(results)

    print(f"\n{'=' * 130}")
    print("  评测完成")
    print(f"{'=' * 130}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="缠论三类买点策略 -- 增量分析器全面评测"
    )
    parser.add_argument(
        "--count", type=int, default=KLINE_COUNT,
        help=f"K线数量 (默认 {KLINE_COUNT})",
    )
    parser.add_argument(
        "--cash", type=float, default=CASH,
        help=f"初始资金 (默认 {CASH:,.0f})",
    )
    parser.add_argument(
        "--warmup-bars", type=int, default=WARMUP_BARS,
        help=f"预热K线数 (默认 {WARMUP_BARS})",
    )
    args = parser.parse_args()

    main(count=args.count, cash=args.cash, warmup_bars=args.warmup_bars)
