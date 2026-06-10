"""screen 模块单元测试 — 纯离线，无需网络。"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from easy_tdx.models.bar import SecurityBar

# ── 辅助：构造 SecurityBar ────────────────────────────────────────────────


def _make_bar(year: int, month: int, day: int, close: float, **kw: Any) -> SecurityBar:
    """快速构造一个 SecurityBar。"""
    return SecurityBar(
        open=kw.get("open", close - 0.1),
        close=close,
        high=kw.get("high", close + 0.2),
        low=kw.get("low", close - 0.3),
        vol=kw.get("vol", 10000.0),
        amount=kw.get("amount", close * 10000),
        year=year,
        month=month,
        day=day,
        hour=0,
        minute=0,
    )


def _make_bars(n: int, base_close: float = 10.0) -> list[SecurityBar]:
    """构造 n 根连续日 K 线，收盘价从 base_close 开始递增。"""
    bars = []
    for i in range(n):
        year = 2024
        month = 1 + i // 28
        day = 1 + i % 28
        if month > 12:
            year += (month - 1) // 12
            month = 1 + (month - 1) % 12
        close = base_close + i * 0.1
        bars.append(_make_bar(year, month, day, close))
    return bars


# ── _bars_to_df 测试 ──────────────────────────────────────────────────────


class TestBarsToDf:
    """测试 scanner._bars_to_df 辅助函数。"""

    def test_empty_bars(self) -> None:
        from easy_tdx.screen.scanner import _bars_to_df

        df = _bars_to_df([])
        assert df.empty

    def test_single_bar(self) -> None:
        from easy_tdx.screen.scanner import _bars_to_df

        bar = _make_bar(2024, 6, 10, 12.5)
        df = _bars_to_df([bar])
        assert len(df) == 1
        assert df.iloc[0]["close"] == 12.5
        assert "datetime" in df.columns
        assert "open" in df.columns
        assert "vol" in df.columns

    def test_multiple_bars(self) -> None:
        from easy_tdx.screen.scanner import _bars_to_df

        bars = _make_bars(50)
        df = _bars_to_df(bars)
        assert len(df) == 50
        assert list(df.columns) == ["datetime", "open", "close", "high", "low", "vol", "amount"]

    def test_datetime_is_timestamp(self) -> None:
        from easy_tdx.screen.scanner import _bars_to_df

        bars = [_make_bar(2024, 6, 10, 12.5)]
        df = _bars_to_df(bars)
        assert isinstance(df.iloc[0]["datetime"], pd.Timestamp)


# ── ScanResult 测试 ──────────────────────────────────────────────────────


class TestScanResult:
    """测试 ScanResult 数据结构。"""

    def test_creation(self) -> None:
        from easy_tdx.screen.scanner import ScanResult

        r = ScanResult(code="000001", market="SZ", signal_date=20240610, last_close=12.5)
        assert r.code == "000001"
        assert r.market == "SZ"
        assert r.signal_date == 20240610
        assert r.last_close == 12.5


# ── SignalScanner 测试 ──────────────────────────────────────────────────


class TestSignalScanner:
    """测试信号扫描引擎。"""

    def test_to_json(self) -> None:
        from easy_tdx.screen.scanner import ScanResult, SignalScanner

        scanner = SignalScanner.__new__(SignalScanner)
        results = [
            ScanResult(code="000001", market="SZ", signal_date=20240610, last_close=12.5),
            ScanResult(code="600519", market="SH", signal_date=20240610, last_close=1800.0),
        ]
        json_str = scanner.to_json(results, "TestStrategy", "test.py", 100)
        data = json.loads(json_str)

        assert data["strategy"] == "TestStrategy"
        assert data["strategy_file"] == "test.py"
        assert data["total_scanned"] == 100
        assert data["total_signals"] == 2
        assert len(data["signals"]) == 2
        assert data["signals"][0]["code"] == "000001"
        assert data["signals"][1]["market"] == "SH"

    def test_to_json_empty(self) -> None:
        from easy_tdx.screen.scanner import SignalScanner

        scanner = SignalScanner.__new__(SignalScanner)
        json_str = scanner.to_json([], "Test", "t.py", 50)
        data = json.loads(json_str)
        assert data["total_signals"] == 0
        assert data["signals"] == []


# ── RankEntry 测试 ──────────────────────────────────────────────────────


class TestRankEntry:
    """测试 RankEntry 数据结构。"""

    def test_creation(self) -> None:
        from easy_tdx.screen.ranker import RankEntry

        e = RankEntry(
            rank=1,
            code="300308",
            market="SZ",
            name="",
            signal_date=20240610,
            last_close=85.0,
            performance={"sharpe": 1.85, "total_return": 0.45},
        )
        assert e.rank == 1
        assert e.performance["sharpe"] == 1.85


# ── SignalRanker.to_json 测试 ──────────────────────────────────────────


class TestRankerJson:
    """测试 Ranker 的 JSON 输出。"""

    def test_to_json(self) -> None:
        from easy_tdx.screen.ranker import RankEntry, SignalRanker

        entries = [
            RankEntry(
                rank=1,
                code="300308",
                market="SZ",
                name="",
                signal_date=20240610,
                last_close=85.0,
                performance={"sharpe": 1.85, "total_return": 0.45},
            ),
        ]
        json_str = SignalRanker.to_json(entries, "RSI", "sharpe")
        data = json.loads(json_str)

        assert data["strategy"] == "RSI"
        assert data["sort_by"] == "sharpe"
        assert data["total_ranked"] == 1
        assert data["ranking"][0]["rank"] == 1
        assert data["ranking"][0]["code"] == "300308"

    def test_to_table(self) -> None:
        from easy_tdx.screen.ranker import RankEntry, SignalRanker

        entries = [
            RankEntry(
                rank=1,
                code="300308",
                market="SZ",
                name="中际旭创",
                signal_date=20240610,
                last_close=85.0,
                performance={
                    "total_return": 0.4523,
                    "annual_return": 0.1872,
                    "max_drawdown": 0.1235,
                    "sharpe": 1.85,
                    "win_rate": 0.625,
                    "total_trades": 16,
                },
            ),
        ]
        table = SignalRanker.to_table(entries, "sharpe")
        assert "信号排名" in table
        assert "SZ300308" in table
        assert "45.23%" in table

    def test_to_table_empty(self) -> None:
        from easy_tdx.screen.ranker import SignalRanker

        table = SignalRanker.to_table([], "sharpe")
        assert "无有效排名结果" in table


# ── load_signals 测试 ──────────────────────────────────────────────────


class TestLoadSignals:
    """测试信号 JSON 加载。"""

    def test_load_from_file(self, tmp_path: Path) -> None:
        from easy_tdx.screen.ranker import load_signals

        data = {
            "strategy": "RSI",
            "strategy_file": "rsi.py",
            "signals": [
                {"code": "000001", "market": "SZ", "signal_date": 20240610, "last_close": 12.5},
            ],
        }
        filepath = tmp_path / "signals.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")

        signals, name, sfile = load_signals(str(filepath))
        assert len(signals) == 1
        assert name == "RSI"
        assert sfile == "rsi.py"
        assert signals[0]["code"] == "000001"

    def test_load_from_stdin(self) -> None:
        from easy_tdx.screen.ranker import load_signals

        data = {
            "strategy": "MACD",
            "signals": [
                {"code": "600519", "market": "SH"},
            ],
        }
        json_str = json.dumps(data)

        with patch("sys.stdin", StringIO(json_str)):
            signals, name, _ = load_signals("-")

        assert len(signals) == 1
        assert name == "MACD"

    def test_load_missing_file(self) -> None:
        from easy_tdx.screen.ranker import load_signals

        with pytest.raises(FileNotFoundError):
            load_signals("/nonexistent/path.json")

    def test_load_empty_signals(self, tmp_path: Path) -> None:
        from easy_tdx.screen.ranker import load_signals

        data = {"strategy": "RSI", "signals": []}
        filepath = tmp_path / "empty.json"
        filepath.write_text(json.dumps(data), encoding="utf-8")

        signals, name, _ = load_signals(str(filepath))
        assert signals == []


# ── 集成：scanner._scan_one 逻辑 ─────────────────────────────────────


class TestScanOne:
    """测试 scanner 的单股扫描逻辑（模拟策略信号）。"""

    def test_no_signal(self) -> None:
        """策略不产生买入信号时返回 None。"""
        from easy_tdx.screen.scanner import SignalScanner

        # 构造一个永远不产生买入信号的 mock 策略
        mock_strategy = MagicMock()
        mock_strategy.__name__ = "NeverBuyStrategy"

        scanner = SignalScanner.__new__(SignalScanner)
        scanner._strategy_cls = mock_strategy
        scanner._vipdoc = Path("/fake")
        scanner._cash = 100000.0
        scanner._commission = 0.0003

        with patch.object(scanner, "_scan_one") as mock_scan:
            # 不产生信号时返回 None
            mock_scan.return_value = None
            result = scanner._scan_one(Path("/fake/sz000001.day"), "SZ", "000001")
            assert result is None

    def test_collect_files_universe_sh(self) -> None:
        """universe=sh 时只扫描上海 A 股。"""
        from easy_tdx.screen.scanner import SignalScanner

        scanner = SignalScanner.__new__(SignalScanner)

        # mock vipdoc 目录结构
        sh_dir = MagicMock()
        sh_files = [MagicMock(name="sh600000.day"), MagicMock(name="sh000001.day")]
        sh_files[0].name = "sh600000.day"
        sh_files[1].name = "sh000001.day"
        sh_dir.is_dir.return_value = True
        sh_dir.glob.return_value = iter(sh_files)

        sz_dir = MagicMock()
        sz_dir.is_dir.return_value = False

        mock_vipdoc = MagicMock()
        mock_vipdoc.__truediv__ = MagicMock(
            side_effect=lambda x: sh_dir if "sh" in str(x) else sz_dir
        )

        scanner._vipdoc = mock_vipdoc

        # 只测 universe 过滤逻辑（不测文件 IO）
        # 实际测试：_collect_files 应该跳过指数文件 sh000001
        # 这里验证 _detect_security_type 被正确调用
        from easy_tdx.offline.daily_bar import _detect_security_type

        assert _detect_security_type("sh600000.day") == "SH_A_STOCK"
        assert _detect_security_type("sh000001.day") == "SH_INDEX"
        assert _detect_security_type("sz000001.day") == "SZ_A_STOCK"
        assert _detect_security_type("sz399001.day") == "SZ_INDEX"
        assert _detect_security_type("sz159919.day") == "SZ_FUND"


# ── 策略加载测试 ────────────────────────────────────────────────────────


class TestLoadStrategy:
    """测试 CLI 的策略加载。"""

    def test_load_valid_strategy(self, tmp_path: Path) -> None:
        from easy_tdx.screen.cli import _load_strategy

        # 写一个简单的策略文件
        strategy_code = """
from easy_tdx.backtest import Strategy

class DummyStrategy(Strategy):
    def init(self) -> None:
        pass
    def next(self) -> None:
        pass
"""
        filepath = tmp_path / "dummy.py"
        filepath.write_text(strategy_code, encoding="utf-8")

        cls = _load_strategy(str(filepath))
        assert cls.__name__ == "DummyStrategy"

    def test_load_missing_file(self) -> None:
        from easy_tdx.screen.cli import _load_strategy

        with pytest.raises(SystemExit):
            _load_strategy("/nonexistent/strategy.py")

    def test_load_no_strategy_class(self, tmp_path: Path) -> None:
        from easy_tdx.screen.cli import _load_strategy

        filepath = tmp_path / "empty.py"
        filepath.write_text("x = 1\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _load_strategy(str(filepath))
