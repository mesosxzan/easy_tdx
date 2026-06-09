"""海龟交易法（唐安奇通道）策略。

价格突破 N 日最高价买入，跌破 N 日最低价卖出。
经典的趋势跟踪策略。

用法::

    easy-tdx backtest SZ 000001 --strategy-file strategies/turtle_breakout.py --table
"""

from easy_tdx.backtest import Strategy
from easy_tdx import MyTT


class TurtleStrategy(Strategy):
    """海龟交易法（唐安奇通道突破）策略。"""

    def init(self) -> None:
        self.upper, _, self.lower = self.I(MyTT.TAQ, self.data.high, self.data.low, 20)

    def next(self) -> None:
        cur = self.data.close[0]
        upper = self.upper[self._bar_index]
        lower = self.lower[self._bar_index]

        if cur >= upper and self.position["size"] == 0:
            self.buy(size=0)
        elif cur <= lower and self.position["size"] > 0:
            self.sell(size=0)
