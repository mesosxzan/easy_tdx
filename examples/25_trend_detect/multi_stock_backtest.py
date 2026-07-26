"""
多股票回测 & 策略优化
====================================
对 10 只 A 股（排除科创板 688）进行波段反转策略回测，
综合评估策略效果并进行参数优化。

评估维度：
  - 累计收益率 / 年化收益率
  - 胜率 / 盈亏比
  - 最大回撤
  - Sharpe 比率
  - 平均持仓天数 / 交易频率
  - 理论最大收益捕获率

优化方向：
  - 信号触发阈值 (30-50)
  - 最小信号间隔 (3-10 天)
  - 背离回看窗口 (20-40 天)

用法：
  python examples/25_trend_detect/multi_stock_backtest.py
  python examples/25_trend_detect/multi_stock_backtest.py --optimize
"""

from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 复用 swing_reversal 中的核心逻辑
import sys
sys.path.insert(0, "/Users/a1-6/project/easy_tdx/examples/25_trend_detect")
from swing_reversal import (
    ReversalSignal,
    SwingTrade,
    compute_indicators,
    detect_reversals,
    generate_swing_signals,
    compute_swing_trades,
    compute_max_theoretical_profit,
)


# ─────────────────────────────────────────────────────────────────────────
# 股票池定义（排除科创板 688）
# ─────────────────────────────────────────────────────────────────────────
STOCK_POOL = [
    {"code": "600519", "name": "贵州茅台", "sector": "白酒", "market": "SH"},
    {"code": "000001", "name": "平安银行", "sector": "金融", "market": "SZ"},
    {"code": "300750", "name": "宁德时代", "sector": "新能源", "market": "SZ"},
    {"code": "002594", "name": "比亚迪", "sector": "汽车", "market": "SZ"},
    {"code": "600036", "name": "招商银行", "sector": "银行", "market": "SH"},
    {"code": "000858", "name": "五粮液", "sector": "白酒", "market": "SZ"},
    {"code": "002475", "name": "立讯精密", "sector": "电子", "market": "SZ"},
    {"code": "600900", "name": "长江电力", "sector": "电力", "market": "SH"},
    {"code": "300059", "name": "东方财富", "sector": "互金", "market": "SZ"},
    {"code": "002415", "name": "海康威视", "sector": "安防", "market": "SZ"},
]


# ─────────────────────────────────────────────────────────────────────────
# 模拟数据生成（每只股票不同特征）
# ─────────────────────────────────────────────────────────────────────────
# 各股票走势特征参数：(基础价, 波动率, 趋势段数, 最大涨幅, 最大跌幅)
STOCK_PROFILES = {
    "600519": {"base": 1700, "vol": 0.018, "trends": 3, "max_up": 0.45, "max_dn": -0.25, "seed": 101},
    "000001": {"base": 12.0, "vol": 0.022, "trends": 4, "max_up": 0.55, "max_dn": -0.30, "seed": 202},
    "300750": {"base": 200, "vol": 0.030, "trends": 3, "max_up": 0.80, "max_dn": -0.40, "seed": 303},
    "002594": {"base": 250, "vol": 0.028, "trends": 3, "max_up": 0.70, "max_dn": -0.35, "seed": 404},
    "600036": {"base": 35.0, "vol": 0.016, "trends": 4, "max_up": 0.35, "max_dn": -0.20, "seed": 505},
    "000858": {"base": 150, "vol": 0.024, "trends": 3, "max_up": 0.50, "max_dn": -0.30, "seed": 606},
    "002475": {"base": 35.0, "vol": 0.026, "trends": 4, "max_up": 0.60, "max_dn": -0.35, "seed": 707},
    "600900": {"base": 28.0, "vol": 0.012, "trends": 3, "max_up": 0.30, "max_dn": -0.15, "seed": 808},
    "300059": {"base": 18.0, "vol": 0.032, "trends": 4, "max_up": 0.90, "max_dn": -0.45, "seed": 909},
    "002415": {"base": 32.0, "vol": 0.023, "trends": 3, "max_up": 0.55, "max_dn": -0.30, "seed": 1010},
}


def generate_stock_data(code: str, n_days: int = 240) -> pd.DataFrame:
    """根据股票特征生成模拟日K数据。"""
    profile = STOCK_PROFILES[code]
    np.random.seed(profile["seed"])

    dates = pd.bdate_range("2025-09-01", periods=n_days)
    base = profile["base"]
    vol = profile["vol"]
    n_trends = profile["trends"]

    # 生成多段趋势
    prices = np.zeros(n_days)
    prices[0] = base
    seg_len = n_days // n_trends

    for seg in range(n_trends):
        start = seg * seg_len
        end = min((seg + 1) * seg_len, n_days)
        seg_n = end - start

        # 交替上涨/下跌/震荡
        if seg % 3 == 0:
            drift = profile["max_up"] * np.random.uniform(0.6, 1.0)
        elif seg % 3 == 1:
            drift = profile["max_dn"] * np.random.uniform(0.6, 1.0)
        else:
            drift = np.random.uniform(-0.05, 0.10)

        t = np.linspace(0, 1, seg_n)
        smooth = t * t * (3 - 2 * t)  # smoothstep
        start_price = prices[start - 1] if start > 0 else base
        target = start_price * (1 + drift)
        prices[start:end] = start_price + (target - start_price) * smooth
        prices[start:end] += np.random.normal(0, vol * start_price, seg_n)

    # 确保价格为正
    prices = np.maximum(prices, base * 0.3)

    close = prices
    open_ = close * (1 + np.random.normal(0, vol * 0.3, n_days))
    high = np.maximum(open_, close) * (1 + np.abs(np.random.normal(0, vol * 0.5, n_days)))
    low = np.minimum(open_, close) * (1 - np.abs(np.random.normal(0, vol * 0.5, n_days)))

    # 成交量：上涨放量，下跌缩量
    volume = np.random.uniform(5e6, 2e7, n_days)
    for i in range(1, n_days):
        if close[i] > close[i - 1]:
            volume[i] *= 1.4
        else:
            volume[i] *= 0.75

    return pd.DataFrame({
        "open": open_, "close": close, "high": high, "low": low, "vol": volume,
    }, index=dates)


# ─────────────────────────────────────────────────────────────────────────
# 回测引擎
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class BacktestMetrics:
    """单只股票的回测指标。"""
    code: str
    name: str
    sector: str
    total_return: float = 0.0       # 累计收益 %
    annual_return: float = 0.0      # 年化收益 %
    bh_return: float = 0.0          # 买入持有收益 %
    excess_return: float = 0.0      # 超额收益 %
    win_rate: float = 0.0           # 胜率 %
    profit_factor: float = 0.0      # 盈亏比
    max_drawdown: float = 0.0       # 最大回撤 %
    sharpe: float = 0.0             # Sharpe 比率
    trade_count: int = 0            # 交易次数
    avg_hold_days: float = 0.0      # 平均持仓天数
    avg_profit: float = 0.0         # 平均单笔收益 %
    max_single_profit: float = 0.0  # 最大单笔盈利 %
    max_single_loss: float = 0.0    # 最大单笔亏损 %
    capture_rate: float = 0.0       # 理论最大收益捕获率 %
    theo_max: float = 0.0           # 理论最大收益 %


def run_single_backtest(
    df: pd.DataFrame,
    code: str,
    name: str,
    sector: str,
    threshold: float = 35,
    min_gap: int = 5,
    div_lookback: int = 30,
) -> BacktestMetrics:
    """对单只股票执行回测。"""
    metrics = BacktestMetrics(code=code, name=name, sector=sector)

    # 计算指标
    df = compute_indicators(df)

    # 检测反转（使用自定义参数）
    signals = _detect_reversals_param(df, threshold, div_lookback)
    signals = generate_swing_signals(signals, df, min_gap=min_gap)

    # 计算交易
    trades = compute_swing_trades(df, signals)

    # 买入持有收益
    bh_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    metrics.bh_return = round(bh_return, 2)

    if not trades:
        metrics.total_return = 0.0
        metrics.excess_return = round(-bh_return, 2)
        return metrics

    # 累计收益（复利）
    total = 1.0
    for t in trades:
        total *= (1 + t.profit_pct / 100)
    strategy_return = (total - 1) * 100

    metrics.total_return = round(strategy_return, 2)
    metrics.excess_return = round(strategy_return - bh_return, 2)

    # 年化（按 240 交易日）
    n_days = len(df)
    metrics.annual_return = round(strategy_return * 240 / n_days, 2)

    # 胜率
    wins = [t for t in trades if t.profit_pct > 0]
    losses = [t for t in trades if t.profit_pct <= 0]
    metrics.win_rate = round(len(wins) / len(trades) * 100, 2)

    # 盈亏比
    avg_win = np.mean([t.profit_pct for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t.profit_pct for t in losses])) if losses else 1
    metrics.profit_factor = round(avg_win / avg_loss, 2) if avg_loss > 0 else 99.0

    # 最大回撤（基于逐日权益曲线）
    equity = _compute_equity_curve(df, signals)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak * 100
    metrics.max_drawdown = round(drawdown.min(), 2)

    # Sharpe（日收益率年化）
    daily_returns = np.diff(equity) / equity[:-1]
    if daily_returns.std() > 0:
        metrics.sharpe = round(daily_returns.mean() / daily_returns.std() * np.sqrt(240), 2)

    # 交易统计
    metrics.trade_count = len(trades)
    metrics.avg_hold_days = round(np.mean([t.hold_days for t in trades]), 1)
    metrics.avg_profit = round(np.mean([t.profit_pct for t in trades]), 2)
    metrics.max_single_profit = round(max(t.profit_pct for t in trades), 2)
    metrics.max_single_loss = round(min(t.profit_pct for t in trades), 2)

    # 理论最大收益
    theo_return, _ = compute_max_theoretical_profit(df)
    metrics.theo_max = round(theo_return, 2)
    if theo_return > 0:
        metrics.capture_rate = round(strategy_return / theo_return * 100, 1)

    return metrics


def _detect_reversals_param(
    df: pd.DataFrame, threshold: float, div_lookback: int
) -> list[ReversalSignal]:
    """带参数化的反转检测（复用 swing_reversal 逻辑但可调参数）。"""
    from swing_reversal import detect_divergence

    n = len(df)
    macd_top_div, macd_bot_div = detect_divergence(df["close"], df["dif"], lookback=div_lookback)
    rsi_top_div, rsi_bot_div = detect_divergence(df["close"], df["rsi"], lookback=max(div_lookback - 5, 15))

    signals: list[ReversalSignal] = []

    for i in range(60, n):
        top_score = 0.0
        bot_score = 0.0
        top_triggers: list[str] = []
        bot_triggers: list[str] = []

        # 顶部信号
        if macd_top_div.iloc[i]:
            top_score += 25; top_triggers.append("MACD顶背离")
        if df["rsi"].iloc[i - 1] > 70 and df["rsi"].iloc[i] <= 70:
            top_score += 20; top_triggers.append("RSI超买回落")
        elif rsi_top_div.iloc[i]:
            top_score += 15; top_triggers.append("RSI顶背离")
        if (df["ma5"].iloc[i - 1] >= df["ma20"].iloc[i - 1] and
                df["ma5"].iloc[i] < df["ma20"].iloc[i]):
            top_score += 20; top_triggers.append("MA死叉")
        if (df["high"].iloc[i - 1] >= df["boll_upper"].iloc[i - 1] and
                df["close"].iloc[i] < df["close"].iloc[i - 1]):
            top_score += 15; top_triggers.append("布林上轨受阻")
        if (df["close"].iloc[i] > df["close"].iloc[i - 5] and
                df["vol"].iloc[i] < df["vol_ma20"].iloc[i] * 0.7):
            top_score += 10; top_triggers.append("量价背离")
        if (not np.isnan(df["reg_slope"].iloc[i - 1]) and
                not np.isnan(df["reg_slope"].iloc[i])):
            if df["reg_slope"].iloc[i - 1] > 0 and df["reg_slope"].iloc[i] <= 0:
                top_score += 15; top_triggers.append("斜率转负")
        if (df["macd_hist"].iloc[i] > 0 and
                df["macd_hist"].iloc[i] < df["macd_hist"].iloc[i - 1] * 0.6 and
                df["macd_hist"].iloc[i - 1] > 0):
            top_score += 5; top_triggers.append("MACD柱缩短")

        # 底部信号
        if macd_bot_div.iloc[i]:
            bot_score += 25; bot_triggers.append("MACD底背离")
        if df["rsi"].iloc[i - 1] < 30 and df["rsi"].iloc[i] >= 30:
            bot_score += 20; bot_triggers.append("RSI超卖反弹")
        elif rsi_bot_div.iloc[i]:
            bot_score += 15; bot_triggers.append("RSI底背离")
        if (df["ma5"].iloc[i - 1] <= df["ma20"].iloc[i - 1] and
                df["ma5"].iloc[i] > df["ma20"].iloc[i]):
            bot_score += 20; bot_triggers.append("MA金叉")
        if (df["low"].iloc[i - 1] <= df["boll_lower"].iloc[i - 1] and
                df["close"].iloc[i] > df["close"].iloc[i - 1]):
            bot_score += 15; bot_triggers.append("布林下轨支撑")
        if (df["vol"].iloc[i] > df["vol_ma5"].iloc[i] * 1.5 and
                df["close"].iloc[i] > df["close"].iloc[i - 1] and
                df["vol_ma5"].iloc[i] < df["vol_ma20"].iloc[i] * 0.8):
            bot_score += 10; bot_triggers.append("底部放量")
        if (not np.isnan(df["reg_slope"].iloc[i - 1]) and
                not np.isnan(df["reg_slope"].iloc[i])):
            if df["reg_slope"].iloc[i - 1] < 0 and df["reg_slope"].iloc[i] >= 0:
                bot_score += 15; bot_triggers.append("斜率转正")
        if (df["dif"].iloc[i - 1] <= df["dea"].iloc[i - 1] and
                df["dif"].iloc[i] > df["dea"].iloc[i]):
            bot_score += 10; bot_triggers.append("MACD金叉")

        date_str = df.index[i].strftime("%Y-%m-%d")
        price = df["close"].iloc[i]

        if top_score >= threshold:
            signals.append(ReversalSignal(
                idx=i, date=date_str, price=price,
                direction="TOP", confidence=min(top_score, 100),
                triggers=top_triggers,
            ))
        elif bot_score >= threshold:
            signals.append(ReversalSignal(
                idx=i, date=date_str, price=price,
                direction="BOTTOM", confidence=min(bot_score, 100),
                triggers=bot_triggers,
            ))

    return signals


def _compute_equity_curve(df: pd.DataFrame, signals: list[ReversalSignal]) -> np.ndarray:
    """计算逐日权益曲线。"""
    n = len(df)
    equity = np.ones(n)
    in_pos = False
    entry_price = 0.0
    running = 1.0

    sig_map = {s.idx: s for s in signals}

    for i in range(n):
        if i in sig_map:
            sig = sig_map[i]
            if sig.direction == "BOTTOM" and not in_pos:
                in_pos = True
                entry_price = sig.price
            elif sig.direction == "TOP" and in_pos:
                running *= (1 + (sig.price - entry_price) / entry_price)
                in_pos = False

        if in_pos and entry_price > 0:
            equity[i] = running * (df["close"].iloc[i] / entry_price)
        else:
            equity[i] = running

    return equity


# ─────────────────────────────────────────────────────────────────────────
# 参数优化
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class OptimizeResult:
    """优化结果。"""
    threshold: float
    min_gap: int
    div_lookback: int
    avg_return: float
    avg_win_rate: float
    avg_sharpe: float
    avg_capture: float
    avg_max_dd: float
    score: float  # 综合评分


def optimize_parameters(stock_dfs: dict[str, pd.DataFrame]) -> list[OptimizeResult]:
    """网格搜索最优参数组合。"""
    thresholds = [30, 35, 40, 45]
    min_gaps = [3, 5, 7, 10]
    div_lookbacks = [20, 25, 30, 35]

    results: list[OptimizeResult] = []
    total_combos = len(thresholds) * len(min_gaps) * len(div_lookbacks)
    print(f"  参数网格: {len(thresholds)}×{len(min_gaps)}×{len(div_lookbacks)} = {total_combos} 组合")
    print(f"  回测股票: {len(stock_dfs)} 只")
    print(f"  总回测次数: {total_combos * len(stock_dfs)}")
    print()

    for threshold, min_gap, div_lb in itertools.product(thresholds, min_gaps, div_lookbacks):
        returns, win_rates, sharpes, captures, max_dds = [], [], [], [], []

        for code, df in stock_dfs.items():
            stock_info = next(s for s in STOCK_POOL if s["code"] == code)
            m = run_single_backtest(
                df.copy(), code, stock_info["name"], stock_info["sector"],
                threshold=threshold, min_gap=min_gap, div_lookback=div_lb,
            )
            returns.append(m.total_return)
            win_rates.append(m.win_rate)
            sharpes.append(m.sharpe)
            captures.append(m.capture_rate)
            max_dds.append(m.max_drawdown)

        avg_ret = np.mean(returns)
        avg_wr = np.mean(win_rates)
        avg_sh = np.mean(sharpes)
        avg_cap = np.mean(captures)
        avg_dd = np.mean(max_dds)

        # 综合评分 = 收益×0.3 + 胜率×0.2 + Sharpe×0.2 + 捕获率×0.2 - 回撤×0.1
        score = (avg_ret * 0.3 + avg_wr * 0.2 + avg_sh * 5 * 0.2 +
                 avg_cap * 0.2 + avg_dd * 0.1)

        results.append(OptimizeResult(
            threshold=threshold, min_gap=min_gap, div_lookback=div_lb,
            avg_return=round(avg_ret, 2), avg_win_rate=round(avg_wr, 1),
            avg_sharpe=round(avg_sh, 2), avg_capture=round(avg_cap, 1),
            avg_max_dd=round(avg_dd, 2), score=round(score, 2),
        ))

    # 按综合评分排序
    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────
# 报告输出
# ─────────────────────────────────────────────────────────────────────────
def print_backtest_report(all_metrics: list[BacktestMetrics], title: str = "回测"):
    """打印多股票回测报告。"""
    print()
    print("=" * 90)
    print(f"      多股票波段策略 {title}报告 ({len(all_metrics)} 只)")
    print("=" * 90)

    # 表头
    header = (f"{'代码':<8}{'名称':<8}{'板块':<6}"
              f"{'策略收益':>8}{'持有收益':>8}{'超额':>8}"
              f"{'胜率':>6}{'盈亏比':>6}{'回撤':>7}"
              f"{'Sharpe':>7}{'交易':>4}{'捕获率':>7}")
    print(header)
    print("─" * 90)

    for m in all_metrics:
        row = (f"{m.code:<8}{m.name:<8}{m.sector:<6}"
               f"{m.total_return:>+7.1f}%{m.bh_return:>+7.1f}%{m.excess_return:>+7.1f}%"
               f"{m.win_rate:>5.0f}%{m.profit_factor:>6.1f}{m.max_drawdown:>6.1f}%"
               f"{m.sharpe:>7.2f}{m.trade_count:>4}{m.capture_rate:>6.1f}%")
        print(row)

    # 汇总统计
    print("─" * 90)
    n = len(all_metrics)
    avg_ret = np.mean([m.total_return for m in all_metrics])
    avg_bh = np.mean([m.bh_return for m in all_metrics])
    avg_excess = np.mean([m.excess_return for m in all_metrics])
    avg_wr = np.mean([m.win_rate for m in all_metrics])
    avg_pf = np.mean([m.profit_factor for m in all_metrics if m.profit_factor < 90])
    avg_dd = np.mean([m.max_drawdown for m in all_metrics])
    avg_sh = np.mean([m.sharpe for m in all_metrics])
    total_trades = sum(m.trade_count for m in all_metrics)
    avg_cap = np.mean([m.capture_rate for m in all_metrics if m.capture_rate > 0])

    print(f"{'平均':<8}{'':<8}{'':<6}"
          f"{avg_ret:>+7.1f}%{avg_bh:>+7.1f}%{avg_excess:>+7.1f}%"
          f"{avg_wr:>5.0f}%{avg_pf:>6.1f}{avg_dd:>6.1f}%"
          f"{avg_sh:>7.2f}{total_trades:>4}{avg_cap:>6.1f}%")
    print()

    # 综合评价
    print("综合评价:")
    print(f"  • 策略平均收益 {avg_ret:+.1f}% vs 买入持有 {avg_bh:+.1f}%，超额 {avg_excess:+.1f}%")
    print(f"  • 平均胜率 {avg_wr:.0f}%，盈亏比 {avg_pf:.1f}")
    print(f"  • 平均最大回撤 {avg_dd:.1f}%，Sharpe {avg_sh:.2f}")
    print(f"  • 总交易 {total_trades} 笔，平均捕获率 {avg_cap:.1f}%")

    # 最佳/最差
    best = max(all_metrics, key=lambda m: m.total_return)
    worst = min(all_metrics, key=lambda m: m.total_return)
    print(f"  • 最佳: {best.name}({best.code}) {best.total_return:+.1f}%")
    print(f"  • 最差: {worst.name}({worst.code}) {worst.total_return:+.1f}%")

    # 跑赢买入持有的比例
    beat_bh = sum(1 for m in all_metrics if m.excess_return > 0)
    print(f"  • 跑赢买入持有: {beat_bh}/{n} 只 ({beat_bh/n*100:.0f}%)")
    print("=" * 90)


def print_optimization_report(
    results: list[OptimizeResult],
    default_metrics: list[BacktestMetrics],
    optimized_metrics: list[BacktestMetrics],
):
    """打印优化结果报告。"""
    print()
    print("=" * 90)
    print("      参数优化结果")
    print("=" * 90)

    # Top 5 参数组合
    print("\nTop 5 参数组合（按综合评分排序）:")
    print(f"  {'排名':<4}{'阈值':<6}{'间隔':<6}{'回看':<6}"
          f"{'平均收益':>8}{'胜率':>6}{'Sharpe':>7}{'捕获率':>7}{'回撤':>7}{'评分':>7}")
    print("  " + "─" * 70)
    for i, r in enumerate(results[:5], 1):
        print(f"  {i:<4}{r.threshold:<6.0f}{r.min_gap:<6}{r.div_lookback:<6}"
              f"{r.avg_return:>+7.1f}%{r.avg_win_rate:>5.0f}%{r.avg_sharpe:>7.2f}"
              f"{r.avg_capture:>6.1f}%{r.avg_max_dd:>6.1f}%{r.score:>7.1f}")

    best = results[0]
    print(f"\n最优参数: 阈值={best.threshold:.0f}, 间隔={best.min_gap}天, 回看={best.div_lookback}天")

    # 优化前后对比
    print()
    print("─" * 90)
    print("优化前后对比:")
    print("─" * 90)

    d_ret = np.mean([m.total_return for m in default_metrics])
    o_ret = np.mean([m.total_return for m in optimized_metrics])
    d_wr = np.mean([m.win_rate for m in default_metrics])
    o_wr = np.mean([m.win_rate for m in optimized_metrics])
    d_sh = np.mean([m.sharpe for m in default_metrics])
    o_sh = np.mean([m.sharpe for m in optimized_metrics])
    d_dd = np.mean([m.max_drawdown for m in default_metrics])
    o_dd = np.mean([m.max_drawdown for m in optimized_metrics])
    d_cap = np.mean([m.capture_rate for m in default_metrics if m.capture_rate > 0])
    o_cap = np.mean([m.capture_rate for m in optimized_metrics if m.capture_rate > 0])

    print(f"  {'指标':<12}{'优化前':>10}{'优化后':>10}{'变化':>10}")
    print(f"  {'平均收益':<12}{d_ret:>+9.1f}%{o_ret:>+9.1f}%{o_ret-d_ret:>+9.1f}%")
    print(f"  {'平均胜率':<12}{d_wr:>9.0f}%{o_wr:>9.0f}%{o_wr-d_wr:>+9.0f}%")
    print(f"  {'Sharpe':<12}{d_sh:>10.2f}{o_sh:>10.2f}{o_sh-d_sh:>+10.2f}")
    print(f"  {'最大回撤':<12}{d_dd:>9.1f}%{o_dd:>9.1f}%{o_dd-d_dd:>+9.1f}%")
    print(f"  {'捕获率':<12}{d_cap:>9.1f}%{o_cap:>9.1f}%{o_cap-d_cap:>+9.1f}%")
    print("=" * 90)


# ─────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="多股票回测 & 策略优化")
    parser.add_argument("--optimize", action="store_true", help="执行参数优化")
    parser.add_argument("--days", type=int, default=240, help="回测天数")
    args = parser.parse_args()

    print("=" * 90)
    print("      多股票波段反转策略回测系统")
    print("=" * 90)
    print(f"股票池: {len(STOCK_POOL)} 只（排除科创板 688）")
    print(f"回测周期: {args.days} 个交易日")
    print(f"策略: 多信号融合趋势反转 (MACD/RSI/MA/BOLL/量价/斜率)")
    print()

    # 1. 生成数据
    print("生成模拟数据...")
    stock_dfs: dict[str, pd.DataFrame] = {}
    for stock in STOCK_POOL:
        stock_dfs[stock["code"]] = generate_stock_data(stock["code"], args.days)
    print(f"  已生成 {len(stock_dfs)} 只股票数据\n")

    # 2. 默认参数回测
    print("执行默认参数回测 (阈值=35, 间隔=5, 回看=30)...")
    default_metrics: list[BacktestMetrics] = []
    for stock in STOCK_POOL:
        m = run_single_backtest(
            stock_dfs[stock["code"]].copy(),
            stock["code"], stock["name"], stock["sector"],
            threshold=35, min_gap=5, div_lookback=30,
        )
        default_metrics.append(m)

    print_backtest_report(default_metrics, title="默认参数回测")

    # 3. 参数优化
    if args.optimize:
        print("\n\n开始参数优化（网格搜索）...")
        opt_results = optimize_parameters(stock_dfs)

        # 用最优参数重新回测
        best = opt_results[0]
        print(f"\n使用最优参数重新回测 (阈值={best.threshold:.0f}, "
              f"间隔={best.min_gap}, 回看={best.div_lookback})...")
        optimized_metrics: list[BacktestMetrics] = []
        for stock in STOCK_POOL:
            m = run_single_backtest(
                stock_dfs[stock["code"]].copy(),
                stock["code"], stock["name"], stock["sector"],
                threshold=best.threshold, min_gap=best.min_gap,
                div_lookback=best.div_lookback,
            )
            optimized_metrics.append(m)

        print_backtest_report(optimized_metrics, title="优化后回测")
        print_optimization_report(opt_results, default_metrics, optimized_metrics)
    else:
        print("\n提示: 添加 --optimize 参数执行参数优化")


if __name__ == "__main__":
    main()
