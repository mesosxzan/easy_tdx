"""Test RebalanceEngine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from easy_tdx.portfolio.optimizer import EqualWeightOptimizer, FactorWeightedOptimizer
from easy_tdx.portfolio.rebalance import RebalanceEngine


def _make_market(n_stocks: int = 10, n_days: int = 120, seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    data = {}
    for i in range(n_stocks):
        close = 10.0 + np.cumsum(rng.normal(0.01, 0.5, n_days))
        close = np.maximum(close, 1.0)
        data[f"{i:06d}"] = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-01-01", periods=n_days, freq="D"),
                "open": close,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "vol": rng.integers(1e5, 1e7, n_days).astype(float),
                "amount": close * 1e6,
            }
        )
    return data


class TestRebalanceEngine:
    def test_basic_run(self):
        engine = RebalanceEngine(
            optimizer=EqualWeightOptimizer(),
            factor_name="momentum_20d",
            n_stocks=5,
            rebalance_freq="M",
            cash=1_000_000,
        )
        result = engine.run(_make_market(), start_date=20240101, end_date=20240430)
        assert len(result.states) > 0
        assert len(result.rebalance_dates) > 0
        assert len(result.equity_curve) > 0
        assert "total_return" in result.performance

    def test_with_factor_weighted(self):
        engine = RebalanceEngine(
            optimizer=FactorWeightedOptimizer(),
            factor_name="momentum_20d",
            n_stocks=5,
        )
        result = engine.run(_make_market(), start_date=20240101, end_date=20240430)
        assert len(result.states) > 0

    def test_empty_data(self):
        result = RebalanceEngine(optimizer=EqualWeightOptimizer()).run({})
        assert result.performance["total_return"] == 0.0

    def test_equity_curve_dates_sorted(self):
        result = RebalanceEngine(
            optimizer=EqualWeightOptimizer(),
            rebalance_freq="M",
        ).run(_make_market(), start_date=20240101, end_date=20240430)
        dates = result.equity_curve["datetime"].tolist()
        assert dates == sorted(dates)

    def test_trades_recorded(self):
        engine = RebalanceEngine(
            optimizer=EqualWeightOptimizer(),
            n_stocks=3,
            rebalance_freq="M",
        )
        result = engine.run(_make_market(), start_date=20240101, end_date=20240430)
        assert len(result.trades) > 0
        assert "BUY" in result.trades["direction"].values

    def test_total_trades_matches_trade_rows(self):
        """issue #25: performance['total_trades'] 应等于真实交易笔数，而非天数。"""
        engine = RebalanceEngine(
            optimizer=EqualWeightOptimizer(),
            n_stocks=3,
            rebalance_freq="M",
        )
        result = engine.run(_make_market(), start_date=20240101, end_date=20240430)
        assert result.performance["total_trades"] == len(result.trades)
        # 修复前 total_trades == len(equity_curve)（天数），明显大于交易笔数
        assert result.performance["total_trades"] != len(result.equity_curve)
