"""扫描8股所有「盈利锁定」卖出交易，分析K线形态与后续走势的关系。

目标：验证「光头光脚大阴线=出货(后续跌)，有下影线=承接(后续涨)」假设是否成立。
"""
import json
import subprocess
import sys
from easy_tdx import MyTT
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_adjust, parse_period

STOCKS = [
    ("600403", "1"), ("600206", "1"), ("002407", "0"), ("600539", "1"),
    ("002156", "0"), ("000001", "0"), ("600519", "1"), ("000858", "0"),
]


def load_stock(code, market):
    with get_mac_client() as client:
        df = client.get_stock_kline(
            parse_market(market), code,
            period=parse_period("daily"), start=0, count=300,
            adjust=parse_adjust("qfq"),
        )
    df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
    df = df.reset_index(drop=True)
    return df


def get_trades(code):
    """跑回测获取交易记录。"""
    result = subprocess.run(
        ["python3", "-m", "easy_tdx", "backtest", code,
         "--strategy-file", "strategies/shadow_yang_v2.py",
         "--count", "300", "--adjust", "qfq", "--json"],
        capture_output=True, text=True, cwd="/Users/a1-6/project/easy_tdx",
    )
    data = json.loads(result.stdout)
    return data.get('trades', [])


def analyze_lockin_trades():
    """分析所有盈利锁定交易。"""
    print("=" * 100)
    print("8股「盈利锁定」交易 K线形态与后续走势分析")
    print("=" * 100)

    all_lockin = []

    for code, market in STOCKS:
        df = load_stock(code, market)
        trades = get_trades(code)

        # 配对买入和卖出
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

            # 找到卖出日在df中的index
            sell_date = sell['datetime'][:10]
            sell_idx = df.index[df['date'] == sell_date].tolist()
            if not sell_idx:
                i += 2
                continue
            si = sell_idx[0]

            entry_price = buy['price']
            sell_price = sell['price']

            # 卖出日K线
            o = df['open'].iloc[si]
            h = df['high'].iloc[si]
            l = df['low'].iloc[si]
            c = df['close'].iloc[si]
            prev_c = df['close'].iloc[si - 1] if si > 0 else c

            # K线形态分析
            is_yin = c < o
            body = abs(c - o)
            upper_shadow = h - max(o, c)
            lower_shadow = min(o, c) - l
            day_change = (c - prev_c) / prev_c * 100
            lower_ratio = lower_shadow / body * 100 if body > 0 else 0
            upper_ratio = upper_shadow / body * 100 if body > 0 else 0

            # 光头光脚判断：下影线<实体的5%
            is_no_lower_shadow = lower_shadow < body * 0.05 if body > 0 else False
            is_bald_bearish = is_yin and is_no_lower_shadow and day_change < -3

            # 持仓期间最高收盘
            buy_date = buy['datetime'][:10]
            buy_idx = df.index[df['date'] == buy_date].tolist()
            if not buy_idx:
                i += 2
                continue
            bi = buy_idx[0]
            highest_close = entry_price
            for k in range(bi, si + 1):
                ck = df['close'].iloc[k]
                if ck > highest_close:
                    highest_close = ck
            max_profit = (highest_close - entry_price) / entry_price * 100
            profit_at_sell = (sell_price - entry_price) / entry_price * 100

            # 卖出后5日走势
            future_5d = []
            for k in range(1, 6):
                if si + k < len(df):
                    fc = df['close'].iloc[si + k]
                    chg = (fc - sell_price) / sell_price * 100
                    future_5d.append(chg)
            future_5d_avg = sum(future_5d) / len(future_5d) if future_5d else 0
            future_5d_max = max(future_5d) if future_5d else 0
            future_5d_min = min(future_5d) if future_5d else 0

            # 卖出后5日是否上涨
            is_trend_continue = future_5d_avg > 2.0
            is_reversal = future_5d_avg < -2.0

            all_lockin.append({
                'code': code,
                'buy_date': buy_date,
                'sell_date': sell_date,
                'entry': entry_price,
                'sell': sell_price,
                'profit': profit_at_sell,
                'max_profit': max_profit,
                'is_yin': is_yin,
                'is_bald_bearish': is_bald_bearish,
                'lower_ratio': lower_ratio,
                'day_change': day_change,
                'future_5d_avg': future_5d_avg,
                'future_5d_max': future_5d_max,
                'future_5d_min': future_5d_min,
                'is_trend_continue': is_trend_continue,
                'is_reversal': is_reversal,
            })

            print(f"\n  {code} | 买{buy_date}@{entry_price:.2f} -> 卖{sell_date}@{sell_price:.2f}")
            print(f"    最高浮盈 +{max_profit:.1f}% | 卖出浮盈 {profit_at_sell:+.1f}%")
            print(f"    K线: {'阴' if is_yin else '阳'} | 当日{day_change:+.1f}% | "
                  f"下影/实体={lower_ratio:.0f}% | 光头光脚大阴线={'是' if is_bald_bearish else '否'}")
            print(f"    后续5日均{future_5d_avg:+.1f}% (max{future_5d_max:+.1f}% min{future_5d_min:+.1f}%) | "
                  f"{'趋势延续' if is_trend_continue else '反弹结束' if is_reversal else '中性'}")

            i += 2

    # 汇总统计
    print(f"\n{'=' * 100}")
    print("【汇总统计】")
    print(f"{'=' * 100}")
    total = len(all_lockin)
    trend_cont = [t for t in all_lockin if t['is_trend_continue']]
    reversal = [t for t in all_lockin if t['is_reversal']]
    neutral = [t for t in all_lockin if not t['is_trend_continue'] and not t['is_reversal']]

    print(f"\n总盈利锁定交易数: {total}")
    print(f"  趋势延续(后续5日均>2%): {len(trend_cont)}")
    print(f"  反弹结束(后续5日均<-2%): {len(reversal)}")
    print(f"  中性: {len(neutral)}")

    # K线形态 vs 后续走势
    print(f"\n── 光头光脚大阴线 vs 有下影线 ──")
    bald = [t for t in all_lockin if t['is_bald_bearish']]
    has_shadow = [t for t in all_lockin if not t['is_bald_bearish'] and t['is_yin']]

    print(f"\n光头光脚大阴线: {len(bald)}笔")
    for t in bald:
        print(f"  {t['code']} {t['sell_date']}: 后续5日均{t['future_5d_avg']:+.1f}% "
              f"({'趋势延续' if t['is_trend_continue'] else '反弹结束' if t['is_reversal'  ] else '中性'})")
    if bald:
        bald_avg = sum(t['future_5d_avg'] for t in bald) / len(bald)
        print(f"  平均后续5日: {bald_avg:+.1f}%")

    print(f"\n有下影线阴线: {len(has_shadow)}笔")
    for t in has_shadow:
        print(f"  {t['code']} {t['sell_date']}: 下影/实体={t['lower_ratio']:.0f}% "
              f"后续5日均{t['future_5d_avg']:+.1f}% "
              f"({'趋势延续' if t['is_trend_continue'] else '反弹结束' if t['is_reversal'  ] else '中性'})")
    if has_shadow:
        shadow_avg = sum(t['future_5d_avg'] for t in has_shadow) / len(has_shadow)
        print(f"  平均后续5日: {shadow_avg:+.1f}%")

    # 当日跌幅 vs 后续走势
    print(f"\n── 当日跌幅分组 ──")
    big_drop = [t for t in all_lockin if t['day_change'] < -7]
    small_drop = [t for t in all_lockin if -7 <= t['day_change'] < -3]
    tiny_drop = [t for t in all_lockin if t['day_change'] >= -3]

    for label, group in [("跌幅>7%", big_drop), ("跌幅3-7%", small_drop), ("跌幅<3%", tiny_drop)]:
        if group:
            avg_future = sum(t['future_5d_avg'] for t in group) / len(group)
            cont = sum(1 for t in group if t['is_trend_continue'])
            rev = sum(1 for t in group if t['is_reversal'])
            print(f"  {label}: {len(group)}笔, 后续5日均{avg_future:+.1f}%, "
                  f"趋势延续{cont}笔, 反弹结束{rev}笔")


if __name__ == "__main__":
    analyze_lockin_trades()
