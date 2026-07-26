"""对比 600550 03-03 vs 002407 05-25 盈利锁定卖出日的指标，找区分特征。

目标：找到能区分「趋势延续(600550后续涨)」vs「反弹结束(002407后续跌)」的特征，
用于设计条件式放宽盈利锁定的精细方案。
"""
import sys
from easy_tdx import MyTT
from easy_tdx.cli.conn import get_mac_client
from easy_tdx.cli.parsers import parse_market, parse_adjust, parse_period

ATR_PERIOD = 14
PROFIT_LOCKIN_TRIGGERS = [0.03, 0.06, 0.10, 0.15, 0.20]
PROFIT_LOCKIN_FLOORS = [0.0, 0.02, 0.04, 0.07, 0.10]
TIER_NAMES = ["Tier1(3%->保本)", "Tier2(6%->2%)", "Tier3(10%->4%)",
              "Tier4(15%->7%)", "Tier5(20%->10%)"]


def calc_atr(df, i):
    if i < ATR_PERIOD:
        return df['close'].iloc[i] * 0.03
    tr_sum = 0.0
    for j in range(ATR_PERIOD):
        offset = i - j
        high = df['high'].iloc[offset]
        low = df['low'].iloc[offset]
        prev_c = df['close'].iloc[offset - 1]
        tr_sum += max(high - low, abs(high - prev_c), abs(low - prev_c))
    return tr_sum / ATR_PERIOD


def is_ma_bullish(ma5, ma10, ma20, ma60):
    if not (ma5 > ma20 > ma60 and ma10 > ma20 > ma60):
        return False
    if ma5 < ma10 and ma5 < ma10 * (1 - 0.01):
        return False
    return True


def calc_profit_floor(entry_price, highest_close):
    """计算盈利锁定价（与策略 _calc_profit_floor 一致）。"""
    if entry_price <= 0 or highest_close <= entry_price:
        return 0.0, "无"
    max_profit = (highest_close - entry_price) / entry_price
    for k in range(len(PROFIT_LOCKIN_TRIGGERS) - 1, -1, -1):
        if max_profit >= PROFIT_LOCKIN_TRIGGERS[k]:
            floor_pct = PROFIT_LOCKIN_FLOORS[k]
            return entry_price * (1 + floor_pct), TIER_NAMES[k]
    return 0.0, "无"


def load_stock(code, market):
    with get_mac_client() as client:
        df = client.get_stock_kline(
            parse_market(market), code,
            period=parse_period("daily"), start=0, count=300,
            adjust=parse_adjust("qfq"),
        )
    df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
    df['ma5'] = MyTT.MA(df['close'].values, 5)
    df['ma10'] = MyTT.MA(df['close'].values, 10)
    df['ma20'] = MyTT.MA(df['close'].values, 20)
    df['ma60'] = MyTT.MA(df['close'].values, 60)
    pdi, mdi, adx, adxr = MyTT.DMI(df['close'].values, df['high'].values, df['low'].values)
    df['pdi'] = pdi
    df['mdi'] = mdi
    df['adx'] = adx
    return df.reset_index(drop=True)


def diagnose_sell_day(df, entry_date, sell_date, label):
    """诊断盈利锁定卖出日的完整指标。"""
    def idx(d):
        m = df.index[df['date'] == d]
        return m[0] if len(m) else -1

    i_entry = idx(entry_date)
    i_sell = idx(sell_date)
    if i_entry < 0 or i_sell < 0:
        print(f"  {label}: 日期未找到 entry={entry_date} sell={sell_date}")
        return

    entry_price = df['close'].iloc[i_entry]
    # 计算持仓期间最高收盘价
    highest_close = entry_price
    for k in range(i_entry, i_sell + 1):
        c = df['close'].iloc[k]
        if c > highest_close:
            highest_close = c

    i = i_sell
    cur_close = df['close'].iloc[i]
    cur_open = df['open'].iloc[i]
    cur_high = df['high'].iloc[i]
    cur_low = df['low'].iloc[i]
    prev_close = df['close'].iloc[i - 1]

    ma5 = df['ma5'].iloc[i]
    ma5_prev = df['ma5'].iloc[i - 1]
    ma10 = df['ma10'].iloc[i]
    ma20 = df['ma20'].iloc[i]
    ma60 = df['ma60'].iloc[i]
    adx = df['adx'].iloc[i]
    pdi = df['pdi'].iloc[i]
    mdi = df['mdi'].iloc[i]
    atr = calc_atr(df, i)

    profit_pct = (cur_close - entry_price) / entry_price * 100
    max_profit_pct = (highest_close - entry_price) / entry_price * 100
    floor_price, tier = calc_profit_floor(entry_price, highest_close)
    drawdown_from_high = (highest_close - cur_close) / highest_close * 100
    day_change = (cur_close - prev_close) / prev_close * 100
    is_bullish = is_ma_bullish(ma5, ma10, ma20, ma60)
    is_strong_trend = (adx == adx and pdi == pdi and mdi == mdi
                       and adx >= 30 and pdi > mdi)
    ma5_direction = "上行" if ma5 > ma5_prev else "下行"

    # 前5日走势（判断急跌vs缓跌）
    print(f"\n{'=' * 80}")
    print(f"【{label}】{sell_date} 盈利锁定卖出日诊断")
    print(f"{'=' * 80}")
    print(f"  买入日: {entry_date} @ {entry_price:.2f}")
    print(f"  最高收盘: {highest_close:.2f} (浮盈 +{max_profit_pct:.1f}%)")
    print(f"  锁定阶梯: {tier}")
    print(f"  锁定价: {floor_price:.2f}")
    print(f"  卖出日收盘: {cur_close:.2f} (当前浮盈 {profit_pct:+.1f}%)")
    print(f"  从最高收盘回撤: -{drawdown_from_high:.1f}%")
    print()
    print(f"  ── 当日K线 ──")
    print(f"  OHLC: {cur_open:.2f} / {cur_high:.2f} / {cur_low:.2f} / {cur_close:.2f}")
    print(f"  当日涨跌: {day_change:+.1f}%")
    print(f"  ATR: {atr:.2f} (ATR%: {atr / cur_close * 100:.1f}%)")
    print()
    print(f"  ── 趋势指标 ──")
    print(f"  ADX: {adx:.1f}  +DI: {pdi:.1f}  -DI: {mdi:.1f}  (+DI/-DI: {pdi/mdi:.2f})")
    print(f"  强趋势(ADX>=30且+DI>-DI): {'是' if is_strong_trend else '否'}")
    print(f"  多头排列: {'是' if is_bullish else '否'}")
    print(f"  MA5: {ma5:.2f} ({ma5_direction})  MA10: {ma10:.2f}  MA20: {ma20:.2f}  MA60: {ma60:.2f}")
    print()
    print(f"  ── 前5日走势（急跌vs缓跌判断）──")
    for k in range(5, 0, -1):
        d = df['date'].iloc[i - k]
        o = df['open'].iloc[i - k]
        c = df['close'].iloc[i - k]
        pc = df['close'].iloc[i - k - 1]
        chg = (c - pc) / pc * 100
        yin_yang = "阴" if c < o else "阳"
        print(f"    {d}: O{o:.2f} C{c:.2f} {yin_yang} {chg:+.1f}%")

    # 卖出后5日走势（验证趋势延续vs反弹结束）
    print()
    print(f"  ── 卖出后5日走势（验证后续方向）──")
    if i + 5 < len(df):
        for k in range(1, 6):
            d = df['date'].iloc[i + k]
            c = df['close'].iloc[i + k]
            pc = df['close'].iloc[i + k - 1]
            chg = (c - pc) / pc * 100
            vs_sell = (c - cur_close) / cur_close * 100
            print(f"    {d}: C{c:.2f} ({chg:+.1f}%, vs卖出日 {vs_sell:+.1f}%)")
    else:
        print("    (数据不足)")

    # 关键区分特征汇总
    print()
    print(f"  ── 关键区分特征 ──")
    print(f"  ADX: {adx:.1f}  |  +DI/-DI: {pdi/mdi:.2f}  |  MA5方向: {ma5_direction}  "
          f"|  当日跌幅: {day_change:+.1f}%  |  回撤: -{drawdown_from_high:.1f}%")


def main():
    # 600550: 02-06 买@15.61, 03-03 卖@16.15 (Tier3盈利锁定, 后续涨到19.58)
    df1 = load_stock("600550", "1")
    diagnose_sell_day(df1, "2026-02-06", "2026-03-03", "600550 趋势延续案例")

    # 002407: 05-15 买@36.27, 05-25 卖@38.43 (盈利锁定, 后续跌到35.79止损)
    df2 = load_stock("002407", "0")
    diagnose_sell_day(df2, "2026-05-15", "2026-05-25", "002407 反弹结束案例")

    print(f"\n{'=' * 80}")
    print("【对比汇总】")
    print(f"{'=' * 80}")
    print("特征          | 600550(趋势延续) | 002407(反弹结束)")
    print("后续5日       | 涨到19.58(+21%)  | 跌到35.79(-7%)")


if __name__ == "__main__":
    main()
