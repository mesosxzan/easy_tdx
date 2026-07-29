"""缠论三类买点策略 -- 问财随机选股交叉验证脚本。

使用问财语义搜索获取多组不同风格的随机股票池，对每只股票独立运行
缠论三类买点策略回测，与买入持有基准对比，验证策略的普适性。

问财查询组（覆盖不同市场风格）：
  1. 今日成交量大于20万手     -- 活跃大盘股
  2. 总市值50-200亿 市盈率20-40 -- 中盘价值股
  3. 近60日涨幅超过30%        -- 强势股
  4. 股价5-20元 主板          -- 中低价主板股
  5. 周换手率超过20%          -- 高换手率股

用法::

    python strategies/verify_chanlun_3buy.py
    python strategies/verify_chanlun_3buy.py --top 15 --count 800
    python strategies/verify_chanlun_3buy.py --queries "今日涨停" "白酒行业"
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

from easy_tdx.cli.conn import get_mac_client
from easy_tdx.mac.enums import Period, Adjust
from easy_tdx.models.enums import Market
from easy_tdx.backtest.engine import BacktestEngine
from easy_tdx.wencai import WencaiClient, WencaiError, filter_tradable
from strategies.chanlun_3buy import Chanlun3BuyStrategy

# ── 问财查询组 ────────────────────────────────────────────────────────────────

WENCAI_QUERIES: list[tuple[str, str]] = [
    ("今日成交量大于20万手 非ST 非科创板", "活跃大盘股"),
    ("总市值50-200亿 市盈率20-40 非ST 非科创板", "中盘价值股"),
    ("近60日涨幅超过30% 非ST 非科创板", "强势股"),
    ("股价5-20元 主板 非ST", "中低价主板股"),
    ("周换手率超过20% 非ST 非科创板", "高换手率股"),
    ("总市值小于50亿 非ST 非科创板", "小盘股"),
    ("市净率小于1 非ST 非科创板", "破净股"),
    ("股息率大于3% 非ST 非科创板", "高股息股"),
    ("创业板 非ST", "创业板股"),
    ("近60日跌幅超过20% 非ST 非科创板", "超跌股"),
    ("总市值大于1000亿 非ST 非科创板", "超大盘股"),
    ("股价20-50元 非ST 非科创板", "中价股"),
    ("股价大于50元 非ST 非科创板", "高价股"),
    ("上市时间小于2年 非ST 非科创板", "次新股"),
]

# ── 回测参数 ──────────────────────────────────────────────────────────────────

CASH = 1_000_000.0
COMMISSION = 0.0003
WARMUP_BARS = 60
KLINE_COUNT = 800
TOP_PER_QUERY = 10


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class StockResult:
    """单只股票的回测结果。"""

    symbol: str
    market: str
    name: str
    query_group: str

    # 策略指标
    strategy_return: float = 0.0
    strategy_max_drawdown: float = 0.0
    strategy_sharpe: float = 0.0
    strategy_win_rate: float = 0.0
    trade_count: int = 0

    # 基准指标
    buy_hold_return: float = 0.0
    buy_hold_max_drawdown: float = 0.0

    # 衍生指标
    excess_return: float = 0.0

    # 状态
    has_error: bool = False
    error_msg: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def compute_excess(self) -> None:
        self.excess_return = self.strategy_return - self.buy_hold_return


@dataclass
class GroupSummary:
    """单组问财查询的汇总统计。"""

    group_name: str
    query: str
    results: list[StockResult] = field(default_factory=list)

    # 汇总指标
    avg_strategy_return: float = 0.0
    avg_buy_hold_return: float = 0.0
    avg_excess_return: float = 0.0
    avg_max_drawdown: float = 0.0
    avg_sharpe: float = 0.0
    avg_win_rate: float = 0.0
    total_trades: int = 0
    valid_count: int = 0
    beat_market_count: int = 0

    def compute_stats(self) -> None:
        valid = [r for r in self.results if not r.has_error and not r.skipped]
        self.valid_count = len(valid)
        if not valid:
            return
        self.avg_strategy_return = sum(r.strategy_return for r in valid) / len(valid)
        self.avg_buy_hold_return = sum(r.buy_hold_return for r in valid) / len(valid)
        self.avg_excess_return = sum(r.excess_return for r in valid) / len(valid)
        self.avg_max_drawdown = sum(r.strategy_max_drawdown for r in valid) / len(valid)
        self.avg_sharpe = sum(r.strategy_sharpe for r in valid) / len(valid)
        self.avg_win_rate = sum(r.strategy_win_rate for r in valid) / len(valid)
        self.total_trades = sum(r.trade_count for r in valid)
        self.beat_market_count = sum(1 for r in valid if r.excess_return > 0)


# ── 核心函数 ──────────────────────────────────────────────────────────────────


def _market_from_code(market_str: str) -> Market:
    """将问财市场代码转换为 Market 枚举。"""
    market_map = {"SH": Market.SH, "SZ": Market.SZ, "BJ": Market.BJ}
    return market_map.get(market_str, Market.SH)


def fetch_wencai_stocks(
    queries: list[tuple[str, str]],
    top_per_query: int,
) -> list[tuple[str, str, str, str]]:
    """通过问财获取多组随机股票池。

    Returns:
        [(symbol, market, name, group_name), ...]
    """
    all_stocks: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    client = WencaiClient()

    for query, group_name in queries:
        print(f"\n  [{group_name}] 查询: {query}")
        try:
            stocks = filter_tradable(client.search(query, perpage=max(top_per_query, 50)))
        except WencaiError as exc:
            print(f"    问财查询失败: {exc}")
            continue

        added = 0
        for stock in stocks:
            if stock.symbol in seen:
                continue
            seen.add(stock.symbol)
            all_stocks.append((stock.symbol, stock.market, stock.name, group_name))
            added += 1
            if added >= top_per_query:
                break

        print(f"    获取 {added} 只 (去重后总计 {len(all_stocks)} 只)")

    return all_stocks


def compute_buy_hold(df: Any) -> tuple[float, float]:
    """计算买入持有收益率和最大回撤。

    买入持有：以第一根 K 线收盘价买入，持有至最后一根。
    """
    closes = df["close"].values
    if len(closes) < 2:
        return 0.0, 0.0

    buy_price = closes[0]
    final_price = closes[-1]
    buy_hold_return = (final_price - buy_price) / buy_price if buy_price > 0 else 0.0

    # 计算最大回撤
    running_max = closes[0]
    max_drawdown = 0.0
    for price in closes:
        if price > running_max:
            running_max = price
        drawdown = (running_max - price) / running_max if running_max > 0 else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return buy_hold_return, max_drawdown


def run_single_backtest(
    df: Any,
    cash: float,
    warmup_bars: int,
) -> dict[str, Any]:
    """运行缠论三类买点策略回测。"""
    # 确保使用最优参数（前瞻偏差修复后重新优化）
    Chanlun3BuyStrategy.stop_loss_pct = 10.0  # 安全网
    Chanlun3BuyStrategy.take_profit_pct = 0.0
    Chanlun3BuyStrategy.ma_exit_period = 0
    Chanlun3BuyStrategy.trailing_stop_pct = 0.0
    Chanlun3BuyStrategy.max_hold_days = 15  # 网格搜索最优
    Chanlun3BuyStrategy.use_chanlun_sell = True

    engine = BacktestEngine(
        strategy=Chanlun3BuyStrategy,
        cash=cash,
        commission=COMMISSION,
        chanlun_level="DAILY",
        warmup_bars=warmup_bars,
    )
    result = engine.run(df)
    perf = result.performance
    return {
        "total_return": perf.get("total_return", 0.0),
        "max_drawdown": perf.get("max_drawdown", 0.0),
        "sharpe_ratio": perf.get("sharpe_ratio", 0.0),
        "win_rate": perf.get("win_rate", 0.0),
        "trade_count": len(result.trades),
    }


def run_verification(
    stocks: list[tuple[str, str, str, str]],
    count: int,
    cash: float,
    warmup_bars: int,
) -> list[StockResult]:
    """对股票池逐个运行回测。"""
    results: list[StockResult] = []
    total = len(stocks)

    with get_mac_client() as client:
        for idx, (symbol, market_str, name, group_name) in enumerate(stocks, 1):
            sr = StockResult(
                symbol=symbol,
                market=market_str,
                name=name,
                query_group=group_name,
            )

            print(
                f"  [{idx}/{total}] {name}({symbol}.{market_str}) [{group_name}] ...",
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

                if df is None or len(df) < warmup_bars + 10:
                    sr.skipped = True
                    sr.skip_reason = f"K线不足 ({len(df) if df is not None else 0} 根)"
                    print(f"SKIP ({sr.skip_reason})")
                    results.append(sr)
                    continue

                # 买入持有基准
                bh_return, bh_drawdown = compute_buy_hold(df)
                sr.buy_hold_return = bh_return
                sr.buy_hold_max_drawdown = bh_drawdown

                # 策略回测
                metrics = run_single_backtest(df, cash=cash, warmup_bars=warmup_bars)
                sr.strategy_return = metrics["total_return"]
                sr.strategy_max_drawdown = metrics["max_drawdown"]
                sr.strategy_sharpe = metrics["sharpe_ratio"]
                sr.strategy_win_rate = metrics["win_rate"]
                sr.trade_count = metrics["trade_count"]

                sr.compute_excess()

                print(
                    f"策略={sr.strategy_return * 100:+7.2f}% "
                    f"基准={sr.buy_hold_return * 100:+7.2f}% "
                    f"超额={sr.excess_return * 100:+7.2f}% "
                    f"交易={sr.trade_count}笔"
                )

            except Exception as exc:
                sr.has_error = True
                sr.error_msg = str(exc)
                print(f"ERROR ({exc})")

            results.append(sr)

    return results


def group_results(results: list[StockResult]) -> list[GroupSummary]:
    """将结果按问财查询组分组。"""
    groups: dict[str, GroupSummary] = {}
    for r in results:
        if r.query_group not in groups:
            groups[r.query_group] = GroupSummary(
                group_name=r.query_group,
                query="",
            )
        groups[r.query_group].results.append(r)

    # 补充 query 文本
    query_map = {name: q for q, name in WENCAI_QUERIES}
    for gs in groups.values():
        gs.query = query_map.get(gs.group_name, "")
        gs.compute_stats()

    return list(groups.values())


def print_detail_table(results: list[StockResult]) -> None:
    """打印逐股明细表。"""
    print(f"\n{'=' * 130}")
    print("  逐股明细")
    print(f"{'=' * 130}")

    header = (
        f"{'股票':<14} {'代码':<10} {'分组':<10} "
        f"{'策略收益':>9} {'买入持有':>9} {'超额收益':>9} "
        f"{'最大回撤':>8} {'夏普':>7} {'胜率':>7} {'交易':>5} {'状态':>6}"
    )
    print(header)
    print("-" * 130)

    for r in results:
        if r.has_error:
            status = "错误"
            strat_ret = "N/A"
            bh_ret = "N/A"
            excess = "N/A"
            dd = "N/A"
            sharpe = "N/A"
            win = "N/A"
            trades = "N/A"
        elif r.skipped:
            status = "跳过"
            strat_ret = "N/A"
            bh_ret = "N/A"
            excess = "N/A"
            dd = "N/A"
            sharpe = "N/A"
            win = "N/A"
            trades = "N/A"
        else:
            status = "OK"
            strat_ret = f"{r.strategy_return * 100:+.2f}%"
            bh_ret = f"{r.buy_hold_return * 100:+.2f}%"
            excess = f"{r.excess_return * 100:+.2f}%"
            dd = f"{r.strategy_max_drawdown * 100:.2f}%"
            sharpe = f"{r.strategy_sharpe:.3f}"
            win = f"{r.strategy_win_rate * 100:.1f}%"
            trades = str(r.trade_count)

        print(
            f"{r.name:<14} {r.symbol:<10} {r.query_group:<10} "
            f"{strat_ret:>9} {bh_ret:>9} {excess:>9} "
            f"{dd:>8} {sharpe:>7} {win:>7} {trades:>5} {status:>6}"
        )


def print_group_summary(groups: list[GroupSummary]) -> None:
    """打印分组汇总表。"""
    print(f"\n{'=' * 130}")
    print("  分组汇总")
    print(f"{'=' * 130}")

    header = (
        f"{'分组':<12} {'查询语句':<28} {'有效数':>6} "
        f"{'平均策略收益':>12} {'平均买入持有':>12} {'平均超额':>10} "
        f"{'平均回撤':>9} {'平均夏普':>9} {'平均胜率':>9} {'总交易':>6} {'跑赢大盘':>8}"
    )
    print(header)
    print("-" * 130)

    for gs in groups:
        query_short = gs.query[:26] if len(gs.query) > 26 else gs.query
        print(
            f"{gs.group_name:<12} {query_short:<28} {gs.valid_count:>6} "
            f"{gs.avg_strategy_return * 100:>+11.2f}% "
            f"{gs.avg_buy_hold_return * 100:>+11.2f}% "
            f"{gs.avg_excess_return * 100:>+9.2f}% "
            f"{gs.avg_max_drawdown * 100:>8.2f}% "
            f"{gs.avg_sharpe:>9.3f} "
            f"{gs.avg_win_rate * 100:>8.1f}% "
            f"{gs.total_trades:>6} "
            f"{gs.beat_market_count}/{gs.valid_count:>2}"
        )


def print_overall_summary(results: list[StockResult], groups: list[GroupSummary]) -> None:
    """打印全局汇总。"""
    valid = [r for r in results if not r.has_error and not r.skipped]

    print(f"\n{'=' * 130}")
    print("  ★ 全局汇总 ★")
    print(f"{'=' * 130}")

    if not valid:
        print("  无有效结果")
        return

    total_stocks = len(results)
    valid_count = len(valid)
    error_count = sum(1 for r in results if r.has_error)
    skip_count = sum(1 for r in results if r.skipped)

    avg_strat = sum(r.strategy_return for r in valid) / valid_count
    avg_bh = sum(r.buy_hold_return for r in valid) / valid_count
    avg_excess = sum(r.excess_return for r in valid) / valid_count
    avg_dd = sum(r.strategy_max_drawdown for r in valid) / valid_count
    avg_sharpe = sum(r.strategy_sharpe for r in valid) / valid_count
    avg_win = sum(r.strategy_win_rate for r in valid) / valid_count
    total_trades = sum(r.trade_count for r in valid)
    beat_count = sum(1 for r in valid if r.excess_return > 0)
    positive_count = sum(1 for r in valid if r.strategy_return > 0)

    print(f"  总标的数:     {total_stocks}")
    print(f"  有效标的:     {valid_count} (错误 {error_count}, 跳过 {skip_count})")
    print(f"  策略正收益:   {positive_count}/{valid_count} ({positive_count / valid_count * 100:.1f}%)")
    print(f"  跑赢买入持有: {beat_count}/{valid_count} ({beat_count / valid_count * 100:.1f}%)")
    print()
    print(f"  平均策略收益率:   {avg_strat * 100:+.2f}%")
    print(f"  平均买入持有收益: {avg_bh * 100:+.2f}%")
    print(f"  平均超额收益:     {avg_excess * 100:+.2f}%")
    print(f"  平均最大回撤:     {avg_dd * 100:.2f}%")
    print(f"  平均夏普比率:     {avg_sharpe:.3f}")
    print(f"  平均胜率:         {avg_win * 100:.1f}%")
    print(f"  总交易笔数:       {total_trades}")
    print(f"  平均交易笔数:     {total_trades / valid_count:.1f}")

    # 收益分布
    print(f"\n  --- 策略收益率分布 ---")
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
        cnt = sum(1 for r in valid if lo <= r.strategy_return < hi)
        if cnt > 0:
            bar = "#" * cnt
            print(f"    {label:<14} {cnt:>3} {bar}")

    # 超额收益分布
    print(f"\n  --- 超额收益分布 ---")
    excess_brackets = [
        (-999, -0.30, "< -30%"),
        (-0.30, -0.10, "-30% ~ -10%"),
        (-0.10, 0.0, "-10% ~ 0%"),
        (0.0, 0.10, "0% ~ 10%"),
        (0.10, 0.30, "10% ~ 30%"),
        (0.30, 0.50, "30% ~ 50%"),
        (0.50, 999, "> 50%"),
    ]
    for lo, hi, label in excess_brackets:
        cnt = sum(1 for r in valid if lo <= r.excess_return < hi)
        if cnt > 0:
            bar = "#" * cnt
            print(f"    {label:<14} {cnt:>3} {bar}")


def _is_star_market(symbol: str, market: str) -> bool:
    """判断是否为科创板股票（688 开头）。"""
    return symbol.startswith("688") or (market == "SH" and symbol.startswith("68"))


def _is_st_stock(name: str) -> bool:
    """判断是否为 ST 股票。"""
    upper_name = name.upper()
    return "ST" in upper_name or "*ST" in upper_name


def filter_excluded_stocks(
    results: list[StockResult],
) -> list[StockResult]:
    """排除科创板和 ST 股票。"""
    return [
        r
        for r in results
        if not _is_star_market(r.symbol, r.market)
        and not _is_st_stock(r.name)
    ]


def print_portfolio_recommendation(
    results: list[StockResult],
    groups: list[GroupSummary],
    portfolio_cash: float = 1_000_000.0,
) -> None:
    """输出实盘组合推荐。

    筛选规则：
    1. 排除科创板、ST 股票
    2. 排除错误/跳过的标的
    3. 策略收益率 > 0
    4. 按超额收益 + 夏普比率 + 胜率综合评分排序
    5. 分三个风险等级推荐组合
    """
    print(f"\n{'=' * 130}")
    print("  ★ 实盘组合推荐 ★")
    print(f"  总资金: {portfolio_cash:,.0f} 元")
    print(f"{'=' * 130}")

    # 排除科创板/ST
    clean_results = filter_excluded_stocks(results)

    excluded = len(results) - len(clean_results)
    if excluded > 0:
        excluded_list = [
            r
            for r in results
            if _is_star_market(r.symbol, r.market) or _is_st_stock(r.name)
        ]
        print(f"\n  已排除 {excluded} 只标的（科创板/ST）:")
        for r in excluded_list:
            reason = []
            if _is_star_market(r.symbol, r.market):
                reason.append("科创板")
            if _is_st_stock(r.name):
                reason.append("ST")
            print(f"    - {r.name}({r.symbol}.{r.market}) [{','.join(reason)}]")

    # 有效结果：策略正收益且有交易记录
    valid = [
        r
        for r in clean_results
        if not r.has_error and not r.skipped and r.strategy_return > 0 and r.trade_count >= 3
    ]

    if not valid:
        print("\n  无符合条件的标的")
        return

    # 综合评分：超额收益(40%) + 夏普(30%) + 胜率(20%) + 低回撤(10%)
    for r in valid:
        # 各指标归一化到 0-1
        excess_norm = max(0.0, min(1.0, (r.excess_return + 0.5) / 1.5))  # -50%~+100% -> 0~1
        sharpe_norm = max(0.0, min(1.0, r.strategy_sharpe / 2.0))  # 0~2 -> 0~1
        win_norm = max(0.0, min(1.0, r.strategy_win_rate))  # 0~100% -> 0~1
        dd_norm = max(0.0, min(1.0, 1.0 - r.strategy_max_drawdown / 0.5))  # 0~50%回撤 -> 1~0
        r_score = excess_norm * 0.40 + sharpe_norm * 0.30 + win_norm * 0.20 + dd_norm * 0.10
        object.__setattr__(r, "_score", r_score)

    valid.sort(key=lambda x: getattr(x, "_score", 0.0), reverse=True)

    # ── 组合 A：稳健型（低回撤 + 高胜率）──
    stable_pool = [r for r in valid if r.strategy_max_drawdown < 0.25 and r.strategy_win_rate >= 0.65]
    stable_pool.sort(
        key=lambda x: (getattr(x, "_score", 0.0), x.strategy_return),
        reverse=True,
    )
    stable_pick = stable_pool[:8]

    # ── 组合 B：均衡型（中等收益 + 正超额）──
    balanced_pool = [r for r in valid if r.excess_return > -0.10 and r.strategy_return > 0.20]
    balanced_pool.sort(
        key=lambda x: (getattr(x, "_score", 0.0), x.strategy_return),
        reverse=True,
    )
    balanced_pick = balanced_pool[:8]

    # ── 组合 C：进取型（高收益 + 高夏普）──
    aggressive_pool = [r for r in valid if r.strategy_return > 0.50 and r.strategy_sharpe > 0.6]
    aggressive_pool.sort(
        key=lambda x: (getattr(x, "_score", 0.0), x.strategy_return),
        reverse=True,
    )
    aggressive_pick = aggressive_pool[:8]

    # 打印组合
    _print_portfolio("A", "稳健型", "低回撤(<25%) + 高胜率(>=65%)", stable_pick, portfolio_cash)
    _print_portfolio("B", "均衡型", "正超额 + 收益>20%", balanced_pick, portfolio_cash)
    _print_portfolio("C", "进取型", "收益>50% + 夏普>0.6", aggressive_pick, portfolio_cash)

    # 全部候选标的明细
    print(f"\n  --- 全部候选标的（综合评分 Top 20）---")
    print(
        f"  {'排名':<4} {'股票名称':<12} {'代码':<12} {'分组':<10} "
        f"{'策略收益':>9} {'超额收益':>9} {'回撤':>7} {'夏普':>7} {'胜率':>7} "
        f"{'交易':>5} {'评分':>7}"
    )
    print(f"  {'-' * 104}")
    for rank, r in enumerate(valid[:20], 1):
        score = getattr(r, "_score", 0.0)
        print(
            f"  {rank:<4} {r.name:<12} {r.symbol + '.' + r.market:<12} {r.query_group:<10} "
            f"{r.strategy_return * 100:>+8.2f}% {r.excess_return * 100:>+8.2f}% "
            f"{r.strategy_max_drawdown * 100:>6.2f}% {r.strategy_sharpe:>7.3f} "
            f"{r.strategy_win_rate * 100:>6.1f}% {r.trade_count:>5} {score:>7.4f}"
        )


def _print_portfolio(
    label: str,
    name: str,
    criteria: str,
    picks: list[StockResult],
    total_cash: float,
) -> None:
    """打印单个组合推荐。"""
    print(f"\n  ── 组合 {label}: {name} （{criteria}）──")

    if not picks:
        print("    无符合条件的标的")
        return

    n = len(picks)
    per_stock = total_cash / n

    print(f"    建议持仓: {n} 只 | 每只约 {per_stock:,.0f} 元")
    print()
    print(
        f"    {'序号':<4} {'股票名称':<12} {'代码':<14} {'分组':<10} "
        f"{'策略收益':>9} {'超额收益':>9} {'回撤':>7} {'夏普':>7} {'胜率':>7} "
        f"{'交易':>5} {'建议仓位':>8}"
    )
    print(f"    {'-' * 106}")

    for idx, r in enumerate(picks, 1):
        weight = 1.0 / n
        print(
            f"    {idx:<4} {r.name:<12} {r.symbol + '.' + r.market:<14} {r.query_group:<10} "
            f"{r.strategy_return * 100:>+8.2f}% {r.excess_return * 100:>+8.2f}% "
            f"{r.strategy_max_drawdown * 100:>6.2f}% {r.strategy_sharpe:>7.3f} "
            f"{r.strategy_win_rate * 100:>6.1f}% {r.trade_count:>5} "
            f"{weight * 100:>6.1f}%"
        )

    # 组合统计
    avg_ret = sum(r.strategy_return for r in picks) / n
    avg_excess = sum(r.excess_return for r in picks) / n
    avg_dd = sum(r.strategy_max_drawdown for r in picks) / n
    avg_sharpe = sum(r.strategy_sharpe for r in picks) / n
    avg_win = sum(r.strategy_win_rate for r in picks) / n
    total_trades = sum(r.trade_count for r in picks)
    beat_count = sum(1 for r in picks if r.excess_return > 0)

    print(f"    {'-' * 106}")
    print(
        f"    组合统计: 平均收益={avg_ret * 100:+.2f}% 平均超额={avg_excess * 100:+.2f}% "
        f"平均回撤={avg_dd * 100:.2f}% 平均夏普={avg_sharpe:.3f} "
        f"平均胜率={avg_win * 100:.1f}% 跑赢={beat_count}/{n}"
    )


def main(
    queries: list[tuple[str, str]] | None = None,
    top_per_query: int = TOP_PER_QUERY,
    count: int = KLINE_COUNT,
    cash: float = CASH,
    warmup_bars: int = WARMUP_BARS,
) -> None:
    """主入口：问财随机选股交叉验证。"""
    if queries is None:
        queries = WENCAI_QUERIES

    print("=" * 130)
    print("  缠论三类买点策略 -- 问财随机选股交叉验证")
    print(f"  问财查询组: {len(queries)} 组 | 每组取前 {top_per_query} 只")
    print(f"  K线: {count} 根 | 资金: {cash:,.0f} 元 | 预热: {warmup_bars} 根")
    print(f"  策略参数: stop_loss=10%, max_hold_days=15, use_chanlun_sell=True (前瞻偏差修复后最优)")
    print("=" * 130)

    # 1. 问财选股
    print(f"\n>> Step 1: 问财随机选股 ({len(queries)} 组查询)")
    t0 = time.time()
    stocks = fetch_wencai_stocks(queries, top_per_query)
    print(f"\n  问财选股完成: {len(stocks)} 只 (耗时 {time.time() - t0:.1f}s)")

    if not stocks:
        print("ERROR: 问财未返回任何标的")
        return

    # 2. 逐个回测
    print(f"\n>> Step 2: 逐个回测 ({len(stocks)} 只)")
    t0 = time.time()
    results = run_verification(stocks, count, cash, warmup_bars)
    print(f"\n  回测完成 (耗时 {time.time() - t0:.1f}s)")

    # 3. 汇总输出
    print(f"\n>> Step 3: 汇总分析")
    groups = group_results(results)

    print_detail_table(results)
    print_group_summary(groups)
    print_overall_summary(results, groups)
    print_portfolio_recommendation(results, groups, cash)

    print(f"\n{'=' * 130}")
    print("  验证完成")
    print(f"{'=' * 130}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="缠论三类买点策略 -- 问财随机选股交叉验证")
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_PER_QUERY,
        help=f"每组问财查询取前 N 只 (默认 {TOP_PER_QUERY})",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=KLINE_COUNT,
        help=f"K线数量 (默认 {KLINE_COUNT})",
    )
    parser.add_argument(
        "--cash",
        type=float,
        default=CASH,
        help=f"初始资金 (默认 {CASH:,.0f})",
    )
    parser.add_argument(
        "--warmup-bars",
        type=int,
        default=WARMUP_BARS,
        help=f"预热K线数 (默认 {WARMUP_BARS})",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="自定义问财查询语句列表（覆盖默认 5 组）",
    )
    args = parser.parse_args()

    if args.queries:
        custom_queries = [(q, f"自定义{i + 1}") for i, q in enumerate(args.queries)]
        main(
            queries=custom_queries,
            top_per_query=args.top,
            count=args.count,
            cash=args.cash,
            warmup_bars=args.warmup_bars,
        )
    else:
        main(
            top_per_query=args.top,
            count=args.count,
            cash=args.cash,
            warmup_bars=args.warmup_bars,
        )
