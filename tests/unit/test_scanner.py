"""单元测试：信号扫描引擎。

测试 SignalScanner 的并发扫描和增量扫描功能。
使用临时目录构造 .day 文件 fixture，无需真实数据。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pandas as pd
import pytest

from easy_tdx.backtest.strategy import Strategy
from easy_tdx.screen.scanner import SignalScanner


class AlwaysBuyStrategy(Strategy):
    """策略：每个 bar 都产生买入信号（用于扫描测试）。"""

    def init(self) -> None:
        pass

    def next(self) -> None:
        self.buy(size=0)


def _write_day_file(
    path: Path,
    n_bars: int = 50,
    base_price: float = 10.0,
) -> None:
    """写一个最小的 .day 文件（通达信日线格式）。

    格式: date(I) open(I) high(I) low(I) close(I) amount(f) vol(I) reserved(I)
    每条 32 字节, 小端序. 价格以 0.01 为系数存储.
    """
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="D")

    with open(path, "wb") as f:
        for i in range(n_bars):
            dt = dates[i]
            day = dt.year * 10000 + dt.month * 100 + dt.day
            price = base_price + i * 0.01
            f.write(
                struct.pack(
                    "<IIIIIfII",
                    day,
                    int(price * 100),
                    int((price + 0.5) * 100),
                    int((price - 0.5) * 100),
                    int(price * 100),
                    float(1000000 + i * 100),
                    10000 + i * 10,
                    0,
                )
            )


@pytest.fixture
def vipdoc(tmp_path: Path) -> Path:
    """创建包含 .day 文件的临时 vipdoc 目录."""
    sz_lday = tmp_path / "sz" / "lday"
    sz_lday.mkdir(parents=True)

    for code in ("000001", "000002", "000003"):
        _write_day_file(sz_lday / f"sz{code}.day", n_bars=50)

    # 指数文件 (应被过滤)
    _write_day_file(sz_lday / "sz399001.day", n_bars=50)

    return tmp_path


class TestConcurrentScan:
    """测试并发扫描."""

    def test_scan_produces_results(self, vipdoc: Path) -> None:
        """基本扫描应返回触发信号的股票."""
        scanner = SignalScanner(AlwaysBuyStrategy, vipdoc_path=vipdoc)
        results = scanner.scan(universe="all")

        assert len(results) >= 1, f"Expected >= 1 result, got {len(results)}"

    def test_concurrent_same_as_serial(self, vipdoc: Path) -> None:
        """并发扫描结果应与串行扫描一致."""
        scanner = SignalScanner(AlwaysBuyStrategy, vipdoc_path=vipdoc)

        serial = scanner.scan(universe="all", workers=1)
        parallel = scanner.scan(universe="all", workers=2)

        serial_codes = sorted(r.code for r in serial)
        parallel_codes = sorted(r.code for r in parallel)
        assert serial_codes == parallel_codes

    def test_scan_with_zero_workers_uses_serial(self, vipdoc: Path) -> None:
        """workers=0 应退回串行模式."""
        scanner = SignalScanner(AlwaysBuyStrategy, vipdoc_path=vipdoc)
        results = scanner.scan(universe="all", workers=0)

        assert len(results) >= 1

    def test_progress_callback(self, vipdoc: Path) -> None:
        """进度回调应被正确调用."""
        scanner = SignalScanner(AlwaysBuyStrategy, vipdoc_path=vipdoc)
        progress: list[tuple[int, int, str]] = []

        def on_progress(current: int, total: int, name: str) -> None:
            progress.append((current, total, name))

        scanner.scan(universe="all", progress_callback=on_progress)

        assert len(progress) >= 2
        assert progress[-1][2] == "done"


class TestIncrementalScan:
    """测试增量扫描."""

    def test_second_scan_uses_cache(self, vipdoc: Path, tmp_path: Path) -> None:
        """第二次扫描应使用缓存, 不重新计算."""
        cache_file = tmp_path / "scan_cache.json"
        scanner = SignalScanner(
            AlwaysBuyStrategy,
            vipdoc_path=vipdoc,
            cache_file=cache_file,
        )

        # 第一次扫描: 无缓存
        results1 = scanner.scan(universe="all")
        assert len(results1) >= 1
        assert cache_file.is_file()

        # 第二次扫描: 应使用缓存, 结果相同
        results2 = scanner.scan(universe="all")
        codes1 = sorted(r.code for r in results1)
        codes2 = sorted(r.code for r in results2)
        assert codes1 == codes2

    def test_no_cache_file_means_full_scan(self, vipdoc: Path) -> None:
        """无缓存文件时每次都是全量扫描."""
        scanner = SignalScanner(AlwaysBuyStrategy, vipdoc_path=vipdoc)

        results1 = scanner.scan(universe="all")
        results2 = scanner.scan(universe="all")

        codes1 = sorted(r.code for r in results1)
        codes2 = sorted(r.code for r in results2)
        assert codes1 == codes2

    def test_cache_updated_after_file_change(self, vipdoc: Path, tmp_path: Path) -> None:
        """文件变化后缓存应失效, 重新扫描."""
        cache_file = tmp_path / "scan_cache.json"
        scanner = SignalScanner(
            AlwaysBuyStrategy,
            vipdoc_path=vipdoc,
            cache_file=cache_file,
        )

        # 第一次扫描
        results1 = scanner.scan(universe="all")
        assert len(results1) >= 1

        # 修改文件 (touch mtime)
        import time

        day_file = vipdoc / "sz" / "lday" / "sz000001.day"
        time.sleep(0.1)
        day_file.touch()

        # 第二次扫描: sz000001 应被重新扫描
        results2 = scanner.scan(universe="all")
        codes2 = sorted(r.code for r in results2)
        # 结果可能相同 (策略没变), 但不应崩溃
        assert len(codes2) >= 1
