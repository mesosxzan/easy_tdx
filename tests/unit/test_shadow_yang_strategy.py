"""影线后收阳线策略（ShadowYangStrategy）单元测试。

测试覆盖：
  - 买入信号检测（阳线 + MA5 上升 + 前日阴线 + 上影线过滤）
  - 卖出逻辑（阴线全卖 / 低开全卖 / 其他卖一半）
  - 完整回测周期（买入 → 卖一半 → 持有 → 全卖）
  - position_mode 对部分卖出的影响
  - 多头排列粘合止损（MA5/MA10 粘合 + 开盘破 MA10 + 收阴）
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

    Bars 0-3: 平盘（close=10, vol=1000），MA5 平坦
    Bar 4: 阴线（open=10.5, close=10.0），满足“前一天必须收阴线”
    Bar 5: 阳线（close=11, open=10.5），放量（vol=2000），MA5 上升 → 触发买入
    Bar 6+: 由参数控制，用于测试不同卖出场景
    """
    n = 6 + n_extra
    opens = [10.0] * n
    closes = [10.0] * n
    vols = [1000.0] * n

    # Bar 4: 阴线（前一天必须收阴线才能触发买入）
    opens[4] = 10.5

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

    def test_no_buy_when_prev_not_yin(self) -> None:
        """前一天非阴线（close >= open）→ 不买入。"""
        df = _base_data(n_extra=0)
        # 将 bar 4 改为平盘（open=close=10.0，非阴线）
        df.loc[4, "open"] = 10.0
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_no_buy_when_ma5_down_not_piercing(self) -> None:
        """MA5 斜率下降且非刺透形态 -> 不买入。"""
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 10.0]
        vols = [1000.0] * n
        # Bar 4: 阴线（前一天必须收阴线才能触发买入）
        opens[4] = 10.5
        # Bar 5: 阳线，开盘价 = prev_low（不满足刺透形态的跳空低开条件）
        opens[5] = 9.5
        closes[5] = 10.0
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_buy_when_ma5_down_with_piercing_line(self) -> None:
        """MA5 斜率下降但当日为刺透形态 -> 买入。"""
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 10.3]
        vols = [1000.0] * n
        # Bar 4: 阴线（open=10.5, close=10.0, low=9.5, high=11.0）
        opens[4] = 10.5
        # Bar 5: 刺透形态（开盘低于前日最低价 9.5，
        #   收盘 10.3 > 中点 10.25 且 < 前日开盘 10.5）
        opens[5] = 9.0
        closes[5] = 10.3
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(10.3)

    def test_buy_when_ma5_down_with_bullish_engulfing(self) -> None:
        """MA5 斜率下降但当日为看涨吞没形态 -> 买入。

        看涨吞没：当日阳线实体完全覆盖前日阴线实体
        （开盘 <= 前日收盘，收盘 >= 前日开盘）。
        """
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 10.6]
        vols = [1000.0] * n
        # Bar 4: 阴线（open=10.5, close=10.0, body=0.5）
        opens[4] = 10.5
        # Bar 5: 看涨吞没（开盘 9.8 <= 前日收盘 10.0，收盘 10.6 >= 前日开盘 10.5）
        opens[5] = 9.8
        closes[5] = 10.6
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(10.6)

    def test_buy_when_ma5_down_with_morning_star(self) -> None:
        """MA5 斜率下降但当日为启明星形态 -> 买入。

        启明星（三根 K 线）：
          - 前两日大阴线（body=1.0）
          - 前一日小实体星线（body=0.4 < 1.0×0.5），实体在前两日实体之下
          - 当日阳线，收盘 > 前两日实体中点
        且非刺透形态、非吞没形态（确保只有启明星触发）。
        """
        n = 7
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 9.5, 9.0, 10.2]
        vols = [1000.0] * n
        # Bar 4 (前两日): 大阴线（open=10.5, close=9.5, body=1.0）
        opens[4] = 10.5
        # Bar 5 (前一日): 小实体星线（open=9.4, close=9.0, body=0.4）
        #   body=0.4 < prev2_body×0.5=0.5, max(9.4,9.0)=9.4 < prev2_close=9.5
        opens[5] = 9.4
        # Bar 6 (当日): 阳线，收盘 10.2 > 中点 10.0
        #   open=9.1 > prev_close=9.0（非吞没），> prev_low=8.5（非刺透）
        opens[6] = 9.1
        vols[6] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(10.2)

    def test_buy_when_ma5_down_with_strong_reversal_yang(self) -> None:
        """MA5 斜率下降但当日为强势反转阳线 -> 买入。

        强势反转阳线：收盘突破前日实体顶部，实体 >= 前日实体 × 2 倍，
        但开盘高于前日收盘（不满足吞没），开盘高于前日最低（不满足刺透）。
        对应 2026-06-15 600703 场景：轻微高开后强势涨停。
        """
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 11.2]
        vols = [1000.0] * n
        # Bar 4: 阴线（open=10.5, close=10.0, body=0.5）
        opens[4] = 10.5
        # Bar 5: 强势反转阳线
        #   open=10.1 > prev_close=10.0（非吞没），> prev_low=9.5（非刺透）
        #   close=11.2 >= prev_open=10.5，body=1.1 >= 0.5×2=1.0
        opens[5] = 10.1
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(11.2)

    def test_no_buy_when_strong_reversal_body_below_2x(self) -> None:
        """MA5 下行，收盘突破前日开盘但实体不足2倍 -> 不买入。

        收盘 >= 前日开盘满足，但实体 0.8 < 0.5×2=1.0，不构成强势反转。
        且非吞没（开盘 > 前日收盘）、非刺透（开盘 > 前日最低）、非启明星。
        """
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 10.9]
        vols = [1000.0] * n
        # Bar 4: 阴线（open=10.5, close=10.0, body=0.5）
        opens[4] = 10.5
        # Bar 5: 阳线，close=10.9 >= prev_open=10.5，但 body=0.8 < 1.0
        opens[5] = 10.1
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 0

    def test_buy_when_strong_reversal_at_exact_2x_boundary(self) -> None:
        """实体恰好等于前日实体 × 2.0 时触发买入（>= 边界包含）。"""
        n = 6
        opens = [10.0] * n
        closes = [12.0, 11.0, 10.0, 10.0, 10.0, 11.1]
        vols = [1000.0] * n
        # Bar 4: 阴线（open=10.5, close=10.0, body=0.5）
        opens[4] = 10.5
        # Bar 5: 阳线，close=11.1 >= prev_open=10.5，body=1.0 = 0.5×2.0
        opens[5] = 10.1
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(11.1)

    def test_buy_with_upper_shadow_at_1_5x_ratio(self) -> None:
        """前日阴线上影 = 实体 × 1.5 时不排除买入（边界测试）。

        UPPER_SHADOW_RATIO 从 1.0 改为 1.5：上影 <= 实体 × 1.5 时放行。
        在 1.0 倍阈值下此上影会被排除，在 1.5 倍阈值下放行。
        """
        n = 6
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n
        # Bar 4: 阴线（open=11.0, close=10.0, body=1.0）
        opens[4] = 11.0
        # Bar 5: 阳线，MA5 上行 -> 买入
        opens[5] = 10.0
        closes[5] = 11.0
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        # 自定义 bar 4 的 high：上影 = 1.5 = body × 1.5（严格大于才排除）
        df.loc[4, "high"] = 12.5
        result = _run_backtest(df)
        buys = _get_trades(result, "BUY")
        assert len(buys) == 1

    def test_no_buy_with_upper_shadow_above_1_5x_ratio(self) -> None:
        """前日阴线上影 > 实体 × 1.5 时排除买入。"""
        n = 6
        opens = [10.0] * n
        closes = [10.0] * n
        vols = [1000.0] * n
        # Bar 4: 阴线（open=11.0, close=10.0, body=1.0）
        opens[4] = 11.0
        # Bar 5: 阳线，MA5 上行
        opens[5] = 10.0
        closes[5] = 11.0
        vols[5] = 2000.0
        df = _make_df(opens, closes, vols)
        # 自定义 bar 4 的 high：上影 = 1.6 > body × 1.5 = 1.5
        df.loc[4, "high"] = 12.6
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
        """买入后第一日低开 3 个点 → 以开盘价全部卖出。"""
        # close[5] = 11.0, 低开阈值 = 11.0 * 0.97 = 10.67
        # open[6] = 10.50 <= 10.67 → 低开
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
        # close[5] = 11.0, 低开阈值 = 11.0 * 0.97 = 10.67
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

        # Bar 4: 阴线（前一天必须收阴线才能触发买入）
        opens[4] = 10.5

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
        # close[7] = 12.5, 低开阈值 = 12.5 * 0.97 = 12.125
        # open[8] = 12.5 > 12.125 (非低开), close[8] = 12.0 < 12.5 (阴线)
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

        # Bar 4: 阴线（前一天必须收阴线才能触发买入）
        opens[4] = 10.5

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
        # close[7] = 12.5, 低开阈值 = 12.5 * 0.97 = 12.125
        # open[8] = 12.0 <= 12.125 (低开)
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

        # Bar 4: 阴线（前一天必须收阴线才能触发买入）
        opens[4] = 10.5

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

        # Bar 4: 阴线（前一天必须收阴线才能触发买入）
        opens[4] = 10.5

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


# ── 多头排列粘合止损测试 ─────────────────────────────────────────────────────


def _bullish_data(
    bar66_open: float = 10.4,
    bar66_close: float = 10.2,
) -> pd.DataFrame:
    """构造多头排列测试数据（67 根 K 线，MA60 有效）。

    数据结构：
      Bars 0-55: close=10.0（平坦，MA60=10.0）
      Bars 56-63: close=10.5（平台，MA10/MA20 抬升）
      Bar 64: 阴线（open=10.5, close=9.8，body=0.7 >= upper_shadow=0.5）
      Bar 65: 阳线（open=9.8, close=10.6，MA5 上行 -> 买入）
      Bar 66: 由参数控制（测试粘合止损）

    买入后 bar 66 满足多头排列（MA5 > MA20 > MA60、MA10 > MA20 > MA60），
    MA5 ≈ MA10（粘合）。
    """
    n = 67
    opens = [10.0] * n
    closes = [10.0] * n
    vols = [1000.0] * n

    # Bars 56-63: 平台 10.5
    for i in range(56, 64):
        opens[i] = 10.5
        closes[i] = 10.5

    # Bar 64: 阴线（body=0.7, upper_shadow=0.5, 非长上影）
    opens[64] = 10.5
    closes[64] = 9.8

    # Bar 65: 阳线买入信号（MA5 上行）
    opens[65] = 9.8
    closes[65] = 10.6
    vols[65] = 2000.0

    # Bar 66: 由参数控制
    opens[66] = bar66_open
    closes[66] = bar66_close

    return _make_df(opens, closes, vols)


# 买入价 = bar 65 收盘价
BULLISH_BUY_PRICE = 10.6
BULLISH_BUY_SHARES = 9400  # floor(100000 / 10.6033 / 100) * 100


class TestConvergenceStopLoss:
    """多头排列粘合止损测试。"""

    def test_convergence_stop_loss_triggers(self) -> None:
        """粘合 + 开盘破 MA10 + 收阴 -> 以收盘价全仓卖出。"""
        # bar 66: open=10.4 < MA10(≈10.41), close=10.2 < open(阴线)
        # MA5/MA10 粘合（差值 < 2%），多头排列
        df = _bullish_data(bar66_open=10.4, bar66_close=10.2)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(BULLISH_BUY_PRICE)
        assert len(sells) == 1
        # 以收盘价全仓卖出
        assert sells.iloc[0]["price"] == pytest.approx(10.2)
        assert sells.iloc[0]["size"] == pytest.approx(BULLISH_BUY_SHARES)

    def test_no_sell_when_converged_yang_line(self) -> None:
        """粘合 + 阳线（非阴线）+ close >= MA10 -> 不触发粘合，不触发斜率 -> 无卖出。"""
        # bar 66: open=10.3, close=10.45 (阳线, close > open)
        # close=10.45 >= MA10(≈10.435) -> 斜率止损不触发
        # 阳线 -> 粘合止损不触发（需要收阴）
        df = _bullish_data(bar66_open=10.3, bar66_close=10.45)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        # 无卖出（粘合需要阴线，斜率需要 close < MA10）
        assert len(sells) == 0

    def test_no_sell_when_converged_slow_rise_open_above_ma10(self) -> None:
        """粘合 + 缓涨 + 阴线，但开盘价 > MA10（收盘价 < MA10）-> 不止损。

        修复 02-27 场景：多头排列 + 粘合 + 缓涨时，缓涨止损要求
        max(开盘价, 收盘价) < MA10，而非仅收盘价 < MA10。
        当开盘价高于 MA10 时，即使收盘价跌破 MA10 也不止损。
        """
        # bar 66: open=10.45 > MA10(≈10.42), close=10.30 < MA10 (阴线)
        # 粘合止损: open(10.45) > MA10 -> 不触发
        # 缓涨粘合止损: max(10.45, 10.30)=10.45 > MA10 -> 不触发
        df = _bullish_data(bar66_open=10.45, bar66_close=10.30)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert len(sells) == 0

    def test_slow_rise_convergence_stop_triggers(self) -> None:
        """粘合 + 缓涨 + 阳线，开盘价与收盘价均 < MA10 -> 缓涨粘合止损触发。

        粘合止损不触发（需要阴线），但缓涨粘合止损触发：
        max(开盘价, 收盘价) < MA10，以收盘价全仓卖出。
        """
        # bar 66: open=10.30 < MA10(≈10.425), close=10.35 < MA10 (阳线, close > open)
        # 粘合止损: 阳线 -> 不触发（需要收阴）
        # 缓涨粘合止损: max(10.30, 10.35)=10.35 < MA10 -> 触发
        df = _bullish_data(bar66_open=10.30, bar66_close=10.35)
        result = _run_backtest(df)

        buys = _get_trades(result, "BUY")
        sells = _get_trades(result, "SELL")
        assert len(buys) == 1
        assert buys.iloc[0]["price"] == pytest.approx(BULLISH_BUY_PRICE)
        assert len(sells) == 1
        # 以收盘价全仓卖出
        assert sells.iloc[0]["price"] == pytest.approx(10.35)
        assert sells.iloc[0]["size"] == pytest.approx(BULLISH_BUY_SHARES)
