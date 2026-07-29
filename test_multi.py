"""快速多股回测脚本。"""
import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from easy_tdx.backtest.engine import BacktestEngine
from easy_tdx.backtest.strategy import Strategy
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_period, parse_adjust

STOCKS = [
    "600206", "002407", "600539", "603127", "002156",
    "000001", "600550", "000858", "300207", "600403",
    "002415", "600519", "000333", "601318", "600036",
    "300059", "002594", "300750", "600030", "000002",
    "000568", "600809", "000895", "600276", "601899",
    "600009", "600048", "601668", "601398", "601988",
]

STRATEGY_FILE = "strategies/shadow_yang_v2.py"
BAR_COUNT = 1200
CASH = 100000

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

total_profit_pct = 0.0
total_bh_profit_pct = 0.0
total_trades = 0
stocks_won = 0
stocks_lost = 0
zero_trade = 0

print(f"{'代码':<10}{'策略收益':>10}{'买入持有':>10}{'超额收益':>10}{'胜率':>8}{'交易数':>6}{'最大回撤':>10}{'夏普':>8}")
print("-" * 80)

with get_mac_client() as client:
    for code in STOCKS:
        try:
            market = "SH" if code.startswith("6") else "SZ"
            mkt = parse_market(market)
            df = client.get_stock_kline(
                mkt, code, period=parse_period("DAILY"),
                start=0, count=BAR_COUNT, adjust=parse_adjust("QFQ"),
            )
            engine = BacktestEngine(
                strategy=strategy_class,
                cash=CASH,
                commission=0.0003,
                execution="next_open",
                warmup_bars=60,
            )
            result = engine.run(df)
            perf = result.performance
            start_cash = perf["start_cash"]
            end_value = perf["end_value"]
            profit = (end_value - start_cash) / start_cash * 100 if start_cash > 0 else 0
            win_rate = perf["win_rate"] * 100 if perf["total_trades"] > 0 else 0
            n_trades = perf["total_trades"]
            max_dd = abs(perf["max_drawdown"]) / start_cash * 100 if start_cash > 0 else 0
            sharpe = perf.get("sharpe_ratio", perf.get("sharpe", 0.0))
            bh = 0.0
            excess = profit - bh
            print(f"{code:<10}{profit:>9.2f}%{bh:>9.2f}%{excess:>9.2f}%{win_rate:>7.1f}%{n_trades:>6}{max_dd:>9.2f}%{sharpe:>8.2f}")
            total_profit_pct += profit
            total_bh_profit_pct += bh
            total_trades += n_trades
            if profit > 0:
                stocks_won += 1
            else:
                stocks_lost += 1
            if n_trades == 0:
                zero_trade += 1
        except Exception as e:
            print(f"{code:<10}  ERROR: {e}")

print("-" * 80)
n = len(STOCKS)
avg_profit = total_profit_pct / n
avg_bh = total_bh_profit_pct / n
avg_excess = avg_profit - avg_bh
print(f"平均收益: {avg_profit:.2f}% | 平均买持: {avg_bh:.2f}% | 平均超额: {avg_excess:.2f}%")
print(f"盈利股票数: {stocks_won}/{n} | 亏损股票数: {stocks_lost}/{n}")
print(f"零交易股票数: {zero_trade}")
print(f"总交易数: {total_trades}")
