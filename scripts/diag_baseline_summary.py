"""8股 + 600550 基线回测汇总。"""
import json
import subprocess

STOCKS = [
    ("600403", "1"), ("600206", "1"), ("002407", "0"), ("600539", "1"),
    ("002156", "0"), ("000001", "0"), ("600519", "1"), ("000858", "0"),
    ("600550", "1"),
]


def run_backtest(code):
    result = subprocess.run(
        ["python3", "-m", "easy_tdx", "backtest", code,
         "--strategy-file", "strategies/shadow_yang_v2.py",
         "--count", "300", "--adjust", "qfq", "--json"],
        capture_output=True, text=True, cwd="/Users/a1-6/project/easy_tdx",
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  {code} JSON解析失败: {result.stderr[:200]}")
        return {}


def summarize(code, data):
    trades = data.get('trades', [])
    perf = data.get('performance', {})
    total_return = perf.get('total_return', 0) * 100
    n_trades = perf.get('total_trades', len(trades) // 2)
    n_wins = sum(1 for t in trades if t['direction'] == 'SELL' and t.get('reason', '') != '' and t.get('price', 0) > 0)
    # 计算盈亏
    pairs = []
    i = 0
    while i < len(trades) - 1:
        if trades[i]['direction'] == 'BUY' and trades[i+1]['direction'] == 'SELL':
            buy_p = trades[i]['price']
            sell_p = trades[i+1]['price']
            pnl = (sell_p - buy_p) / buy_p * 100
            pairs.append((trades[i+1].get('reason', ''), pnl))
            i += 2
        else:
            i += 1
    wins = sum(1 for _, p in pairs if p > 0)
    win_rate = wins / len(pairs) * 100 if pairs else 0
    lockin_count = sum(1 for r, _ in pairs if r == '盈利锁定')
    return total_return, n_trades, win_rate, lockin_count, pairs


print("=" * 90)
print(f"{'代码':<8} {'总收益':>10} {'交易数':>6} {'胜率':>8} {'盈利锁定':>8}")
print("=" * 90)
total = 0
all_pairs = []
for code, market in STOCKS:
    data = run_backtest(code)
    if not data:
        continue
    tr, nt, wr, lc, pairs = summarize(code, data)
    total += tr
    all_pairs.extend([(code, r, p) for r, p in pairs])
    print(f"{code:<8} {tr:>+9.2f}% {nt:>6} {wr:>7.1f}% {lc:>8}")

print("-" * 90)
print(f"{'合计':<8} {total:>+9.2f}%")

# 盈利锁定交易明细
print(f"\n── 盈利锁定交易明细 ──")
lockin_trades = [(c, r, p) for c, r, p in all_pairs if r == '盈利锁定']
for c, r, p in lockin_trades:
    print(f"  {c}: {p:+.2f}%")
print(f"盈利锁定交易数: {len(lockin_trades)}, "
      f"平均{sum(p for _,_,p in lockin_trades)/len(lockin_trades):+.2f}%" if lockin_trades else "")
