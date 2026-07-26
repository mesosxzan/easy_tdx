"""多股回测对比：shadow_yang_v2 修改前后绩效对比。"""

import json
import subprocess
import sys

STOCKS = ["600403", "600206", "002407", "600539", "002156", "000001", "600519", "000858"]
STRATEGY = "strategies/shadow_yang_v2.py"
COUNT = 300


def run_backtest(code: str) -> dict:
    """运行单只个股回测，返回关键指标。"""
    result = subprocess.run(
        [sys.executable, "-m", "easy_tdx", "backtest", code,
         "--strategy-file", STRATEGY, "--count", str(COUNT), "--json"],
        capture_output=True, text=True, cwd="/Users/a1-6/project/easy_tdx",
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": result.stdout[:200] + result.stderr[:200]}
    perf = data.get("performance", {})
    trades = data.get("trades", [])
    # 配对买卖，计算每笔收益
    trade_pnls = []
    for i in range(0, len(trades) - 1, 2):
        if trades[i]["direction"] == "BUY" and i + 1 < len(trades):
            buy_p = trades[i]["price"]
            sell_p = trades[i + 1]["price"]
            pnl = (sell_p - buy_p) / buy_p * 100
            trade_pnls.append(round(pnl, 1))
    return {
        "return": round(perf.get("total_return", 0) * 100, 2),
        "trades": perf.get("total_trades", 0),
        "win_rate": round(perf.get("win_rate", 0) * 100, 0),
        "profit_factor": round(perf.get("profit_factor", 0), 2),
        "max_dd": round(perf.get("max_drawdown", 0) * 100, 1),
        "pnl_list": trade_pnls,
    }


def main() -> None:
    print(f"{'股票':<8}{'收益%':>8}{'交易数':>6}{'胜率%':>6}{'盈亏比':>7}{'最大回撤%':>9}  每笔收益%")
    print("-" * 80)
    total_return = 0
    total_trades = 0
    all_pnls = []
    for code in STOCKS:
        r = run_backtest(code)
        if "error" in r:
            print(f"{code:<8}  ERROR: {r['error'][:60]}")
            continue
        total_return += r["return"]
        total_trades += r["trades"]
        all_pnls.extend(r["pnl_list"])
        pnl_str = str(r["pnl_list"])
        print(f"{code:<8}{r['return']:>8.2f}{r['trades']:>6}{r['win_rate']:>6.0f}"
              f"{r['profit_factor']:>7.2f}{r['max_dd']:>9.1f}  {pnl_str}")
    print("-" * 80)
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p < 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    print(f"{'合计':<8}{total_return:>8.2f}{total_trades:>6}"
          f"{len(wins)/len(all_pnls)*100 if all_pnls else 0:>6.0f}"
          f"{sum(wins)/abs(sum(losses)) if losses and sum(losses)!=0 else 0:>7.2f}"
          f"{'':>9}  胜{len(wins)}负{len(losses)} 均赚{avg_win:.1f}% 均亏{avg_loss:.1f}%")


if __name__ == "__main__":
    main()
