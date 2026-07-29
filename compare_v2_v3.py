"""对比 V2 和 V3 策略的回测表现。"""
import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from easy_tdx.backtest.engine import BacktestEngine
from easy_tdx.backtest.strategy import Strategy
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_period, parse_adjust
import pandas as pd

STOCKS = [
    "600206", "002407", "600539", "603127", "002156",
    "000001", "600550", "000858", "300207", "600403",
    "002415", "000333", "601318", "600036",
    "300059", "002594", "300750", "600030", "000002",
    "000568", "000895", "600276", "601899",
    "600009", "600048", "601668", "601398", "601988",
]

STRATEGIES = {
    "V2": "strategies/shadow_yang_v2.py",
    "V10": "strategies/shadow_yang_v10.py",
    "V11": "strategies/shadow_yang_v11.py",
    "V12": "strategies/shadow_yang_v12.py",
}

def load_strategy(path):
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            return obj
    raise RuntimeError("No Strategy subclass found")

strategy_classes = {k: load_strategy(v) for k, v in STRATEGIES.items()}

results = {k: [] for k in STRATEGIES}
all_trades = {k: [] for k in STRATEGIES}

with get_mac_client() as client:
    for code in STOCKS:
        try:
            market = "SH" if code.startswith("6") else "SZ"
            mkt = parse_market(market)
            df = client.get_stock_kline(
                mkt, code, period=parse_period("DAILY"),
                start=0, count=1200, adjust=parse_adjust("QFQ"),
            )
            for sname, scls in strategy_classes.items():
                engine = BacktestEngine(
                    strategy=scls, cash=100000, commission=0.0003,
                    execution="next_open", warmup_bars=60,
                )
                result = engine.run(df)
                perf = result.performance
                start_cash = perf["start_cash"]
                end_value = perf["end_value"]
                profit = (end_value - start_cash) / start_cash * 100
                win_rate = perf["win_rate"] * 100 if perf["total_trades"] > 0 else 0
                n_trades = perf["total_trades"]
                max_dd = abs(perf["max_drawdown"]) / start_cash * 100 if start_cash > 0 else 0
                sharpe = perf.get("sharpe_ratio", perf.get("sharpe", 0.0))
                pf = perf.get("profit_factor", 0.0)
                results[sname].append({
                    "code": code, "profit": profit, "win_rate": win_rate,
                    "n_trades": n_trades, "max_dd": max_dd,
                    "sharpe": sharpe, "pf": pf,
                })
                trades = result.trades
                if len(trades) > 0:
                    sells = trades[trades["direction"] == "SELL"].copy()
                    if len(sells) > 0:
                        sells["code"] = code
                        sells["return_pct"] = sells["pnl"] / sells["cost_basis"] * 100
                        all_trades[sname].append(sells)
        except Exception as e:
            print(f"{code} ERROR: {e}")

# 汇总表
print(f"\n{'代码':<10}", end="")
for sname in STRATEGIES:
    print(f"  {sname}收益   {sname}胜率 {sname}交易数", end="")
print()
print("-" * 75)

for i, code in enumerate(STOCKS):
    print(f"{code:<10}", end="")
    for sname in STRATEGIES:
        r = results[sname][i]
        print(f"  {r['profit']:>7.2f}%  {r['win_rate']:>5.1f}%  {r['n_trades']:>4}", end="")
    print()

print("-" * 75)

# 汇总统计
print(f"\n{'指标':<15}", end="")
for sname in STRATEGIES:
    print(f"  {sname:>12}", end="")
print()
print("-" * 55)

for sname in STRATEGIES:
    pass

import numpy as np
metrics = [
    ("平均收益", "profit"),
    ("胜率(平均)", "win_rate"),
    ("交易总数", "n_trades"),
    ("夏普(平均)", "sharpe"),
    ("最大回撤(平均)", "max_dd"),
]

for label, key in metrics:
    print(f"{label:<15}", end="")
    for sname in STRATEGIES:
        vals = [r[key] for r in results[sname]]
        if key == "n_trades":
            print(f"  {sum(vals):>12.0f}", end="")
        else:
            print(f"  {np.mean(vals):>11.2f}%", end="")
    print()

# 盈利股票数
print(f"{'盈利股票数':<15}", end="")
for sname in STRATEGIES:
    wins = sum(1 for r in results[sname] if r["profit"] > 0)
    print(f"  {wins:>7}/{len(STOCKS)}", end="")
print()

# 交易级分析
for sname in STRATEGIES:
    if not all_trades[sname]:
        continue
    df_trades = pd.concat(all_trades[sname], ignore_index=True)
    wins = df_trades[df_trades["pnl"] > 0]
    losses = df_trades[df_trades["pnl"] <= 0]
    print(f"\n--- {sname} 交易级分析（{len(df_trades)}笔）---")
    print(f"  胜率: {len(wins)/len(df_trades)*100:.1f}% ({len(wins)}胜/{len(losses)}负)")
    print(f"  平均盈利: {wins['return_pct'].mean():.2f}%")
    print(f"  平均亏损: {losses['return_pct'].mean():.2f}%")
    print(f"  盈亏比: {abs(wins['return_pct'].mean()/losses['return_pct'].mean()):.2f}")
    pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 else float('inf')
    print(f"  利润因子(PF): {pf:.2f}")

    # 卖出原因分布
    if "reason" in df_trades.columns:
        print(f"  卖出原因:")
        reason_stats = df_trades.groupby("reason", dropna=False).agg(
            count=("return_pct", "count"),
            avg_ret=("return_pct", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("count", ascending=False)
        for reason, row in reason_stats.iterrows():
            print(f"    {reason or '未知':<12} {row['count']:>3}笔  avg {row['avg_ret']:>6.2f}%  胜率 {row['win_rate']:>5.1f}%")

    # 分桶
    bins = [-100, -8, -5, -3, 0, 3, 6, 10, 15, 25, 50, 200]
    labels = ["<-8%", "-8~-5%", "-5~-3%", "-3~0%", "0~3%", "3~6%", "6~10%", "10~15%", "15~25%", "25~50%", ">50%"]
    df_trades["bucket"] = pd.cut(df_trades["return_pct"], bins=bins, labels=labels)
    bucket_stats = df_trades.groupby("bucket", observed=True).agg(count=("return_pct", "count"))
    print(f"  盈利分布: {dict(zip(bucket_stats.index, bucket_stats['count'].astype(int)))}")
