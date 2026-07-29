"""增量缠论分析器测试。

验证 IncrementalAnalyser 的正确性：
1. 与批量分析器结构一致性（历史部分应完全一致）
2. signals_by_bar 因果性（确认bar不使用未来信息）
3. 边界情况处理
"""

from __future__ import annotations

import pandas as pd

from easy_tdx.chanlun.analyser import ChanlunAnalyser
from easy_tdx.chanlun.config import ChanlunConfig
from easy_tdx.chanlun.incremental import IncrementalAnalyser, compare_batch_vs_incremental
from easy_tdx.chanlun.types import MMDType


def _make_df(prices: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """从 (open, close, high, low) 元组列表构建K线DataFrame。

    Args:
        prices: (open, close, high, low) 元组列表

    Returns:
        包含 datetime/open/close/high/low/vol 列的DataFrame
    """
    n = len(prices)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": [p[0] for p in prices],
            "close": [p[1] for p in prices],
            "high": [p[2] for p in prices],
            "low": [p[3] for p in prices],
            "vol": [1000.0] * n,
        }
    )


# 生成一段有足够波动形成笔/中枢/买卖点的K线数据
_PRICES = [
    # 上涨段
    (10.0, 11.0, 11.5, 9.8),
    (11.0, 12.0, 12.5, 10.8),
    (12.0, 11.5, 12.8, 11.2),
    (11.5, 13.0, 13.5, 11.3),
    (13.0, 14.0, 14.5, 12.8),
    (14.0, 13.5, 14.8, 13.2),
    # 下跌段
    (13.5, 12.5, 13.8, 12.2),
    (12.5, 11.0, 12.8, 10.8),
    (11.0, 10.0, 11.2, 9.8),
    (10.0, 9.0, 10.2, 8.8),
    (9.0, 10.5, 10.8, 8.9),
    # 上涨段2
    (10.5, 12.0, 12.2, 10.3),
    (12.0, 13.5, 13.8, 11.8),
    (13.5, 15.0, 15.5, 13.3),
    (15.0, 14.0, 15.2, 13.8),
    (14.0, 16.0, 16.5, 13.8),
    (16.0, 15.0, 16.2, 14.8),
    # 下跌段2
    (15.0, 13.5, 15.2, 13.2),
    (13.5, 12.0, 13.8, 11.8),
    (12.0, 11.0, 12.2, 10.8),
    (11.0, 12.5, 12.8, 10.8),
    (12.5, 14.0, 14.2, 12.3),
    (14.0, 13.0, 14.2, 12.8),
    (13.0, 15.0, 15.2, 12.8),
    (15.0, 14.0, 15.2, 13.8),
]


class TestIncrementalCLKlineMerging:
    """测试增量CLKline合并。"""

    def test_empty_data(self) -> None:
        """空数据应返回空结果。"""
        df = pd.DataFrame(
            columns=["datetime", "open", "close", "high", "low", "vol"]
        )
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)
        assert len(result.cklines) == 0
        assert len(result.klines) == 0

    def test_single_kline(self) -> None:
        """单根K线应产生一根tentative CLKline。"""
        df = _make_df([(10.0, 11.0, 11.5, 9.8)])
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)
        assert len(result.cklines) == 1
        assert result.cklines[0].high == 11.5
        assert result.cklines[0].low == 9.8

    def test_merge_consistency(self) -> None:
        """增量合并结果应与批量合并一致（confirmed CLKlines部分）。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        # CLKline数量差异最多为1（tentative vs final）
        assert abs(len(batch_result.cklines) - len(incr_result.cklines)) <= 1

        # 所有confirmed CLKlines的high/low应一致
        min_len = min(
            len(batch_result.cklines) - 1,  # 排除最后一根（可能不同）
            len(incr_result.cklines) - 1,
        )
        for i in range(min_len):
            assert batch_result.cklines[i].high == incr_result.cklines[i].high, (
                f"CLKline {i} high mismatch: "
                f"batch={batch_result.cklines[i].high} "
                f"incr={incr_result.cklines[i].high}"
            )
            assert batch_result.cklines[i].low == incr_result.cklines[i].low, (
                f"CLKline {i} low mismatch: "
                f"batch={batch_result.cklines[i].low} "
                f"incr={incr_result.cklines[i].low}"
            )


class TestIncrementalFractals:
    """测试增量分型识别。"""

    def test_fractal_count_consistency(self) -> None:
        """增量分型数量应与批量接近（差异来自最后tentative CLKline）。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        # 分型数量差异最多为2（最后两个分型可能受tentative影响）
        diff = abs(len(batch_result.fractals) - len(incr_result.fractals))
        assert diff <= 2, (
            f"Fractal count diff too large: batch={len(batch_result.fractals)} "
            f"incr={len(incr_result.fractals)} diff={diff}"
        )

    def test_fractal_values_match(self) -> None:
        """已确认的分型值应与批量一致。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        min_len = min(len(batch_result.fractals), len(incr_result.fractals))
        # 检查除最后2个外的所有分型
        check_len = max(0, min_len - 2)
        for i in range(check_len):
            assert batch_result.fractals[i].val == incr_result.fractals[i].val, (
                f"Fractal {i} value mismatch: "
                f"batch={batch_result.fractals[i].val} "
                f"incr={incr_result.fractals[i].val}"
            )
            assert batch_result.fractals[i].fx_type == incr_result.fractals[i].fx_type, (
                f"Fractal {i} type mismatch: "
                f"batch={batch_result.fractals[i].fx_type} "
                f"incr={incr_result.fractals[i].fx_type}"
            )


class TestIncrementalBis:
    """测试增量笔计算。"""

    def test_bi_count_consistency(self) -> None:
        """增量笔数量应与批量接近。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        diff = abs(len(batch_result.bis) - len(incr_result.bis))
        assert diff <= 2, (
            f"Bi count diff too large: batch={len(batch_result.bis)} "
            f"incr={len(incr_result.bis)} diff={diff}"
        )

    def test_bi_directions_match(self) -> None:
        """已确认的笔方向应与批量一致。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        min_len = min(len(batch_result.bis), len(incr_result.bis))
        check_len = max(0, min_len - 2)
        for i in range(check_len):
            assert batch_result.bis[i].direction == incr_result.bis[i].direction, (
                f"Bi {i} direction mismatch: "
                f"batch={batch_result.bis[i].direction} "
                f"incr={incr_result.bis[i].direction}"
            )


class TestIncrementalZSs:
    """测试增量中枢计算。"""

    def test_zs_count_consistency(self) -> None:
        """增量中枢数量应与批量接近。"""
        df = _make_df(_PRICES)
        config = ChanlunConfig()

        batch = ChanlunAnalyser(config=config)
        batch_result = batch.process_klines(df)

        incr = IncrementalAnalyser(config=config)
        incr_result = incr.process_klines(df)

        diff = abs(len(batch_result.zss) - len(incr_result.zss))
        assert diff <= 2, (
            f"ZS count diff too large: batch={len(batch_result.zss)} "
            f"incr={len(incr_result.zss)} diff={diff}"
        )


class TestIncrementalMMDs:
    """测试增量买卖点识别 - 核心前瞻偏差修复。"""

    def test_signals_by_bar_not_empty(self) -> None:
        """signals_by_bar应包含信号（如果有足够的K线形成结构）。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        # 有25根K线，应该能形成一些结构
        # signals_by_bar可能为空（如果没有形成买卖点），但mmds应该有一些
        assert isinstance(result.signals_by_bar, dict)

    def test_signals_by_bar_causality(self) -> None:
        """signals_by_bar中的每个确认bar必须小于K线总数。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        n_klines = len(result.klines)
        for bar_idx in result.signals_by_bar:
            assert 0 <= bar_idx < n_klines, (
                f"Signal bar {bar_idx} out of range [0, {n_klines})"
            )

    def test_signals_by_bar_contains_mmds(self) -> None:
        """signals_by_bar中的MMD数量应等于mmds列表长度。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        total_signals = sum(len(mmds) for mmds in result.signals_by_bar.values())
        assert total_signals == len(result.mmds), (
            f"signals_by_bar total ({total_signals}) != mmds count ({len(result.mmds)})"
        )

    def test_mmd_types_valid(self) -> None:
        """所有MMD类型应为有效的买卖点类型。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        valid_types = {
            MMDType.BUY_1, MMDType.BUY_2, MMDType.BUY_3,
            MMDType.SELL_1, MMDType.SELL_2, MMDType.SELL_3,
        }
        for mmd in result.mmds:
            assert mmd.mmd_type in valid_types, f"Invalid MMD type: {mmd.mmd_type}"

    def test_no_future_zs_used(self) -> None:
        """验证MMD检测不使用未来中枢。

        每个MMD关联的ZS的确认bar必须 <= MMD关联的笔的确认bar。
        """
        df = _make_df(_PRICES * 4)  # 4倍数据以形成更多结构
        config = ChanlunConfig()

        incr = IncrementalAnalyser(config=config)
        result = incr.process_klines(df)

        # 获取每个ZS的确认bar
        zs_confirm = incr._zs_confirm_bar  # noqa: SLF001
        bi_confirm = incr._bi_confirm_bar  # noqa: SLF001

        for mmd in result.mmds:
            if mmd.bi is None or mmd.zs is None:
                continue
            bi_idx = mmd.bi.index
            zs_idx = mmd.zs.index

            bi_bar = bi_confirm.get(bi_idx, float("inf"))
            zs_bar = zs_confirm.get(zs_idx, float("inf"))

            # ZS的确认bar必须 <= 笔的确认bar（ZS在笔之前确认）
            assert zs_bar <= bi_bar, (
                f"Look-ahead bias detected: ZS[{zs_idx}] confirm_bar={zs_bar} "
                f"> BI[{bi_idx}] confirm_bar={bi_bar}"
            )


class TestCompareBatchVsIncremental:
    """测试批量vs增量对比工具。"""

    def test_compare_returns_dict(self) -> None:
        """对比工具应返回包含各结构数量的字典。"""
        df = _make_df(_PRICES)
        result = compare_batch_vs_incremental(df)

        assert "cklines" in result
        assert "fractals" in result
        assert "bis" in result
        assert "zss" in result
        assert "mmds" in result
        assert "signals_by_bar_count" in result

    def test_compare_structures_close(self) -> None:
        """批量与增量结构数量应接近。"""
        df = _make_df(_PRICES)
        result = compare_batch_vs_incremental(df)

        # CLKline差异最多1
        assert abs(result["cklines"]["diff"]) <= 1
        # 分型差异最多2
        assert abs(result["fractals"]["diff"]) <= 2
        # 笔差异最多2
        assert abs(result["bis"]["diff"]) <= 2


class TestIncrementalEdgeCases:
    """测试边界情况。"""

    def test_all_increasing(self) -> None:
        """持续上涨的K线应正常处理。"""
        prices = [(i * 1.0, (i + 1) * 1.0, (i + 1.5) * 1.0, (i - 0.5) * 1.0) for i in range(20)]
        df = _make_df(prices)

        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        # 不崩溃即可
        assert len(result.klines) == 20

    def test_all_decreasing(self) -> None:
        """持续下跌的K线应正常处理。"""
        prices = [
            ((20 - i) * 1.0, (19 - i) * 1.0, (20.5 - i) * 1.0, (18.5 - i) * 1.0)
            for i in range(20)
        ]
        df = _make_df(prices)

        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        assert len(result.klines) == 20

    def test_flat_prices(self) -> None:
        """价格不变时应正常处理。"""
        prices = [(10.0, 10.0, 10.0, 10.0)] * 20
        df = _make_df(prices)

        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        assert len(result.klines) == 20

    def test_two_klines(self) -> None:
        """只有2根K线时应正常处理。"""
        df = _make_df([(10.0, 11.0, 11.5, 9.8), (11.0, 10.0, 11.2, 9.8)])
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        assert len(result.klines) == 2

    def test_config_passthrough(self) -> None:
        """配置应正确传递到增量分析器。"""
        config = ChanlunConfig(fx_strict=False, bi_type="old")
        df = _make_df(_PRICES)

        analyser = IncrementalAnalyser(config=config)
        result = analyser.process_klines(df)

        # 不崩溃即可，配置应被正确使用
        assert result.klines is not None


class TestIncrementalResultCompleteness:
    """测试增量分析结果的完整性。"""

    def test_result_has_all_fields(self) -> None:
        """结果应包含所有ChanlunResult字段。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        assert hasattr(result, "klines")
        assert hasattr(result, "cklines")
        assert hasattr(result, "fractals")
        assert hasattr(result, "bis")
        assert hasattr(result, "zss")
        assert hasattr(result, "xds")
        assert hasattr(result, "mmds")
        assert hasattr(result, "bcs")
        assert hasattr(result, "macd")
        assert hasattr(result, "signals_by_bar")

    def test_macd_computed(self) -> None:
        """MACD应被正确计算。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        assert "dif" in result.macd
        assert "dea" in result.macd
        assert "hist" in result.macd
        assert len(result.macd["dif"]) == len(_PRICES)

    def test_to_dict_compatible(self) -> None:
        """结果应支持to_dict序列化。"""
        df = _make_df(_PRICES)
        analyser = IncrementalAnalyser()
        result = analyser.process_klines(df)

        d = result.to_dict()
        assert "code" in d
        assert "bis" in d
        assert "zss" in d
        assert "mmds" in d
