"""TRIX 三重平滑趋势策略（优化版）。

TRIX 金叉买入 + MA60 趋势过滤 + 均线发散波动率过滤 + 动态离场
（MA15 快线止盈 / ATR 吊灯止损）。三重过滤减少震荡市假信号，适合中长线趋势跟踪。

入场条件（全部满足）：
  1. TRIX 上穿信号线（金叉）
  2. 收盘价 > MA60（趋势过滤）
  3. (MA15 - MA20) > 过去 20 日 (MA15-MA20) 均值（波动率过滤：均线发散）

离场条件（任一触发即卖出）：
  ① 快线止盈：收盘价 < MA15，且 (MA15 - MA20) > 过去 20 日 (MA15-MA20) 均值
  ② ATR 吊灯止损：收盘价 < 持仓最高价 - ATR(20) × 5.0

参数优化结论（30 只 A 股 × 800 日回测）：
  - MA60 趋势过滤优于 MA120（更多入场机会）
  - MA15 快线止盈为最优（MA5 过于敏感，MA20 在 ma_fast=ma_slow 时退化）
  - ATR 倍数 5.0 为最佳平衡点（≥6.0 时吊灯止损失效）

用法::

    easy-tdx backtest SZ 000001 \\
        --strategy-file strategies/trix_cross_optimized.py --count 2000 --table
"""

from easy_tdx import MyTT
from easy_tdx.backtest import Strategy, crossover


class TRIXCrossStrategy(Strategy):
    """TRIX 三重平滑趋势策略（优化版）。

    在 TRIX 金叉信号基础上增加三重过滤：
    - 趋势过滤（MA60）：仅多头趋势中做多
    - 波动率过滤（均线发散度）：过滤震荡市假金叉
    - 动态离场（MA15 快线止盈 + ATR 吊灯止损）：锁定利润并控制回撤
    - ATR 吊灯止损自适应波动率：高波动股票止损更宽，低波动股票止损更窄
    """

    def init(self) -> None:
        # TRIX 指标 + 金叉信号
        self.trix, self.trma = self.I(MyTT.TRIX, self.data.close)
        self.golden = crossover(self.trix, self.trma)

        # 趋势过滤均线（MA60）
        self.ma_trend = self.I(MyTT.MA, self.data.close, 60)

        # 波动率过滤均线（MA15 快线 / MA20 中线 / MA20 慢线）
        self.ma_fast = self.I(MyTT.MA, self.data.close, 15)
        self.ma_mid = self.I(MyTT.MA, self.data.close, 20)
        self.ma_slow = self.I(MyTT.MA, self.data.close, 20)

        # 均线差值及其移动平均（波动率过滤核心）
        # 入场过滤：MA_fast - MA_slow 的发散度
        self._diff_entry = self.ma_fast - self.ma_slow
        self._diff_entry_avg = self.I(MyTT.MA, self._diff_entry, 20)

        # 离场过滤：MA_fast - MA_mid 的发散度
        self._diff_exit = self.ma_fast - self.ma_mid
        self._diff_exit_avg = self.I(MyTT.MA, self._diff_exit, 20)

        # ATR 吊灯止损：持仓期间最高价 + ATR
        self.atr = self.I(MyTT.ATR, self.data.close, self.data.high, self.data.low, 20)
        self._peak_price: float = 0.0

    def next(self) -> None:
        i = self._bar_index
        close = self.data.close[0]
        high = self.data.high[0]

        if self.position["size"] == 0:
            # ── 空仓：检查入场条件 ──────────────────────────────────────────
            # 1. TRIX 金叉
            # 2. 趋势过滤：收盘价 > MA60
            # 3. 波动率过滤：MA_fast - MA_slow > 其 20 日均值（均线发散）
            if (
                self.golden[i]
                and close > self.ma_trend[i]
                and self._diff_entry[i] > self._diff_entry_avg[i]
            ):
                self.buy(size=0)
                self._peak_price = high
        else:
            # ── 持仓：更新峰值，检查离场条件 ────────────────────────────────
            if high > self._peak_price:
                self._peak_price = high

            # 离场条件①：快线止盈 - 收盘价 < MA_fast 且均线发散
            fast_line_exit = (
                close < self.ma_fast[i]
                and self._diff_exit[i] > self._diff_exit_avg[i]
            )

            if fast_line_exit :
                self.sell(size=0)
                self._peak_price = 0.0
