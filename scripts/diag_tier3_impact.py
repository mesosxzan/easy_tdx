"""诊断600550的4笔盈利锁定详情，分析Tier3调整影响。"""
import json
import subprocess
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_adjust, parse_period

PROFIT_LOCKIN_TRIGGER1 = 0.03
PROFIT_LOCKIN_TRIGGER2 = 0.06
PROFIT_LOCKIN_FLOOR2 = 0.02
PROFIT_LOCKIN_TRIGGER3 = 0.10
PROFIT_LOCKIN_FLOOR3 = 0.04  # 当前值
PROFIT_LOCKIN_TRIGGER4 = 0.15
PROFIT_LOCKIN_FLOOR4 = 0.07
PROFIT_LOCKIN_TRIGGER5 = 0.20
PROFIT_LOCKIN_FLOOR5 = 0.10


def calc_floor_new(entry, highest_close, floor3_value):
    """计算盈利锁定价（可指定floor3值）。"""
    if highest_close <= entry:
        return 0.0, None
    max_profit = (highest_close - entry) / entry
    if max_profit >= PROFIT_LOCKIN_TRIGGER5:
        return entry * (1 + PROFIT_LOCKIN_FLOOR5), "Tier5(20%->10%)"
    if max_profit >= PROFIT_LOCKIN_TRIGGER4:
        return entry * (1 + PROFIT_LOCKIN_FLOOR4), "Tier4(15%->7%)"
    if max_profit >= PROFIT_LOCKIN_TRIGGER3:
        return entry * (1 + floor3_value), f"Tier3(10%->{floor3_value*100:.0f}%)"
    if max_profit >= PROFIT_LOCKIN_TRIGGER2:
        return entry * (1 + PROFIT_LOCKIN_FLOOR2), "Tier2(6%->2%)"
    if max_profit >= PROFIT_LOCKIN_TRIGGER1:
        return entry, "Tier1(3%->0%)"
    return 0.0, None


# 加载600550数据
with get_mac_client() as client:
    df = client.get_stock_kline(
        parse_market("1"), "600550",
        period=parse_period("daily"), start=0, count=300,
        adjust=parse_adjust("qfq"),
    )
df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
df = df.reset_index(drop=True)

# 跑回测
result = subprocess.run(
    ["python3", "-m", "easy_tdx", "backtest", "600550",
     "--strategy-file", "strategies/shadow_yang_v2.py",
     "--count", "300", "--adjust", "qfq", "--json"],
    capture_output=True, text=True, cwd="/Users/a1-6/project/easy_tdx",
)
data = json.loads(result.stdout)
trades = data.get('trades', [])

print("=" * 110)
print("600550 盈利锁定交易详情 + Tier3调整(4%->3%)影响分析")
print("=" * 110)

i = 0
while i < len(trades) - 1:
    buy = trades[i]
    sell = trades[i + 1]
    if buy['direction'] != 'BUY' or sell['direction'] != 'SELL':
        i += 1
        continue

    if sell['reason'] != '盈利锁定':
        i += 2
        continue

    entry = buy['price']
    sell_price = sell['price']
    buy_date = buy['datetime'][:10]
    sell_date = sell['datetime'][:10]

    # 找买卖点在df中的index
    buy_idx = df.index[df['date'] == buy_date].tolist()
    sell_idx = df.index[df['date'] == sell_date].tolist()
    if not buy_idx or not sell_idx:
        i += 2
        continue
    bi, si = buy_idx[0], sell_idx[0]

    # 持仓期间最高收盘
    highest_close = entry
    for k in range(bi, si + 1):
        ck = df['close'].iloc[k]
        if ck > highest_close:
            highest_close = ck
    max_profit = (highest_close - entry) / entry * 100
    profit_at_sell = (sell_price - entry) / entry * 100

    # 当前Tier3=4%的锁定价
    floor_old, tier = calc_floor_new(entry, highest_close, 0.04)
    # 新Tier3=3%的锁定价
    floor_new, _ = calc_floor_new(entry, highest_close, 0.03)

    triggered_old = sell_price <= floor_old
    triggered_new = sell_price <= floor_new

    # 卖出后5日走势
    future_5d = []
    for k in range(1, 6):
        if si + k < len(df):
            fc = df['close'].iloc[si + k]
            chg = (fc - sell_price) / sell_price * 100
            future_5d.append(chg)
    future_avg = sum(future_5d) / len(future_5d) if future_5d else 0

    print(f"\n  买{buy_date}@{entry:.2f} -> 卖{sell_date}@{sell_price:.2f} ({profit_at_sell:+.1f}%)")
    print(f"    最高收盘{highest_close:.2f} (+{max_profit:.1f}%) | Tier: {tier}")
    print(f"    锁定价(4%): {floor_old:.4f} | 触发={'是' if triggered_old else '否'}")
    print(f"    锁定价(3%): {floor_new:.4f} | 触发={'是' if triggered_new else '否'}")
    print(f"    卖出价: {sell_price:.4f}")
    if triggered_old and not triggered_new:
        print(f"    >>> Tier3调整后不触发！持仓继续。后续5日均{future_avg:+.1f}%")
    print(f"    后续5日均{future_avg:+.1f}%")

    i += 2

# 同样分析8股
print(f"\n{'=' * 110}")
print("8股盈利锁定交易 Tier3调整影响")
print("=" * 110)

STOCKS_8 = [
    ("600403", "1"), ("600206", "1"), ("002407", "0"), ("600539", "1"),
    ("002156", "0"), ("000001", "0"), ("600519", "1"), ("000858", "0"),
]

for code, market in STOCKS_8:
    with get_mac_client() as client:
        df8 = client.get_stock_kline(
            parse_market(market), code,
            period=parse_period("daily"), start=0, count=300,
            adjust=parse_adjust("qfq"),
        )
    df8['date'] = df8['datetime'].dt.strftime('%Y-%m-%d')
    df8 = df8.reset_index(drop=True)

    result8 = subprocess.run(
        ["python3", "-m", "easy_tdx", "backtest", code,
         "--strategy-file", "strategies/shadow_yang_v2.py",
         "--count", "300", "--adjust", "qfq", "--json"],
        capture_output=True, text=True, cwd="/Users/a1-6/project/easy_tdx",
    )
    data8 = json.loads(result8.stdout)
    trades8 = data8.get('trades', [])

    j = 0
    while j < len(trades8) - 1:
        buy = trades8[j]
        sell = trades8[j + 1]
        if buy['direction'] != 'BUY' or sell['direction'] != 'SELL':
            j += 1
            continue
        if sell['reason'] != '盈利锁定':
            j += 2
            continue

        entry = buy['price']
        sell_price = sell['price']
        buy_date = buy['datetime'][:10]
        sell_date = sell['datetime'][:10]

        buy_idx = df8.index[df8['date'] == buy_date].tolist()
        sell_idx = df8.index[df8['date'] == sell_date].tolist()
        if not buy_idx or not sell_idx:
            j += 2
            continue
        bi, si = buy_idx[0], sell_idx[0]

        highest_close = entry
        for k in range(bi, si + 1):
            ck = df8['close'].iloc[k]
            if ck > highest_close:
                highest_close = ck
        max_profit = (highest_close - entry) / entry * 100

        floor_old, tier = calc_floor_new(entry, highest_close, 0.04)
        floor_new, _ = calc_floor_new(entry, highest_close, 0.03)
        triggered_old = sell_price <= floor_old
        triggered_new = sell_price <= floor_new

        future_5d = []
        for k in range(1, 6):
            if si + k < len(df8):
                fc = df8['close'].iloc[si + k]
                chg = (fc - sell_price) / sell_price * 100
                future_5d.append(chg)
        future_avg = sum(future_5d) / len(future_5d) if future_5d else 0

        changed = triggered_old and not triggered_new
        marker = " <<< 改变！" if changed else ""
        print(f"  {code} 买{buy_date}@{entry:.2f}->卖{sell_date}@{sell_price:.2f} "
              f"最高+{max_profit:.1f}% {tier} | 4%锁{floor_old:.2f}({'触' if triggered_old else '不'}) "
              f"3%锁{floor_new:.2f}({'触' if triggered_new else '不'}) | 后续{future_avg:+.1f}%{marker}")

        j += 2
