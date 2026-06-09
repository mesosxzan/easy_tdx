"""批量回测脚本：依次运行 strategies/ 目录下所有策略并比较结果。

用法::

    python run_all_strategies.py SZ 300308 --count 2000 --cash 1000000 --adjust QFQ

输出每个策略的绩效指标，并按总收益率排名。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 确保 easy_tdx 可导入
sys.path.insert(0, str(Path(__file__).parent / "src"))

import click


@click.command()
@click.argument("market")
@click.argument("code")
@click.option("--count", default=2000, type=int, help="K线数量")
@click.option("--cash", default=1000000.0, type=float, help="初始资金")
@click.option("--commission", default=0.0003, type=float, help="佣金率")
@click.option("--adjust", default="QFQ", help="复权: NONE/QFQ/HFQ")
@click.option("--period", default="DAILY", help="K线周期")
def run_all(
    market: str,
    code: str,
    count: int,
    cash: float,
    commission: float,
    adjust: str,
    period: str,
) -> None:
    """批量运行 strategies/ 目录下所有策略并比较结果。"""
    from easy_tdx.backtest.engine import BacktestEngine
    from easy_tdx.backtest.strategy import Strategy
    from easy_tdx.cli.parsers import parse_adjust, parse_market, parse_period
    from easy_tdx.mac.client import MacClient

    # 1. 发现策略文件
    strategies_dir = Path(__file__).parent / "strategies"
    strategy_files = sorted(strategies_dir.glob("*.py"))
    if not strategy_files:
        click.echo("未找到策略文件 (strategies/*.py)", err=True)
        raise SystemExit(1)

    click.echo(f"发现 {len(strategy_files)} 个策略文件")
    click.echo(f"标的: {market} {code} | K线: {count} | 资金: {cash:,.0f} | 复权: {adjust}")
    click.echo("=" * 80)

    # 2. 获取数据（所有策略共享同一份数据）
    mkt = parse_market(market)
    click.echo("正在获取行情数据...")
    client = MacClient.from_best_host()
    client.connect()
    try:
        df = client.get_stock_kline(
            mkt,
            code,
            period=parse_period(period),
            start=0,
            count=count,
            adjust=parse_adjust(adjust),
        )
    finally:
        client.close()
    click.echo(f"获取到 {len(df)} 条K线数据")
    click.echo("=" * 80)

    # 3. 逐个运行策略
    results: list[dict] = []

    for sf in strategy_files:
        strategy_name = sf.stem
        click.echo(f"\n>> 运行策略: {strategy_name} ...", nl=False)

        # 加载策略类
        import importlib.util

        spec = importlib.util.spec_from_file_location("strategy_module", sf)
        if spec is None or spec.loader is None:
            click.echo(" [加载失败]")
            continue

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 查找 Strategy 子类
        strategy_cls = None
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            try:
                if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                    strategy_cls = obj
                    break
            except TypeError:
                pass

        if strategy_cls is None:
            click.echo(" [未找到 Strategy 子类]")
            continue

        # 运行回测
        t0 = time.perf_counter()
        try:
            engine = BacktestEngine(
                strategy=strategy_cls,
                cash=cash,
                commission=commission,
            )
            result = engine.run(df)
            elapsed = time.perf_counter() - t0
            perf = result.performance
            click.echo(f" 完成 ({elapsed:.1f}s)")

            results.append({
                "strategy": strategy_name,
                "total_return": perf.get("total_return", 0),
                "annual_return": perf.get("annual_return", 0),
                "max_drawdown": perf.get("max_drawdown", 0),
                "sharpe": perf.get("sharpe", 0),
                "sortino": perf.get("sortino", 0),
                "calmar": perf.get("calmar", 0),
                "win_rate": perf.get("win_rate", 0),
                "total_trades": perf.get("total_trades", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "volatility": perf.get("volatility", 0),
            })
        except Exception as e:
            elapsed = time.perf_counter() - t0
            click.echo(f" 错误 ({elapsed:.1f}s): {e}")
            results.append({
                "strategy": strategy_name,
                "error": str(e),
            })

    # 4. 输出排名
    click.echo("\n" + "=" * 80)
    click.echo("[*] 策略绩效排名 (按总收益率降序)")
    click.echo("=" * 80)

    # 过滤掉有错误的策略
    valid = [r for r in results if "error" not in r]
    errored = [r for r in results if "error" in r]

    if not valid:
        click.echo("所有策略均运行失败！")
        for r in errored:
            click.echo(f"  {r['strategy']}: {r['error']}")
        raise SystemExit(1)

    # 按总收益率排序
    valid.sort(key=lambda x: x["total_return"], reverse=True)

    # 表头
    click.echo(
        f"{'排名':>4}  {'策略':<22} {'总收益率':>10} {'年化收益':>10} "
        f"{'最大回撤':>10} {'夏普':>8} {'胜率':>8} {'交易次数':>8} {'盈亏比':>8}"
    )
    click.echo("-" * 100)

    for i, r in enumerate(valid, 1):
        medal = " *1*" if i == 1 else " *2*" if i == 2 else " *3*" if i == 3 else "    "
        click.echo(
            f"{medal}{i:>2}  {r['strategy']:<22} "
            f"{r['total_return']:>9.2%} "
            f"{r['annual_return']:>9.2%} "
            f"{r['max_drawdown']:>9.2%} "
            f"{r['sharpe']:>8.2f} "
            f"{r['win_rate']:>7.1%} "
            f"{r['total_trades']:>8} "
            f"{r['profit_factor']:>8.2f}"
        )

    # 最佳策略详细报告
    best = valid[0]
    click.echo("\n" + "=" * 80)
    click.echo(f"[BEST] 最佳策略: {best['strategy']}")
    click.echo("=" * 80)
    click.echo(f"  总收益率:   {best['total_return']:.2%}")
    click.echo(f"  年化收益:   {best['annual_return']:.2%}")
    click.echo(f"  最大回撤:   {best['max_drawdown']:.2%}")
    click.echo(f"  夏普比率:   {best['sharpe']:.2f}")
    click.echo(f"  索提诺:     {best['sortino']:.2f}")
    click.echo(f"  卡玛比率:   {best['calmar']:.2f}")
    click.echo(f"  胜率:       {best['win_rate']:.1%}")
    click.echo(f"  交易次数:   {best['total_trades']}")
    click.echo(f"  盈亏比:     {best['profit_factor']:.2f}")
    click.echo(f"  年化波动:   {best['volatility']:.4f}")

    # 综合评分（综合夏普、收益率、回撤）
    click.echo("\n" + "=" * 80)
    click.echo("[*] 综合评分排名 (Sharpe*0.4 + Ret/DD*0.3 + WinRate*0.3)")
    click.echo("=" * 80)

    scored = []
    for r in valid:
        # 避免除以零
        ret_dd_ratio = r["annual_return"] / r["max_drawdown"] if r["max_drawdown"] > 1e-6 else 999.0
        score = r["sharpe"] * 0.4 + ret_dd_ratio * 0.3 + r["win_rate"] * 100 * 0.3
        scored.append((r, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    click.echo(
        f"{'排名':>4}  {'策略':<22} {'综合评分':>10} {'夏普':>8} {'收益/回撤':>10} {'胜率':>8}"
    )
    click.echo("-" * 70)
    for i, (r, score) in enumerate(scored, 1):
        ret_dd_ratio = r["annual_return"] / r["max_drawdown"] if r["max_drawdown"] > 1e-6 else 999.0
        medal = " *1*" if i == 1 else " *2*" if i == 2 else " *3*" if i == 3 else "    "
        click.echo(
            f"{medal}{i:>2}  {r['strategy']:<22} {score:>10.2f} "
            f"{r['sharpe']:>8.2f} {ret_dd_ratio:>10.2f} {r['win_rate']:>7.1%}"
        )

    # 报告错误
    if errored:
        click.echo("\n[!] 以下策略运行失败:")
        for r in errored:
            click.echo(f"  {r['strategy']}: {r['error']}")


if __name__ == "__main__":
    run_all()
