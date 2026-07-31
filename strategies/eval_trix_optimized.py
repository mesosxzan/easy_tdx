"""TRIX 交叉策略优化版 -- 全面评测脚本。

评测目标
========
对比 TRIX 原版策略（金叉买/死叉卖）与优化版策略（MA120 趋势过滤 +
均线发散波动率过滤 + 快线止盈/吊灯止损动态离场）在真实 A 股数据上的
表现差异，量化三重过滤机制的改进效果。

评测维度
========
1. 原版 vs 优化版：策略收益、胜率、交易笔数、夏普比率、最大回撤
2. vs 买入持有：超额收益、跑赢比例
3. 风险指标：最大回撤、Calmar 比率、Sortino 比率
4. 交易质量：盈亏比、平均持仓天数、单笔收益分布
5. 分行业/市值分组表现

数据源：MAC 客户端（通达信 MAC 协议），前复权日线。

用法::

    python strategies/eval_trix_optimized.py
    python strategies/eval_trix_optimized.py --count 1000 --top 5
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

# 触发内置策略注册（import 副作用）
import easy_tdx.backtest.strategies.builtin  # noqa: E402, F401
from easy_tdx.backtest.engine import BacktestEngine  # noqa: E402
from easy_tdx.backtest.strategies import get_registry  # noqa: E402
from easy_tdx.cli.conn import get_mac_client  # noqa: E402
from easy_tdx.mac.enums import Adjust, Period  # noqa: E402
from easy_tdx.models.enums import Market  # noqa: E402

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
KLINE_COUNT = 800
# 优化版 MA120 需要 120 根预热，原版 TRIX(12,20) 需要约 60 根
WARMUP_BARS = 120


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class StrategyMetrics:
    """单策略单股票的回测指标。"""

    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_holding_days: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    volatility: float = 0.0


@dataclass
class EvalResult:
    """单只股票的评测结果。"""

    symbol: str
    market: str
    name: str
    group: str

    # 原版 TRIX 策略结果
    orig: StrategyMetrics = field(default_factory=StrategyMetrics)

    # 优化版 TRIX 策略结果
    opt: StrategyMetrics = field(default_factory=StrategyMetrics)

    # 基准
    buy_hold_return: float = 0.0

    # 衍生
    orig_excess: float = 0.0
    opt_excess: float = 0.0
    improvement: float = 0.0  # 优化版收益 - 原版收益

    # 状态
    has_error: bool = False
    error_msg: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def compute_derived(self) -> None:
        self.orig_excess = self.orig.total_return - self.buy_hold_return
        self.opt_excess = self.opt.total_return - self.buy_hold_return
        self.improvement = self.opt.total_return - self.orig.total_return


@dataclass
class GroupSummary:
    """分组汇总。"""

    name: str
    results: list[EvalResult] = field(default_factory=list)

    orig_avg_return: float = 0.0
    opt_avg_return: float = 0.0
    avg_buy_hold: float = 0.0

    orig_avg_drawdown: float = 0.0
    opt_avg_drawdown: float = 0.0
    orig_avg_sharpe: float = 0.0
    opt_avg_sharpe: float = 0.0

    orig_avg_win_rate: float = 0.0
    opt_avg_win_rate: float = 0.0

    orig_total_trades: int = 0
    opt_total_trades: int = 0

    orig_beat_count: int = 0
    opt_beat_count: int = 0

    opt_better_count: int = 0  # 优化版优于原版的数量
    avg_improvement: float = 0.0

    valid_count: int = 0

    def compute(self) -> None:
        valid = [r for r in self.results if not r.has_error and not r.skipped]
        self.valid_count = len(valid)
        if not valid:
            return
        n = len(valid)

        self.orig_avg_return = sum(r.orig.total_return for r in valid) / n
        self.opt_avg_return = sum(r.opt.total_return for r in valid) / n
        self.avg_buy_hold = sum(r.buy_hold_return for r in valid) / n

        self.orig_avg_drawdown = sum(r.orig.max_drawdown for r in valid) / n
        self.opt_avg_drawdown = sum(r.opt.max_drawdown for r in valid) / n
        self.orig_avg_sharpe = sum(r.orig.sharpe for r in valid) / n
        self.opt_avg_sharpe = sum(r.opt.sharpe for r in valid) / n

        self.orig_avg_win_rate = sum(r.orig.win_rate for r in valid) / n
        self.opt_avg_win_rate = sum(r.opt.win_rate for r in valid) / n

        self.orig_total_trades = sum(r.orig.total_trades for r in valid)
        self.opt_total_trades = sum(r.opt.total_trades for r in valid)

        self.orig_beat_count = sum(1 for r in valid if r.orig_excess > 0)
        self.opt_beat_count = sum(1 for r in valid if r.opt_excess > 0)

        self.opt_better_count = sum(1 for r in valid if r.improvement > 0)
        self.avg_improvement = sum(r.improvement for r in valid) / n


# ── 核心函数 ──────────────────────────────────────────────────────────────────


def _market_from_code(market_str: str) -> Market:
    market_map = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
    return market_map.get(market_str, Market.SH)


def compute_buy_hold(df: Any) -> float:
    closes = df["close"].values
    if len(closes) < 2:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0.0


def _extract_metrics(perf: dict[str, Any]) -> StrategyMetrics:
    """从 performance dict 提取关键指标。"""
    return StrategyMetrics(
        total_return=perf.get("total_return", 0.0),
        annual_return=perf.get("annual_return", 0.0),
        max_drawdown=perf.get("max_drawdown", 0.0),
        sharpe=perf.get("sharpe", 0.0),
        sortino=perf.get("sortino", 0.0),
        calmar=perf.get("calmar", 0.0),
        win_rate=perf.get("win_rate", 0.0),
        profit_factor=perf.get("profit_factor", 0.0),
        total_trades=perf.get("total_trades", 0),
        avg_holding_days=perf.get("avg_holding_days", 0.0),
        avg_win=perf.get("avg_win", 0.0),
        avg_loss=perf.get("avg_loss", 0.0),
        volatility=perf.get("volatility", 0.0),
    )


def run_single_backtest(
    strategy_cls: type,
    df: Any,
    cash: float,
    warmup_bars: int,
    strategy_name: str,
) -> StrategyMetrics:
    """运行单次回测并返回指标。"""
    engine = BacktestEngine(
        strategy=strategy_cls,
        cash=cash,
        commission=COMMISSION,
        warmup_bars=warmup_bars,
    )
    result = engine.run(df)
    return _extract_metrics(result.performance)


def run_evaluation(
    stocks: list[tuple[str, str, str, str]],
    count: int,
    cash: float,
    warmup_bars: int,
) -> list[EvalResult]:
    """对所有标的运行评测。"""
    results: list[EvalResult] = []
    total = len(stocks)

    # 从注册表获取策略类
    registry = get_registry()
    orig_entry = registry.get("trix")
    opt_entry = registry.get("trix_cross_optimized")
    orig_cls = orig_entry.strategy_cls
    opt_cls = opt_entry.strategy_cls

    # 优化版自带 default_warmup_bars=120，原版用传入的 warmup_bars
    orig_warmup = max(warmup_bars, 60)

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

                # 原版 TRIX 策略
                er.orig = run_single_backtest(
                    orig_cls, df, cash, orig_warmup, "trix"
                )

                # 优化版 TRIX 策略
                er.opt = run_single_backtest(
                    opt_cls, df, cash, warmup_bars, "trix_cross_optimized"
                )

                er.compute_derived()

                print(
                    f"原版={er.orig.total_return * 100:+7.2f}% "
                    f"优化={er.opt.total_return * 100:+7.2f}% "
                    f"持有={er.buy_hold_return * 100:+7.2f}% "
                    f"改进={er.improvement * 100:+6.2f}pp "
                    f"交易(原/优)={er.orig.total_trades}/{er.opt.total_trades}"
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
    print(f"\n{'=' * 170}")
    print("  逐股明细：原版 TRIX vs 优化版 TRIX vs 买入持有")
    print(f"{'=' * 170}")

    header = (
        f"{'股票':<12} {'代码':<8} {'分组':<8} "
        f"{'原版收益':>9} {'优化收益':>9} {'买入持有':>9} "
        f"{'改进':>8} {'原版回撤':>8} {'优化回撤':>8} "
        f"{'原版夏普':>8} {'优化夏普':>8} "
        f"{'原版胜率':>8} {'优化胜率':>8} "
        f"{'原版交易':>6} {'优化交易':>6}"
    )
    print(header)
    print("-" * 170)

    for r in results:
        if r.has_error or r.skipped:
            status = "错误" if r.has_error else "跳过"
            print(
                f"{r.name:<12} {r.symbol:<8} {r.group:<8} "
                f"{'N/A':>9} {'N/A':>9} {'N/A':>9} "
                f"{'N/A':>8} {'N/A':>8} {'N/A':>8} "
                f"{'N/A':>8} {'N/A':>8} "
                f"{'N/A':>8} {'N/A':>8} "
                f"{'N/A':>6} {'N/A':>6} {status}"
            )
            continue

        print(
            f"{r.name:<12} {r.symbol:<8} {r.group:<8} "
            f"{r.orig.total_return * 100:>+8.2f}% {r.opt.total_return * 100:>+8.2f}% "
            f"{r.buy_hold_return * 100:>+8.2f}% "
            f"{r.improvement * 100:>+7.2f}pp "
            f"{r.orig.max_drawdown * 100:>7.2f}% {r.opt.max_drawdown * 100:>7.2f}% "
            f"{r.orig.sharpe:>8.3f} {r.opt.sharpe:>8.3f} "
            f"{r.orig.win_rate * 100:>7.1f}% {r.opt.win_rate * 100:>7.1f}% "
            f"{r.orig.total_trades:>6} {r.opt.total_trades:>6}"
        )


def print_group_summary(groups: list[GroupSummary]) -> None:
    print(f"\n{'=' * 140}")
    print("  分组汇总")
    print(f"{'=' * 140}")

    header = (
        f"{'分组':<10} {'数量':>4} "
        f"{'原版均收':>10} {'优化均收':>10} {'买入持有':>10} "
        f"{'平均改进':>9} {'原版回撤':>9} {'优化回撤':>9} "
        f"{'原版夏普':>9} {'优化夏普':>9} "
        f"{'原版交易':>7} {'优化交易':>7} "
        f"{'原版跑赢':>8} {'优化跑赢':>8} {'优化更优':>8}"
    )
    print(header)
    print("-" * 140)

    for gs in groups:
        print(
            f"{gs.name:<10} {gs.valid_count:>4} "
            f"{gs.orig_avg_return * 100:>+9.2f}% {gs.opt_avg_return * 100:>+9.2f}% "
            f"{gs.avg_buy_hold * 100:>+9.2f}% "
            f"{gs.avg_improvement * 100:>+8.2f}pp "
            f"{gs.orig_avg_drawdown * 100:>8.2f}% {gs.opt_avg_drawdown * 100:>8.2f}% "
            f"{gs.orig_avg_sharpe:>9.3f} {gs.opt_avg_sharpe:>9.3f} "
            f"{gs.orig_total_trades:>7} {gs.opt_total_trades:>7} "
            f"{gs.orig_beat_count:>4}/{gs.valid_count:<3} "
            f"{gs.opt_beat_count:>4}/{gs.valid_count:<3} "
            f"{gs.opt_better_count:>4}/{gs.valid_count:<3}"
        )


def print_overall_summary(results: list[EvalResult]) -> None:
    valid = [r for r in results if not r.has_error and not r.skipped]

    print(f"\n{'=' * 100}")
    print("  ★ 全局汇总：原版 TRIX vs 优化版 TRIX ★")
    print(f"{'=' * 100}")

    if not valid:
        print("  无有效结果")
        return

    n = len(valid)

    # ── 核心指标对比 ──
    orig_avg_ret = sum(r.orig.total_return for r in valid) / n
    opt_avg_ret = sum(r.opt.total_return for r in valid) / n
    bh_avg_ret = sum(r.buy_hold_return for r in valid) / n
    orig_avg_dd = sum(r.orig.max_drawdown for r in valid) / n
    opt_avg_dd = sum(r.opt.max_drawdown for r in valid) / n
    orig_avg_sharpe = sum(r.orig.sharpe for r in valid) / n
    opt_avg_sharpe = sum(r.opt.sharpe for r in valid) / n
    orig_avg_sortino = sum(r.orig.sortino for r in valid) / n
    opt_avg_sortino = sum(r.opt.sortino for r in valid) / n
    orig_avg_calmar = sum(r.orig.calmar for r in valid) / n
    opt_avg_calmar = sum(r.opt.calmar for r in valid) / n
    orig_avg_win = sum(r.orig.win_rate for r in valid) / n
    opt_avg_win = sum(r.opt.win_rate for r in valid) / n
    orig_avg_pf = sum(r.orig.profit_factor for r in valid) / n
    opt_avg_pf = sum(r.opt.profit_factor for r in valid) / n
    orig_total_trades = sum(r.orig.total_trades for r in valid)
    opt_total_trades = sum(r.opt.total_trades for r in valid)
    orig_avg_hold = sum(r.orig.avg_holding_days for r in valid) / n
    opt_avg_hold = sum(r.opt.avg_holding_days for r in valid) / n
    orig_avg_vol = sum(r.orig.volatility for r in valid) / n
    opt_avg_vol = sum(r.opt.volatility for r in valid) / n

    orig_positive = sum(1 for r in valid if r.orig.total_return > 0)
    opt_positive = sum(1 for r in valid if r.opt.total_return > 0)
    orig_beat = sum(1 for r in valid if r.orig_excess > 0)
    opt_beat = sum(1 for r in valid if r.opt_excess > 0)
    opt_better = sum(1 for r in valid if r.improvement > 0)

    headers = (
        f"  {'指标':<20} {'原版TRIX':>15} {'优化版TRIX':>15} "
        f"{'买入持有':>15} {'差异(优-原)':>12}"
    )
    print(f"\n{headers}")
    print(f"  {'-' * 80}")
    print(
        f"  {'平均收益率':<20} {orig_avg_ret * 100:>+14.2f}% {opt_avg_ret * 100:>+14.2f}% "
        f"{bh_avg_ret * 100:>+14.2f}% {(opt_avg_ret - orig_avg_ret) * 100:>+11.2f}pp"
    )
    print(
        f"  {'正收益比例':<20} {orig_positive}/{n} ({orig_positive / n * 100:>5.1f}%)  "
        f"{opt_positive}/{n} ({opt_positive / n * 100:>5.1f}%)"
    )
    print(
        f"  {'跑赢买入持有':<20} {orig_beat}/{n} ({orig_beat / n * 100:>5.1f}%)  "
        f"{opt_beat}/{n} ({opt_beat / n * 100:>5.1f}%)"
    )
    print(
        f"  {'优化版优于原版':<20} {'':>15} {opt_better}/{n} ({opt_better / n * 100:>5.1f}%)"
    )
    print(
        f"  {'平均最大回撤':<20} {orig_avg_dd * 100:>14.2f}% {opt_avg_dd * 100:>14.2f}% "
        f"{'':>15} {(opt_avg_dd - orig_avg_dd) * 100:>+11.2f}pp"
    )
    print(
        f"  {'平均夏普比率':<20} {orig_avg_sharpe:>15.3f} {opt_avg_sharpe:>15.3f} "
        f"{'':>15} {opt_avg_sharpe - orig_avg_sharpe:>+12.3f}"
    )
    print(
        f"  {'平均Sortino比率':<20} {orig_avg_sortino:>15.3f} {opt_avg_sortino:>15.3f} "
        f"{'':>15} {opt_avg_sortino - orig_avg_sortino:>+12.3f}"
    )
    print(
        f"  {'平均Calmar比率':<20} {orig_avg_calmar:>15.3f} {opt_avg_calmar:>15.3f} "
        f"{'':>15} {opt_avg_calmar - orig_avg_calmar:>+12.3f}"
    )
    print(
        f"  {'平均胜率':<20} {orig_avg_win * 100:>14.1f}% {opt_avg_win * 100:>14.1f}% "
        f"{'':>15} {(opt_avg_win - orig_avg_win) * 100:>+11.1f}pp"
    )
    print(
        f"  {'平均盈亏比':<20} {orig_avg_pf:>15.2f} {opt_avg_pf:>15.2f} "
        f"{'':>15} {opt_avg_pf - orig_avg_pf:>+12.2f}"
    )
    print(
        f"  {'总交易笔数':<20} {orig_total_trades:>15} {opt_total_trades:>15} "
        f"{'':>15} {opt_total_trades - orig_total_trades:>+12}"
    )
    print(
        f"  {'平均持仓天数':<20} {orig_avg_hold:>15.1f} {opt_avg_hold:>15.1f} "
        f"{'':>15} {opt_avg_hold - orig_avg_hold:>+12.1f}"
    )
    print(
        f"  {'平均年化波动率':<20} {orig_avg_vol * 100:>14.2f}% {opt_avg_vol * 100:>14.2f}% "
        f"{'':>15} {(opt_avg_vol - orig_avg_vol) * 100:>+11.2f}pp"
    )

    # ── 交易频率分析 ──
    print("\n  --- 交易频率分析 ---")
    if orig_total_trades > 0:
        trade_reduction = (orig_total_trades - opt_total_trades) / orig_total_trades * 100
        print(f"  原版总交易数:     {orig_total_trades}")
        print(f"  优化版总交易数:   {opt_total_trades}")
        print(f"  交易减少数:       {orig_total_trades - opt_total_trades}")
        print(f"  交易减少比例:     {trade_reduction:.1f}%")

    # ── 改进效果分析 ──
    print("\n  --- 优化改进效果分析 ---")
    avg_improvement = sum(r.improvement for r in valid) / n
    max_improvement = max(r.improvement for r in valid)
    min_improvement = min(r.improvement for r in valid)
    improvement_positive = sum(1 for r in valid if r.improvement > 0.01)
    improvement_negative = sum(1 for r in valid if r.improvement < -0.01)

    print(f"  平均收益改进:     {avg_improvement * 100:+.2f}pp")
    print(f"  最大收益改进:     {max_improvement * 100:+.2f}pp")
    print(f"  最小收益改进:     {min_improvement * 100:+.2f}pp")
    print(f"  优化版更好:       {improvement_positive}/{n} ({improvement_positive / n * 100:.1f}%)")
    print(f"  原版更好:         {improvement_negative}/{n} ({improvement_negative / n * 100:.1f}%)")
    print(f"  两者接近(±1%):    {n - improvement_positive - improvement_negative}/{n}")

    # ── 优化版收益率分布 ──
    print("\n  --- 优化版收益率分布 ---")
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
        cnt = sum(1 for r in valid if lo <= r.opt.total_return < hi)
        if cnt > 0:
            bar = "#" * cnt
            print(f"    {label:<14} {cnt:>3} {bar}")

    # ── 回撤改善分析 ──
    print("\n  --- 回撤改善分析 ---")
    dd_improved = sum(1 for r in valid if r.opt.max_drawdown < r.orig.max_drawdown)
    dd_worsened = sum(1 for r in valid if r.opt.max_drawdown > r.orig.max_drawdown)
    avg_dd_improvement = sum(
        r.orig.max_drawdown - r.opt.max_drawdown for r in valid
    ) / n
    print(f"  回撤改善的股票:   {dd_improved}/{n} ({dd_improved / n * 100:.1f}%)")
    print(f"  回撤恶化的股票:   {dd_worsened}/{n} ({dd_worsened / n * 100:.1f}%)")
    print(f"  平均回撤改善:     {avg_dd_improvement * 100:+.2f}pp")


def main(
    count: int = KLINE_COUNT,
    cash: float = CASH,
    warmup_bars: int = WARMUP_BARS,
) -> None:
    print("=" * 130)
    print("  TRIX 交叉策略优化版 -- 全面评测")
    print(f"  标的数: {len(EVAL_STOCKS)} 只 | K线: {count} 根 | "
          f"资金: {cash:,.0f} 元 | 预热: {warmup_bars} 根")
    print("  策略对比: 原版TRIX(金叉买/死叉卖) vs 优化版TRIX(MA120趋势+波动率+动态离场)")
    print("  数据源: MAC 客户端（通达信 MAC 协议），前复权日线")
    print("=" * 130)

    print(f"\n>> Step 1: 逐股双策略回测 ({len(EVAL_STOCKS)} 只)")
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
        description="TRIX 交叉策略优化版 -- 全面评测"
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
