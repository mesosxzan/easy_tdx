"""订单撮合模拟器。

将策略信号转换为成交记录，支持多种执行模式、仓位管理和拒绝策略。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from easy_tdx.backtest.types import Signal, Trade


@dataclass
class OrderSimulator:
    """订单撮合模拟器。

    将策略信号（Signal）转换为成交记录（Trade），支持多种执行模式、
    仓位管理和拒绝策略。

    Attributes:
        df: K线数据 DataFrame
        execution: 成交价规则 (next_open/next_close/this_close/worst/best)
        position_mode: 仓位模式 (full/fixed/percent)
        reject_policy: 拒绝策略 (reduce/skip)
        commission: 佣金费率
        min_commission: 最低佣金
        stamp_tax: 印花税率（仅卖出）
        slippage: 滑点（每股）
        future_leak_warning: 是否使用了未来数据（this_close 模式）
    """

    df: pd.DataFrame
    execution: str = "next_open"
    position_mode: str = "full"
    reject_policy: str = "reduce"
    commission: float = 0.0003
    min_commission: float = 5.0
    stamp_tax: float = 0.001
    slippage: float = 0.0
    future_leak_warning: bool = False

    def simulate(
        self,
        signals: list[Signal],
        cash: float,
        position: float,
        position_mode: str | None = None,
    ) -> list[Trade]:
        """模拟订单撮合过程。

        Args:
            signals: 交易信号列表
            cash: 初始现金
            position: 初始持仓（股数）
            position_mode: 仓位模式（覆盖初始化参数）

        Returns:
            成交记录列表
        """
        if position_mode is None:
            position_mode = self.position_mode

        trades: list[Trade] = []
        current_cash = cash
        current_position = position

        for signal in signals:
            # 找到信号对应的 K 线
            bar_idx = self._find_bar_index(signal.datetime)
            if bar_idx is None:
                continue

            # 确定成交的 K 线索引
            exec_idx = self._resolve_exec_index(bar_idx)
            if exec_idx is None or exec_idx >= len(self.df):
                continue

            # 获取成交价
            price = self._get_price(exec_idx, signal.direction)
            if price is None:
                continue

            # 执行交易
            if signal.direction == "BUY":
                trade = self._execute_buy(
                    signal=signal,
                    bar_idx=bar_idx,
                    exec_idx=exec_idx,
                    price=price,
                    cash=current_cash,
                    position=current_position,
                    position_mode=position_mode,
                )
                if trade is not None:
                    trades.append(trade)
                    if not trade.rejected:
                        current_cash -= trade.size * trade.price + trade.commission + trade.slippage
                        current_position += trade.size

            elif signal.direction == "SELL":
                trade = self._execute_sell(
                    signal=signal,
                    bar_idx=bar_idx,
                    exec_idx=exec_idx,
                    price=price,
                    cash=current_cash,
                    position=current_position,
                    position_mode=position_mode,
                )
                if trade is not None:
                    trades.append(trade)
                    if not trade.rejected:
                        current_cash += trade.size * trade.price - trade.commission - trade.slippage
                        current_position -= trade.size

        return trades

    def _find_bar_index(self, datetime_val: int) -> int | None:
        """查找 datetime 对应的 K 线索引。

        Args:
            datetime_val: 信号时间（int 格式 YYYYMMDD）

        Returns:
            K 线索引，未找到返回 None
        """
        # 检查 df 中的 datetime 列类型
        dt_col = self.df["datetime"]

        # 尝试直接比较（如果是 int 类型）
        try:
            idx = (dt_col == datetime_val).idxmax() if (dt_col == datetime_val).any() else None
            if idx is not None:
                return int(idx)
        except (TypeError, ValueError):
            pass

        # 如果是 datetime 对象，转为 int 比较
        if pd.api.types.is_datetime64_any_dtype(dt_col):
            dt_ints = dt_col.dt.strftime("%Y%m%d").astype(int)
            mask = dt_ints == datetime_val
            if mask.any():
                return int(mask.idxmax())
            return None

        return None

    def _resolve_exec_index(self, bar_idx: int) -> int | None:
        """根据执行模式确定成交的 K 线索引。

        Args:
            bar_idx: 信号对应的 K 线索引

        Returns:
            成交 K 线索引
        """
        if self.execution == "this_close":
            # 当信号 K 线收盘时成交
            self.future_leak_warning = True
            return bar_idx
        else:
            # 其他模式在下一根 K 线成交
            return bar_idx + 1

    def _get_price(self, exec_idx: int, direction: str) -> float | None:
        """根据执行模式和方向获取成交价。

        Args:
            exec_idx: 成交 K 线索引
            direction: 交易方向

        Returns:
            成交价格
        """
        if exec_idx >= len(self.df):
            return None

        row = self.df.iloc[exec_idx]

        if self.execution == "next_open":
            return float(row["open"])
        elif self.execution == "next_close":
            return float(row["close"])
        elif self.execution == "this_close":
            return float(row["close"])
        elif self.execution == "worst":
            # 买入取最高价，卖出取最低价
            return float(row["high"]) if direction == "BUY" else float(row["low"])
        elif self.execution == "best":
            # 买入取最低价，卖出取最高价
            return float(row["low"]) if direction == "BUY" else float(row["high"])
        else:
            return None

    def _calculate_buy_size(
        self,
        signal_size: float,
        price: float,
        cash: float,
        position_mode: str,
    ) -> float:
        """计算买入数量。

        Args:
            signal_size: 信号指定的数量
            price: 成交价格
            cash: 可用现金
            position_mode: 仓位模式

        Returns:
            买入数量（股）
        """
        if position_mode == "full" or signal_size == 0:
            # 全仓：计算可用现金能买多少（100股整手）
            # 先计算最大股数，然后向下取整到100的倍数
            max_cost_per_share = price * (1 + self.commission) + self.slippage
            max_shares_raw = cash / max_cost_per_share
            max_shares = int(max_shares_raw / 100) * 100
            return float(max_shares)
        elif position_mode == "fixed":
            # 固定股数
            return signal_size
        elif position_mode == "percent":
            # 总资产的百分比
            total_value = cash  # 简化：假设现金=总资产
            target_value = total_value * signal_size
            max_shares = int(target_value / price / 100) * 100
            return float(max_shares)
        else:
            return signal_size

    def _calculate_sell_size(
        self,
        signal_size: float,
        position: float,
        position_mode: str,
    ) -> float:
        """计算卖出数量。

        Args:
            signal_size: 信号指定的数量
            position: 当前持仓
            position_mode: 仓位模式

        Returns:
            卖出数量（股）
        """
        if position_mode == "full" or signal_size == 0:
            # 全部卖出
            return position
        elif position_mode == "fixed":
            # 固定股数
            return signal_size
        elif position_mode == "percent":
            # 持仓的百分比
            return position * signal_size
        else:
            return signal_size

    def _calculate_commission(self, size: float, price: float, is_sell: bool = False) -> float:
        """计算手续费。

        Args:
            size: 成交数量
            price: 成交价格
            is_sell: 是否为卖出

        Returns:
            手续费总额
        """
        # 佣金
        commission = max(size * price * self.commission, self.min_commission)

        # 印花税（仅卖出）
        if is_sell:
            stamp = size * price * self.stamp_tax
            commission += stamp

        return commission

    def _execute_buy(
        self,
        signal: Signal,
        bar_idx: int,
        exec_idx: int,
        price: float,
        cash: float,
        position: float,
        position_mode: str,
    ) -> Trade | None:
        """执行买入。

        Args:
            signal: 交易信号
            bar_idx: 信号 K 线索引
            exec_idx: 成交 K 线索引
            price: 成交价格
            cash: 可用现金
            position: 当前持仓
            position_mode: 仓位模式

        Returns:
            成交记录
        """
        # 保存原始信号数量
        original_size = signal.size

        # 计算买入数量
        size = self._calculate_buy_size(signal.size, price, cash, position_mode)

        if size <= 0:
            # 资金不足或计算结果为0
            if self.reject_policy == "skip":
                # 对于 percent 模式，original_size 是百分比（如 0.5），不是股数
                # 对于 fixed/full 模式，original_size 就是股数
                display_size = original_size if position_mode == "fixed" else 100
                return Trade(
                    datetime=self.df.iloc[exec_idx]["datetime"],
                    direction="BUY",
                    size=display_size,
                    price=price,
                    commission=0.0,
                    slippage=0.0,
                    pnl=0.0,
                    rejected=True,
                )
            return None

        # 计算费用
        commission = self._calculate_commission(size, price, is_sell=False)
        slippage = size * self.slippage

        # 检查资金是否足够
        total_cost = size * price + commission + slippage
        if total_cost > cash:
            if self.reject_policy == "skip":
                return Trade(
                    datetime=self.df.iloc[exec_idx]["datetime"],
                    direction="BUY",
                    size=original_size,
                    price=price,
                    commission=commission,
                    slippage=slippage,
                    pnl=0.0,
                    rejected=True,
                )
            elif self.reject_policy == "reduce":
                # reduce 模式：重新计算可买数量
                available_cash = cash - self.min_commission - slippage
                if available_cash > price:
                    reduced_size = int(available_cash / price / 100) * 100
                    if reduced_size > 0:
                        commission = self._calculate_commission(reduced_size, price, is_sell=False)
                        slippage = reduced_size * self.slippage
                        return Trade(
                            datetime=self.df.iloc[exec_idx]["datetime"],
                            direction="BUY",
                            size=reduced_size,
                            price=price,
                            commission=commission,
                            slippage=slippage,
                            pnl=0.0,
                            rejected=False,
                        )
                # 无法买任何数量
                return None

        return Trade(
            datetime=self.df.iloc[exec_idx]["datetime"],
            direction="BUY",
            size=size,
            price=price,
            commission=commission,
            slippage=slippage,
            pnl=0.0,
            rejected=False,
        )

    def _execute_sell(
        self,
        signal: Signal,
        bar_idx: int,
        exec_idx: int,
        price: float,
        cash: float,
        position: float,
        position_mode: str,
    ) -> Trade | None:
        """执行卖出。

        Args:
            signal: 交易信号
            bar_idx: 信号 K 线索引
            exec_idx: 成交 K 线索引
            price: 成交价格
            cash: 可用现金
            position: 当前持仓
            position_mode: 仓位模式

        Returns:
            成交记录
        """
        # 计算卖出数量
        size = self._calculate_sell_size(signal.size, position, position_mode)

        if size <= 0:
            # 无持仓
            if self.reject_policy == "skip":
                return Trade(
                    datetime=self.df.iloc[exec_idx]["datetime"],
                    direction="SELL",
                    size=0,
                    price=price,
                    commission=0.0,
                    slippage=0.0,
                    pnl=0.0,
                    rejected=True,
                )
            return None

        # 检查持仓是否足够
        if size > position:
            if self.reject_policy == "skip":
                return Trade(
                    datetime=self.df.iloc[exec_idx]["datetime"],
                    direction="SELL",
                    size=size,
                    price=price,
                    commission=0.0,
                    slippage=0.0,
                    pnl=0.0,
                    rejected=True,
                )
            # reduce 模式：减少到实际持仓
            size = position

        # 计算费用
        commission = self._calculate_commission(size, price, is_sell=True)
        slippage = size * self.slippage

        return Trade(
            datetime=self.df.iloc[exec_idx]["datetime"],
            direction="SELL",
            size=size,
            price=price,
            commission=commission,
            slippage=slippage,
            pnl=0.0,
            rejected=False,
        )
