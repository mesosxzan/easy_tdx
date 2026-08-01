"""回测 CLI 命令。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import click


def guess_market(code: str) -> str:
    """根据股票代码前缀自动推断所属市场。

    规则：
      6/9 开头 → SH（上海）
      0/2/3 开头 → SZ（深圳）
      4/8 开头 → BJ（北京）
    """
    code = code.strip()
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _parse_code_args(args: tuple[str, ...]) -> tuple[str, str]:
    """解析命令行参数，支持多种格式：

    - 单参数: '600519' → 自动推断市场
    - 单参数: 'SH:600519' → 显式市场
    - 双参数: 'SH', '600519' → 显式市场（向后兼容）
    """
    if len(args) == 2:
        # 旧格式: easy-tdx backtest SH 600519
        return args[0].strip().upper(), args[1].strip()
    arg = args[0].strip()
    # 支持 SH:600519 格式
    if ":" in arg:
        parts = arg.split(":", 1)
        return parts[0].strip().upper(), parts[1].strip()
    # 纯代码，自动推断市场
    return guess_market(arg), arg


@click.command()
@click.argument("code_args", nargs=-1, required=True)
@click.option("--strategy", "strategy_str", default=None, help="DSL 策略表达式 (P1)")
@click.option("--strategy-file", "strategy_file", default=None, help="Python 策略文件路径")
@click.option(
    "--combo-strategies",
    "combo_strategies",
    default=None,
    help="多因子组合：逗号分隔的策略文件路径（如 strats/a.py,strats/b.py,strats/c.py）",
)
@click.option(
    "--combo-mode",
    "combo_mode",
    default="MAJORITY",
    type=click.Choice(["AND", "OR", "MAJORITY"], case_sensitive=False),
    help="多因子信号合并模式（默认 MAJORITY）",
)
@click.option("--cash", default=100000.0, type=float, help="初始资金")
@click.option("--commission", default=0.0003, type=float, help="佣金率")
@click.option(
    "--execution",
    default="next_open",
    type=click.Choice(["next_open", "next_close"]),
    help="成交价规则",
)
@click.option(
    "--position-mode",
    "position_mode",
    default="full",
    type=click.Choice(["full", "fixed", "percent"], case_sensitive=False),
    help="仓位模式：full=全仓 / fixed=指定股数 / percent=百分比（默认 full）",
)
@click.option(
    "--warmup-bars",
    "warmup_bars",
    default=0,
    type=int,
    help="指标预热 bar 数，前 N 根不产生信号（默认 0）",
)
@click.option("--period", default="DAILY", help="K线周期")
@click.option("--adjust", default="NONE", help="复权: NONE/QFQ/HFQ")
@click.option("--count", default=500, type=int, help="K线数量")
@click.option("--indicators", default=None, help="预计算指标（逗号分隔）")
@click.option(
    "--chanlun-level",
    "chanlun_level",
    default="DAILY",
    help="自动计算缠论分析并注入策略（如 DAILY/30MIN），默认 DAILY",
)
@click.option("--table", "use_table", is_flag=True, default=True, help="表格输出（默认）")
@click.option("--json", "use_json", is_flag=True, default=False, help="JSON 输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default=None)
def backtest(
    code_args: tuple[str, ...],
    strategy_str: str | None,
    strategy_file: str | None,
    combo_strategies: str | None,
    combo_mode: str,
    cash: float,
    commission: float,
    execution: str,
    position_mode: str,
    warmup_bars: int,
    period: str,
    adjust: str,
    count: int,
    indicators: str | None,
    chanlun_level: str,
    use_table: bool,
    use_json: bool,
    output_fmt: str | None,
) -> None:
    """回测引擎：执行策略并返回绩效报告。

    CODE_ARG: 股票代码（自动推断市场），如 600519 / 000001 / SH:600519

    示例：

      easy-tdx backtest 600519 --strategy-file strategies/shadow_yang.py

      easy-tdx backtest 000001 --strategy-file my_strategy.py --indicators MACD,KDJ

      easy-tdx backtest 000001 --strategy-file chanlun_strategy.py

      easy-tdx backtest 000001 \\
        --combo-strategies strategies/macd_cross.py,strategies/rsi_reversal.py \\
        --combo-mode MAJORITY
    """
    from ..backtest.engine import BacktestEngine
    from ..cli.conn import get_mac_client
    from ..cli.parsers import parse_adjust, parse_market, parse_period
    from ..indicator import compute_indicators

    # 0. 解析代码参数（自动推断市场）
    if len(code_args) > 2:
        click.echo(
            "错误: 参数过多，用法: easy-tdx backtest 600519 或 easy-tdx backtest SH 600519",
            err=True,
        )
        raise SystemExit(1)
    market, code = _parse_code_args(code_args)

    # 1. 加载策略（单策略 or 多因子组合）
    is_combo = combo_strategies is not None

    if is_combo:
        assert combo_strategies is not None  # narrowed by is_combo
        combo_classes = _load_combo_strategies(combo_strategies)
    else:
        strategy_cls = _load_strategy(strategy_str, strategy_file)
        if strategy_cls is None:
            click.echo("错误: 必须指定 --strategy-file / --combo-strategies / --strategy", err=True)
            raise SystemExit(1)

    # 2. 获取数据
    mkt = parse_market(market)
    with get_mac_client() as client:
        df = client.get_stock_kline(
            mkt,
            code,
            period=parse_period(period),
            start=0,
            count=count,
            adjust=parse_adjust(adjust),
        )

    # 3. 预计算指标
    if indicators:
        indicator_list = [ind.strip() for ind in indicators.split(",")]
        df = compute_indicators(df, indicator_list)

    # 4. 创建引擎并运行
    if is_combo:
        from ..backtest.combo import CombinationRunner

        runner = CombinationRunner(
            strategy_classes=combo_classes,
            df=df,
            cash=cash,
            commission=commission,
            execution=execution,
        )
        result = runner.run_combination(
            indices=list(range(len(combo_classes))),
            mode=combo_mode.upper(),
        )
    else:
        assert strategy_cls is not None  # guarded above by SystemExit
        engine = BacktestEngine(
            strategy=strategy_cls,
            cash=cash,
            commission=commission,
            execution=execution,
            position_mode=position_mode,
            warmup_bars=warmup_bars,
            chanlun_level=chanlun_level,
        )
        result = engine.run(df)

    # 5. 输出结果（默认 table）
    if use_json:
        fmt = "json"
    elif output_fmt:
        fmt = output_fmt
    else:
        fmt = "table"
    if fmt == "json":
        click.echo(result.to_json())
    elif fmt == "table":
        _print_table(result, df)
    else:
        click.echo(result.to_json())


def _load_strategy(strategy_str: str | None, strategy_file: str | None) -> type | None:
    """加载策略类。

    优先从 Python 文件加载，其次从 DSL 表达式加载（未实现）。

    Args:
        strategy_str: DSL 策略表达式
        strategy_file: Python 策略文件路径

    Returns:
        Strategy 子类
    """

    if strategy_file:
        return _load_strategy_from_file(strategy_file)

    if strategy_str:
        click.echo("错误: DSL 策略表达式尚未实现", err=True)
        return None

    return None


def _load_strategy_from_file(path: str) -> type:
    """从 Python 文件加载 Strategy 子类。

    Args:
        path: Python 文件路径

    Returns:
        Strategy 子类
    """
    from ..backtest.strategy import Strategy

    file_path = Path(path)
    if not file_path.exists():
        click.echo(f"错误: 文件不存在: {path}", err=True)
        raise SystemExit(1)

    spec = importlib.util.spec_from_file_location("strategy_module", file_path)
    if spec is None or spec.loader is None:
        click.echo(f"错误: 无法加载文件: {path}", err=True)
        raise SystemExit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 查找 Strategy 子类
    strategy_classes = []
    for name in dir(module):
        obj = getattr(module, name)
        try:
            if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                strategy_classes.append(obj)
        except TypeError:
            pass

    if not strategy_classes:
        click.echo(f"错误: 文件中未找到 Strategy 子类: {path}", err=True)
        raise SystemExit(1)

    if len(strategy_classes) > 1:
        click.echo(f"警告: 文件包含多个 Strategy 子类，使用第一个: {path}", err=True)

    return strategy_classes[0]


def _load_combo_strategies(combo_strategies: str) -> list[type]:
    """从逗号分隔的路径列表加载多个策略类。

    Args:
        combo_strategies: 逗号分隔的策略文件路径

    Returns:
        Strategy 子类列表
    """
    paths = [p.strip() for p in combo_strategies.split(",") if p.strip()]
    if len(paths) < 2:
        click.echo("错误: --combo-strategies 至少需要 2 个策略文件", err=True)
        raise SystemExit(1)

    classes: list[type] = []
    for p in paths:
        cls = _load_strategy_from_file(p)
        classes.append(cls)

    names = [c.__name__ for c in classes]
    click.echo(f"[*] 多因子组合 ({len(classes)} 因子): {' + '.join(names)}")
    return classes


def _print_table(result: Any, df: Any = None) -> None:
    """以表格形式输出回测结果。"""
    perf = result.performance
    config = result.config

    # 计算买入持有收益
    strategy_return = perf.get('total_return', 0)
    buy_hold_return = 0.0
    if df is not None and len(df) >= 2:
        close_col = 'close' if 'close' in df.columns else None
        if close_col:
            first_close = float(df[close_col].iloc[0])
            last_close = float(df[close_col].iloc[-1])
            if first_close > 0:
                buy_hold_return = (last_close / first_close) - 1
    excess_return = strategy_return - buy_hold_return

    click.echo("=== 回测绩效概要 ===")
    click.echo(f"策略累计收益: {strategy_return:.2%}")
    click.echo(f"买入持有收益: {buy_hold_return:.2%}")
    click.echo(f"超额收益: {excess_return:.2%}")
    click.echo(f"年化收益: {perf.get('annual_return', 0):.2%}")
    click.echo(f"最大回撤: {perf.get('max_drawdown', 0):.2%}")
    click.echo(f"夏普比率: {perf.get('sharpe', 0):.2f}")
    click.echo(f"胜率: {perf.get('win_rate', 0):.2%}")
    click.echo(f"交易次数: {perf.get('total_trades', 0)}")
    click.echo()

    if getattr(result, "diagnostic", None):
        click.echo(f"⚠ 诊断: {result.diagnostic}")
        click.echo()

    click.echo("=== 配置参数 ===")
    click.echo(f"初始资金: {config.get('cash', 0):.2f}")
    click.echo(f"佣金率: {config.get('commission', 0):.4f}")
    click.echo(f"成交规则: {config.get('execution', 'next_open')}")
    if config.get("chanlun_level"):
        click.echo(f"缠论级别: {config.get('chanlun_level')}")
    click.echo()

    if config.get("future_leak_warning"):
        click.echo("!!! 警告: 策略可能存在未来函数（使用未来数据）")
        click.echo()

    if not result.trades.empty:
        click.echo("=== 最近交易记录 ===")
        recent_trades = result.trades
        for idx, trade in recent_trades.iterrows():
            direction = "买入" if trade["direction"] == "BUY" else "卖出"
            status = "拒绝" if trade["rejected"] else "成交"
            reason = trade.get("reason", "")
            reason_str = f" {reason}" if reason else ""
            click.echo(
                f"  [{trade['datetime']}] {direction} "
                f"数量={trade['size']:.0f} 价格={trade['price']:.2f} "
                f"盈亏={trade['pnl']:.2f} [{status}]{reason_str}"
            )
    else:
        click.echo("无交易记录")


# ── portfolio 多标的组合回测命令 ─────────────────────────────────────────────


@click.command()
@click.option(
    "--stocks",
    required=True,
    help="股票列表：逗号分隔的 市场:代码（如 SZ:000001,SH:600519,SH:600036）",
)
@click.option("--strategy-file", "strategy_file", required=True, help="Python 策略文件路径")
@click.option("--cash", default=200_000.0, type=float, help="总资金（默认 20 万）")
@click.option("--commission", default=0.0003, type=float, help="佣金率")
@click.option(
    "--execution",
    default="next_open",
    type=click.Choice(["next_open", "next_close"]),
    help="成交价规则",
)
@click.option("--period", default="DAILY", help="K线周期")
@click.option("--adjust", default="NONE", help="复权: NONE/QFQ/HFQ")
@click.option("--count", default=500, type=int, help="K线数量")
@click.option(
    "--allocation",
    default="equal",
    type=click.Choice(["equal"], case_sensitive=False),
    help="资金分配方式（默认 equal 均等分配）",
)
@click.option(
    "--chanlun-level",
    "chanlun_level",
    default="DAILY",
    help="自动计算缠论分析并注入策略（如 DAILY/30MIN），默认 DAILY",
)
@click.option("--table", "use_table", is_flag=True, help="表格输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table", "csv"]), default="json")
def portfolio(
    stocks: str,
    strategy_file: str,
    cash: float,
    commission: float,
    execution: str,
    period: str,
    adjust: str,
    count: int,
    allocation: str,
    chanlun_level: str,
    use_table: bool,
    output_fmt: str,
) -> None:
    """多标的组合回测：共享资金池，独立产生信号，统一管理仓位。

    对多只股票同时回测，按均等比例分配资金，汇总组合整体绩效。

    示例：

      easy-tdx portfolio --stocks SZ:000001,SH:600519 --strategy-file ma_cross.py

      easy-tdx portfolio --stocks SZ:000001,SH:600519,SH:600036 \\
        --strategy-file my_strategy.py --cash 500000 --table

      easy-tdx portfolio --stocks SZ:000001,SH:600519 \\
        --strategy-file chanlun_strat.py --chanlun-level DAILY
    """
    import json

    from ..cli.conn import get_mac_client
    from ..cli.parsers import parse_adjust, parse_market, parse_period
    from .portfolio_engine import PortfolioBacktestEngine, StockData

    # 1. 加载策略
    strategy_cls = _load_strategy_from_file(strategy_file)
    strategy_name = strategy_cls.__name__

    # 2. 解析股票列表
    stock_list = []
    for item in stocks.split(","):
        item = item.strip()
        if ":" not in item:
            click.echo(f"错误: 股票格式应为 市场:代码，如 SZ:000001，收到: {item}", err=True)
            raise SystemExit(1)
        mkt_str, code = item.split(":", 1)
        stock_list.append((mkt_str.strip().upper(), code.strip()))

    if not stock_list:
        click.echo("错误: 未指定股票", err=True)
        raise SystemExit(1)

    click.echo(f"策略: {strategy_name} | 标的: {len(stock_list)} 只 | 资金: {cash:,.0f}", err=True)

    # 3. 获取数据
    stock_data_list: list[StockData] = []
    with get_mac_client() as client:
        for mkt_str, code in stock_list:
            mkt = parse_market(mkt_str)
            df = client.get_stock_kline(
                mkt,
                code,
                period=parse_period(period),
                start=0,
                count=count,
                adjust=parse_adjust(adjust),
            )
            stock_data_list.append(StockData(code=code, market=mkt_str, df=df))

    # 4. 创建引擎并运行
    engine = PortfolioBacktestEngine(
        strategy=strategy_cls,
        stocks=stock_data_list,
        total_cash=cash,
        allocation=allocation,
        commission=commission,
        execution=execution,
        chanlun_level=chanlun_level,
    )
    result = engine.run()

    # 5. 输出结果
    fmt = "table" if use_table else output_fmt
    if fmt == "table":
        _print_portfolio_table(result)
    else:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def _print_portfolio_table(result: Any) -> None:
    """以表格形式输出组合回测结果。"""
    perf = result.total_performance

    click.echo("=== 组合回测绩效概要 ===")
    click.echo(f"标的数量: {perf.get('total_stocks', 0)}")
    click.echo(f"总资金: {perf.get('total_cash', 0):,.0f}")
    click.echo(f"组合收益率: {perf.get('total_return', 0):.2%}")
    click.echo(f"组合年化: {perf.get('annual_return', 0):.2%}")
    click.echo()

    click.echo("── 各标的详情 ──")
    for key, stock_result in result.individual_results.items():
        sp = stock_result.performance
        alloc = result.equity_allocation.get(key, 0)
        click.echo(
            f"  {key}: 收益={sp.get('total_return', 0):.2%} "
            f"夏普={sp.get('sharpe', 0):.2f} "
            f"回撤={sp.get('max_drawdown', 0):.2%} "
            f"分配={alloc:.0%} "
            f"交易={sp.get('total_trades', 0)}"
        )
    click.echo()


# ── backtest-wencai 问财选股批量回测命令 ──────────────────────────────────────


@click.command("backtest-wencai")
@click.argument("query")
@click.option("--strategy-file", "strategy_file", default=None, help="Python 策略文件路径")
@click.option("--top", default=10, type=int, help="取问财结果前 N 只标的（默认 10，最大 50）")
@click.option("--cash", default=100_000.0, type=float, help="每只股票初始资金")
@click.option("--commission", default=0.0003, type=float, help="佣金率")
@click.option(
    "--execution",
    default="next_open",
    type=click.Choice(["next_open", "next_close"]),
    help="成交价规则",
)
@click.option(
    "--position-mode",
    "position_mode",
    default="full",
    type=click.Choice(["full", "fixed", "percent"], case_sensitive=False),
    help="仓位模式：full=全仓 / fixed=指定股数 / percent=百分比（默认 full）",
)
@click.option(
    "--warmup-bars",
    "warmup_bars",
    default=0,
    type=int,
    help="指标预热 bar 数，前 N 根不产生信号（默认 0）",
)
@click.option("--period", default="DAILY", help="K线周期")
@click.option("--adjust", default="NONE", help="复权: NONE/QFQ/HFQ")
@click.option("--count", default=250, type=int, help="K线数量")
@click.option(
    "--chanlun-level",
    "chanlun_level",
    default=None,
    help="自动计算缠论分析并注入策略（如 DAILY/30MIN）。仅缠论策略需要，默认不启用。",
)
@click.option("--cookie", default=None, help="问财 Cookie（不传时自动读取配置）")
@click.option("--table", "use_table", is_flag=True, default=True, help="表格输出（默认）")
@click.option("--json", "use_json", is_flag=True, default=False, help="JSON 输出")
@click.option("--output", "output_fmt", type=click.Choice(["json", "table"]), default=None)
def backtest_wencai(
    query: str,
    strategy_file: str | None,
    top: int,
    cash: float,
    commission: float,
    execution: str,
    position_mode: str,
    warmup_bars: int,
    period: str,
    adjust: str,
    count: int,
    chanlun_level: str | None,
    cookie: str | None,
    use_table: bool,
    use_json: bool,
    output_fmt: str | None,
) -> None:
    """问财选股批量回测：用问财语义搜索选股，逐个独立回测并汇总统计。

    输入问财自然语言查询语句，先调问财搜索获取前 ``top`` 只标的，
    再逐个独立回测（每只股票各自独立跑策略，非组合分仓），返回每只的
    绩效摘要 + 汇总统计。适用于横向对比哪些股票适合该策略。

    QUERY: 问财自然语言查询语句（如 "今日涨停"、"白酒行业"）

    示例：

      easy-tdx backtest-wencai "今日涨停" --top 10 --strategy-file strategies/shadow_yang.py

      easy-tdx backtest-wencai "白酒行业" --top 15 --strategy-file strategies/shadow_yang_v2.py
    """
    import json as json_module

    from ..backtest.engine import BacktestEngine
    from ..cli.conn import get_mac_client
    from ..cli.parsers import parse_adjust, parse_market, parse_period
    from ..wencai import WencaiClient, WencaiError, filter_tradable

    # 1. 校验策略文件
    if not strategy_file:
        click.echo("错误: 必须指定 --strategy-file", err=True)
        raise SystemExit(1)

    strategy_cls = _load_strategy_from_file(strategy_file)
    strategy_name = strategy_cls.__name__

    # 2. 校验 top 范围
    if top < 1 or top > 50:
        click.echo("错误: --top 范围应为 1-50", err=True)
        raise SystemExit(1)

    click.echo(f"[*] 问财查询: {query} | 取前 {top} 只 | 策略: {strategy_name}", err=True)

    # 3. 问财搜索（filter_tradable 排除 ST/科创板/北交所）
    try:
        wencai_client = WencaiClient(cookie=cookie)
        stocks = filter_tradable(wencai_client.search(query, perpage=max(top, 100)))
    except WencaiError as e:
        click.echo(f"错误: 问财搜索失败: {e}", err=True)
        raise SystemExit(1)

    stocks = stocks[:top]
    if not stocks:
        click.echo(f"错误: 问财未返回任何有效标的，查询语句: {query}", err=True)
        raise SystemExit(1)

    click.echo(f"[*] 问财返回 {len(stocks)} 只标的，开始逐个回测", err=True)

    # 4. 逐个取行情 + 独立回测（单个失败不中断整组）
    results: list[dict[str, Any]] = []
    with get_mac_client() as tdx_client:
        for stock in stocks:
            symbol, market, name = stock.symbol, stock.market, stock.name
            try:
                mkt = parse_market(market)
                df = tdx_client.get_stock_kline(
                    mkt,
                    symbol,
                    period=parse_period(period),
                    start=0,
                    count=count,
                    adjust=parse_adjust(adjust),
                )
                if len(df) < 2:
                    raise ValueError(f"K线数据不足: {len(df)} 根")

                # 每只新建策略实例，避免内部状态跨标的污染
                engine = BacktestEngine(
                    strategy=strategy_cls,
                    cash=cash,
                    commission=commission,
                    execution=execution,
                    position_mode=position_mode,
                    chanlun_level=chanlun_level,
                    warmup_bars=warmup_bars,
                )
                result = engine.run(df)
                results.append(
                    {
                        "symbol": symbol,
                        "market": market,
                        "name": name,
                        "performance": result.performance,
                        "error": None,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "symbol": symbol,
                        "market": market,
                        "name": name,
                        "performance": {},
                        "error": str(e),
                    }
                )

    # 5. 构建汇总统计
    summary = _build_wencai_summary(results)
    output_data: dict[str, Any] = {
        "query": query,
        "top": top,
        "strategy": strategy_name,
        "stocks_searched": len(stocks),
        "stocks_backtested": sum(1 for r in results if r["error"] is None),
        "results": results,
        "summary": summary,
    }

    # 6. 输出结果（默认 table）
    if use_json:
        fmt = "json"
    elif output_fmt:
        fmt = output_fmt
    else:
        fmt = "table"

    if fmt == "json":
        click.echo(json_module.dumps(output_data, ensure_ascii=False, indent=2, default=str))
    else:
        _print_wencai_table(output_data)


def _build_wencai_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """根据逐个回测结果列表构建汇总统计。"""
    valid = [r for r in results if r.get("error") is None and r.get("performance")]
    if not valid:
        return {
            "avg_return": 0.0,
            "avg_sharpe": 0.0,
            "avg_max_drawdown": 0.0,
            "positive_count": 0,
            "negative_count": 0,
        }

    returns = [r["performance"].get("total_return", 0) for r in valid]
    sharpes = [r["performance"].get("sharpe", 0) for r in valid]
    drawdowns = [r["performance"].get("max_drawdown", 0) for r in valid]
    best = max(valid, key=lambda r: r["performance"].get("total_return", 0))
    worst = min(valid, key=lambda r: r["performance"].get("total_return", 0))

    return {
        "avg_return": sum(returns) / len(returns),
        "avg_sharpe": sum(sharpes) / len(sharpes),
        "avg_max_drawdown": sum(drawdowns) / len(drawdowns),
        "positive_count": sum(1 for r in returns if r > 0),
        "negative_count": sum(1 for r in returns if r <= 0),
        "best": {
            "symbol": best["symbol"],
            "name": best["name"],
            "return": best["performance"].get("total_return", 0),
        },
        "worst": {
            "symbol": worst["symbol"],
            "name": worst["name"],
            "return": worst["performance"].get("total_return", 0),
        },
    }


def _print_wencai_table(data: dict[str, Any]) -> None:
    """以表格形式输出问财批量回测结果。"""
    click.echo("=== 问财选股批量回测 ===")
    click.echo(f"查询语句: {data['query']}")
    click.echo(f"策略: {data['strategy']}")
    click.echo(
        f"搜索标的: {data['stocks_searched']} | 成功回测: {data['stocks_backtested']}"
    )
    click.echo()

    click.echo("── 各标的绩效 ──")
    for r in data["results"]:
        if r.get("error"):
            click.echo(f"  {r['market']}:{r['symbol']} {r['name']}: 错误 - {r['error']}")
            continue
        perf = r.get("performance", {})
        ret = perf.get("total_return", 0)
        sharpe = perf.get("sharpe", 0)
        dd = perf.get("max_drawdown", 0)
        trades = perf.get("total_trades", 0)
        click.echo(
            f"  {r['market']}:{r['symbol']} {r['name']}: "
            f"收益={ret:.2%} 夏普={sharpe:.2f} 回撤={dd:.2%} 交易={trades}"
        )
    click.echo()

    summary = data.get("summary", {})
    click.echo("── 汇总统计 ──")
    click.echo(f"平均收益: {summary.get('avg_return', 0):.2%}")
    click.echo(f"平均夏普: {summary.get('avg_sharpe', 0):.2f}")
    click.echo(f"平均回撤: {summary.get('avg_max_drawdown', 0):.2%}")
    click.echo(
        f"盈利: {summary.get('positive_count', 0)} | "
        f"亏损: {summary.get('negative_count', 0)}"
    )
    if "best" in summary:
        best = summary["best"]
        click.echo(f"最佳: {best['name']} ({best['return']:.2%})")
    if "worst" in summary:
        worst = summary["worst"]
        click.echo(f"最差: {worst['name']} ({worst['return']:.2%})")
