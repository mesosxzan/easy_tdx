"""深度分析 shadow_yang_v2 策略的交易特征。"""
import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from easy_tdx.backtest.engine import BacktestEngine
from easy_tdx.backtest.strategy import Strategy
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_period, parse_adjust
import pandas as pd
import numpy as np

STOCKS = [
    "600206", "002407", "600539", "603127", "002156",
    "000001", "600550", "000858", "300207", "600403",
    "002415", "000333", "601318", "600036",
    "300059", "002594", "300750", "600030", "000002",
    "000568", "000895", "600276", "601899",
    "600009", "600048", "601668", "601398", "601988",
]

STRATEGY_FILE = "strategies/shadow_yang_v2.py"

def load_strategy(path):
    spec = importlib.util.spec_from_file_location("strategy_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
            return obj
    raise RuntimeError("No Strategy subclass found")

strategy_class = load_strategy(STRATEGY_FILE)

all_trades = []

with get_mac_client() as client:
    for code in STOCKS:
        try:
            market = "SH" if code.startswith("6") else "SZ"
            mkt = parse_market(market)
            df = client.get_stock_kline(
                mkt, code, period=parse_period("DAILY"),
                start=0, count=1200, adjust=parse_adjust("QFQ"),
            )
            engine = BacktestEngine(
                strategy=strategy_class,
                cash=100000,
                commission=0.0003,
                execution="next_open",
                warmup_bars=60,
            )
            result = engine.run(df)
            trades = result.trades
            if len(trades) > 0:
                sells = trades[trades["direction"] == "SELL"].copy()
                if len(sells) > 0:
                    sells["code"] = code
                    sells["return_pct"] = sells["pnl"] / sells["cost_basis"] * 100
                    all_trades.append(sells)
        except Exception as e:
            print(f"{code} ERROR: {e}")

if not all_trades:
    print("No trades")
    sys.exit(0)

df_all = pd.concat(all_trades, ignore_index=True)

print(f"\n{'='*60}")
print(f"总交易分析（{len(df_all)} 笔完整交易）")
print(f"{'='*60}")

# 基本统计
wins = df_all[df_all["pnl"] > 0]
losses = df_all[df_all["pnl"] <= 0]
print(f"胜率: {len(wins)/len(df_all)*100:.1f}% ({len(wins)}胜/{len(losses)}负)")
print(f"平均盈利: {wins['return_pct'].mean():.2f}% (中位数 {wins['return_pct'].median():.2f}%)")
print(f"平均亏损: {losses['return_pct'].mean():.2f}% (中位数 {losses['return_pct'].median():.2f}%)")
print(f"盈亏比: {abs(wins['return_pct'].mean() / losses['return_pct'].mean()):.2f}")
print(f"最大盈利: {wins['return_pct'].max():.2f}%")
print(f"最大亏损: {losses['return_pct'].min():.2f}%")
pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 else float('inf')
print(f"利润因子(PF): {pf:.2f}")

# 按盈利幅度分桶
print(f"\n--- 盈利分布分桶 ---")
bins = [-100, -8, -5, -3, 0, 3, 6, 10, 15, 25, 50, 200]
labels = ["<-8%", "-8~-5%", "-5~-3%", "-3~0%", "0~3%", "3~6%", "6~10%", "10~15%", "15~25%", "25~50%", ">50%"]
df_all["bucket"] = pd.cut(df_all["return_pct"], bins=bins, labels=labels)
bucket_stats = df_all.groupby("bucket", observed=True).agg(
    count=("return_pct", "count"),
    avg_ret=("return_pct", "mean"),
)
print(bucket_stats.to_string())

# 按卖出原因统计（如果有reason列）
if "reason" in df_all.columns:
    print(f"\n--- 卖出原因分布 ---")
    reason_stats = df_all.groupby("reason", dropna=False).agg(
        count=("return_pct", "count"),
        avg_ret=("return_pct", "mean"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
    ).sort_values("count", ascending=False)
    print(reason_stats.to_string())

# 按买入原因统计（从买入记录获取）
print(f"\n--- 买入原因分布（需单独提取）---")
all_buys = []
with get_mac_client() as client:
    for code in STOCKS[:10]:
        try:
            market = "SH" if code.startswith("6") else "SZ"
            mkt = parse_market(market)
            df = client.get_stock_kline(
                mkt, code, period=parse_period("DAILY"),
                start=0, count=1200, adjust=parse_adjust("QFQ"),
            )
            engine = BacktestEngine(
                strategy=strategy_class,
                cash=100000, commission=0.0003, execution="next_open", warmup_bars=60,
            )
            result = engine.run(df)
            trades = result.trades
            if len(trades) > 0:
                buys = trades[trades["direction"] == "BUY"].copy()
                if len(buys) > 0:
                    buys["code"] = code
                    all_buys.append(buys)
        except:
            pass

if all_buys:
    df_buys = pd.concat(all_buys, ignore_index=True)
    if "reason" in df_buys.columns:
        print(df_buys["reason"].value_counts().to_string())

print(f"\n--- 头部亏损交易 ---")
print(df_all.nsmallest(10, "return_pct")[["code", "datetime", "return_pct", "pnl"]].to_string())

print(f"\n--- 头部盈利交易 ---")
print(df_all.nlargest(10, "return_pct")[["code", "datetime", "return_pct", "pnl"]].to_string())
