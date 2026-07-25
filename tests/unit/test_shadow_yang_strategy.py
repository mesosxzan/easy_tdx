"""影线后收阳线策略（ShadowYangStrategy）单元测试。

测试覆盖：
  - 买入信号检测（阳线 + 放量 + MA5 上升）
  - 卖出逻辑（阴线全卖 / 低开全卖 / 其他卖一半）
  - 完整回测周期（买入 → 卖一半 → 持有 → 全卖）
  - position_mode 对部分卖出的影响
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from easy_tdx.backtest.engine import BacktestEngine
from easy_tdx.backtest.types import BacktestResult

# ── 策略加载 ─────────────────────────────────────────────────────────────────


def _load_shadow_yang() -> type:
    """从 strategies/shadow_yang.py 加载 ShadowYangStrategy 类。"""
    path = Path(__file__).parent.parent.parent / "strategies" / "shadow_yang.py"
    spec = importlib.util.spec_from_file_location("shadow_yang", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ShadowYangStrategy  # type: ignore[no-any-return]


ShadowYangStrategy = _load_shadow_yang()

# ── 常量 ─────────────────────────────────────────────────────────────────────

BUY_BAR = 5          # 买入信号出现的 bar 索引
BUY_PRICE = 11.0     # 买入价（bar 5 收盘价）
BUY_SHARES = 9000    # 买入股数（100000 / 11.0033 向下取整到 100 股）
HALF_SHARES = 4500   # 卖一半股数
REMAIN_SHARES = 4500  # 卖一半后剩余股数


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _make_df(
    opens: list[float],
    closes: list[float],
    vols: list[float],
) -> pd.DataFrame:
    """构造 OHLCV DataFrame。

    high/low 自动从 open/close 派生，确保 OHLC 关系正确。
    """
    n = len(closes)
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": opens,
            "close": closes,
            "high": highs,
            "low": lows,
            "vol": vols,
            "amount": [v * c for v, c in zip(vols, closes, strict=True)],
        }
    )


def _base_data(
    bar6_open: float = 11.0,
    bar6_close: float = 12.0,
    bar6_vol: float = 1500.0,
    n_extra: int = 4,
) -> pd.DataFrame:
    """构造基础测试数据。

    Bars 0-4: 平盘（close=10, vol=1000），MA5 平坦
    Bar 5: 阳线（close=11, open=10.5），放量（vol=2000），MA5 上升 → 触发买入
    Bar 6+: 由参数控制，用于测试不同卖出场景
    """
    n = 6 + n_extra
    opens = [10.0] * n
    closes = [10.0] * n
    vols = [1000.0] * n

    # Bar 5: 买入信号
    opens[5] = 10.5
    closes[5] = 11.0
    vols[5] = 2000.0

    # Bar 6: 由参数控制（仅当 n_extra >= 1 时存在）
    if n_extra >= 1:
        opens[6] = bar6_open
        closes[6] = bar6_close
        vols[6] = bar6_vol

    return _make_df(opens, closes, vols)


def _run_backtest(df: pd.DataFrame, position_mode: str = "fixed") -> BacktestResult:
    """运行回测并返回结果。"""
    engine = BacktestEngine(
        strategy=ShadowYangStrategy,
        cash=100000.0,
        commission=0.0003,
        position_mode=position_mode,
    )
    return engine.run(df)


def _get_trades(result: BacktestResult, direction: str | None = None) -> pd.DataFrame:
    """获取交易记录，可按方向过滤。"""
    trades = result.trades
    # 空交易记录时 DataFrame 可能没有列
    if trades.empty or "direction" not in trades.columns:
        return trades
    # 排除被拒绝的交易
    if "rejected" in trades.columns:
        trades = trades[~trades["rejected"]]
    if direction is not None:
        trades = trades[trades["direction"] == direction]
    return trades


# ── 买入信号测试 ─────────────────────────────────────────────────────────────


class TestBuySignal:
    """买入条件检测。"""

    def test_buy_triggers_on_all_conditions(self) -> None:
        """阳线 + 放量 + MA5 上升 → 买入。"""
        df = _base_data(n_extra=1)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(BUY_PRICE)
        assert buys.iloc[0]["size"] == pytest.approx(BUY_SHARES)

    def test_no_buy_when_yin_line(self) -> None:
        """阴线（close < open）→ 不买入。"""
        df = _base_data(n_extra=0)
        # 修改 bar 5 为阴线
        df.loc[5, "open"] = 11.5
        df.loc[5, "close"] = 11.0
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_no_buy_when_low_volume(self) -> None:
        """缩量（vol < MA5(vol)）→ 不买入。"""
        df = _base_data(n_extra=0)
        # 修改 bar 5 成交量为低量
        df.loc[5, "vol"] = 500.0
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_no_buy_when_ma5_declining(self) -> None:
        """MA5 斜率下降 → 不买入。"""
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 9.0]
        vols = [1000.0] * n
        # bar 5: 阳线 + 放量，但 MA5 下降（close[5]=9 < close[0]=12）
        opens[5] = 8.5
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_no_buy_during_warmup(self) -> None:
        """MA5 预热期（bar 0-3）不产生买入信号。"""
        # 只给 4 根 bar，MA5 全为 NaN
        opens = [10.0, 10.0, 10.5, 11.0]
        closes = [10.0, 10.0, 11.0, 11.5]
        vols = [1000.0, 1000.0, 2000.0, 2000.0]
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0


# ── 卖出逻辑测试 ─────────────────────────────────────────────────────────────


class TestSellLogic:
    """卖出条件检测（买入后第一日）。"""

    def test_sell_all_on_yin_line(self) -> None:
        """买入后第一日收阴线 → 以收盘价全部卖出。"""
        # bar 6: 阴线（close < open），非低开
        df = _base_data(bar6_open=11.5, bar6_close=11.0, n_extra=1)
        result = _run_backtest(df)
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        # 以收盘价卖出
        assert sells.iloc[0]["price"] == pytest.approx(11.0)
        # 全部卖出
        assert sells.iloc[0]["size"] == pytest.approx(BUY_SHARES)

    def test_sell_all_on_low_open(self) -> None:
        """买入后第一日低开 2 个点 → 以开盘价全部卖出。"""
        # close[5] = 11.0, 低开阈值 = 11.0 * 0.98 = 10.78
        # open[6] = 10.50 <= 10.78 → 低开
        df = _base_data(bar6_open=10.50, bar6_close=10.80, n_extra=1)
        result = _run_backtest(df)
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        # 以开盘价卖出
        assert sells.iloc[0]["price"] == pytest.approx(10.50)
        # 全部卖出
        assert sells.iloc[0]["size"] == pytest.approx(BUY_SHARES)

    def test_sell_half_on_other_cases(self) -> None:
        """买入后第一日其余情况 → 以收盘价卖一半。"""
        # bar 6: 阳线（close > open），非低开
        df = _base_data(bar6_open=11.0, bar6_close=12.0, n_extra=1)
        result = _run_backtest(df, position_mode="fixed")
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        # 以收盘价卖出
        assert sells.iloc[0]["price"] == pytest.approx(12.0)
        # 卖一半
        assert sells.iloc[0]["size"] == pytest.approx(HALF_SHARES)

    def test_low_open_takes_priority_over_yin(self) -> None:
        """低开 + 阴线同时满足 → 以开盘价卖出（低开优先）。"""
        # close[5] = 11.0, 低开阈值 = 10.78
        # open[6] = 10.50 (低开), close[6] = 10.30 (阴线: close < open)
        df = _base_data(bar6_open=10.50, bar6_close=10.30, n_extra=1)
        result = _run_backtest(df)
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        # 以开盘价卖出（低开优先）
        assert sells.iloc[0]["price"] == pytest.approx(10.50)
        assert sells.iloc[0]["size"] == pytest.approx(BUY_SHARES)


# ── 完整周期测试 ─────────────────────────────────────────────────────────────


class TestFullCycle:
    """完整回测周期：买入 → 卖一半 → 持有 → 全卖。"""

    def test_cycle_sell_all_on_yin(self) -> None:
        """买入 → 卖一半 → 持有 → 阴线全卖。"""
        n = 9  # 9 根 bar
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n

        # Bar 5: 买入信号
        opens[5] = 10.5
        closes[5] = 11.0
        vols[5] = 2000.0

        # Bar 6: 阳线非低开 → 卖一半
        opens[6] = 11.0
        closes[6] = 12.0
        vols[6] = 1500.0

        # Bar 7: 阳线非低开 → 持有
        opens[7] = 12.0
        closes[7] = 12.5
        vols[7] = 1500.0

        # Bar 8: 阴线非低开 → 全卖
        # close[7] = 12.5, 低开阈值 = 12.5 * 0.98 = 12.25
        # open[8] = 12.5 > 12.25 (非低开), close[8] = 12.0 < 12.5 (阴线)
        opens[8] = 12.5
        closes[8] = 12.0
        vols[8] = 1500.0

        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert len(sells) == 2

        # 验证买入
        assert buys.iloc[0]["price"] == pytest.approx(BUY_PRICE)
        assert buys.iloc[0]["size"] == pytest.approx(BUY_SHARES)

        # 验证第一次卖出（卖一半，bar 6 收盘价）
        assert sells.iloc[0]["price"] == pytest.approx(12.0)
        assert sells.iloc[0]["size"] == pytest.approx(HALF_SHARES)

        # 验证第二次卖出（全卖，bar 8 收盘价）
        assert sells.iloc[1]["price"] == pytest.approx(12.0)
        assert sells.iloc[1]["size"] == pytest.approx(REMAIN_SHARES)

    def test_cycle_sell_all_on_low_open(self) -> None:
        """买入 → 卖一半 → 持有 → 低开全卖。"""
        n = 9
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n

        # Bar 5: 买入
        opens[5] = 10.5
        closes[5] = 11.0
        vols[5] = 2000.0

        # Bar 6: 阳线非低开 → 卖一半
        opens[6] = 11.0
        closes[6] = 12.0
        vols[6] = 1500.0

        # Bar 7: 阳线非低开 → 持有
        opens[7] = 12.0
        closes[7] = 12.5
        vols[7] = 1500.0

        # Bar 8: 低开 → 全卖
        # close[7] = 12.5, 低开阈值 = 12.25
        # open[8] = 12.0 <= 12.25 (低开)
        opens[8] = 12.0
        closes[8] = 12.5  # 即使收阳线，低开优先
        vols[8] = 1500.0

        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert len(sells) == 2

        # 验证买入
        assert buys.iloc[0]["price"] == pytest.approx(BUY_PRICE)

        # 验证第一次卖出（卖一半，bar 6 收盘价）
        assert sells.iloc[0]["price"] == pytest.approx(12.0)
        assert sells.iloc[0]["size"] == pytest.approx(HALF_SHARES)

        # 验证第二次卖出（全卖，bar 8 开盘价，因为低开）
        assert sells.iloc[1]["price"] == pytest.approx(12.0)
        assert sells.iloc[1]["size"] == pytest.approx(REMAIN_SHARES)

    def test_cycle_hold_multiple_bars_then_sell(self) -> None:
        """买入 → 卖一半 → 持有多日 → 阴线全卖。"""
        n = 11
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n

        # Bar 5: 买入
        opens[5] = 10.5
        closes[5] = 11.0
        vols[5] = 2000.0

        # Bar 6: 卖一半
        opens[6] = 11.0
        closes[6] = 12.0
        vols[6] = 1500.0

        # Bars 7-9: 持有（阳线非低开）
        for i in range(7, 10):
            opens[i] = closes[i - 1]
            closes[i] = closes[i - 1] + 0.5
            vols[i] = 1500.0

        # Bar 10: 阴线 → 全卖
        opens[10] = closes[9]
        closes[10] = closes[9] - 0.5
        vols[10] = 1500.0

        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert len(sells) == 2  # 卖一半 + 全卖

        # 第一次卖出是卖一半
        assert sells.iloc[0]["size"] == pytest.approx(HALF_SHARES)
        # 第二次卖出是全卖
        assert sells.iloc[1]["size"] == pytest.approx(REMAIN_SHARES)


# ── position_mode 测试 ───────────────────────────────────────────────────────


class TestPositionMode:
    """仓位模式对部分卖出的影响。"""

    def test_fixed_mode_partial_sell(self) -> None:
        """position_mode=fixed → 卖一半正常工作。"""
        df = _base_data(bar6_open=11.0, bar6_close=12.0, n_extra=1)
        result = _run_backtest(df, position_mode="fixed")
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        assert sells.iloc[0]["size"] == pytest.approx(HALF_SHARES)

    def test_full_mode_sells_all(self) -> None:
        """position_mode=full → 卖一半变为全仓卖出。"""
        df = _base_data(bar6_open=11.0, bar6_close=12.0, n_extra=1)
        result = _run_backtest(df, position_mode="full")
        sells = _get_trades(result, "SELL")
        assert len(sells) == 1
        # full 模式下所有卖出都是全仓
        assert sells.iloc[0]["size"] == pytest.approx(BUY_SHARES)

    def test_no_second_buy_while_holding(self) -> None:
        """持仓期间不产生第二次买入。"""
        n = 9
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n

        # Bar 5: 买入
        opens[5] = 10.5
        closes[5] = 11.0
        vols[5] = 2000.0

        # Bar 6: 阳线非低开 → 卖一半
        opens[6] = 11.0
        closes[6] = 12.0
        vols[6] = 1500.0

        # Bar 7: 再次满足买入条件（阳线+放量+MA5上升）
        opens[7] = 12.0
        closes[7] = 13.0
        vols[7] = 3000.0

        # Bar 8: 阴线 → 全卖
        opens[8] = 13.0
        closes[8] = 12.5
        vols[8] = 1500.0

        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        # 持仓期间不应产生第二次买入
        assert len(buys) == 1
