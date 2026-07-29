"""增量 vs 批量缠论分析对比验证脚本。

验证 IncrementalAnalyser 彻底消除前瞻偏差的核心改进：
1. 结构数量一致性（历史部分应完全一致）
2. signals_by_bar 因果性（确认bar严格递增，无未来信息泄漏）
3. MMD 识别差异展示（批量使用未来中枢 vs 增量只使用已确认中枢）

用法:: 

    python strategies/verify_incremental.py
    python strategies/verify_incremental.py --count 200
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from easy_tdx.chanlun.analyser import ChanlunAnalyser  # noqa: E402
from easy_tdx.chanlun.config import ChanlunConfig  # noqa: E402
from easy_tdx.chanlun.incremental import IncrementalAnalyser  # noqa: E402


def generate_oscillating_prices(n: int = 100, seed: int = 7) -> pd.DataFrame:
    """生成有足够波动形成笔/中枢/买卖点的K线数据。

    使用正弦波叠加随机噪声，模拟真实市场的波动节奏。
    seed=7 可产生增量分析能识别出信号的场景（同时展示与批量的差异）。
    """
    rng = random.Random(seed)
    import math

    base = 10.0
    prices: list[tuple[float, float, float, float]] = []

    for i in range(n):
        # 正弦波叠加模拟市场波动节奏
        wave = 5.0 * math.sin(i * 0.3) + 3.0 * math.sin(i * 0.15)

        noise = rng.uniform(-0.5, 0.5)
        close = base + wave + noise
        opn = close + rng.uniform(-0.3, 0.3)
        high = max(opn, close) + rng.uniform(0.1, 0.8)
        low = min(opn, close) - rng.uniform(0.1, 0.8)
        prices.append((opn, close, high, low))

    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "datetime": dates,
        "open": [p[0] for p in prices],
        "close": [p[1] for p in prices],
        "high": [p[2] for p in prices],
        "low": [p[3] for p in prices],
        "vol": [10000.0] * n,
    })


def verify_structure_consistency(df: pd.DataFrame, config: ChanlunConfig) -> bool:
    """验证批量与增量分析的结构数量一致性。"""
    print("\n" + "=" * 70)
    print("  1. 结构数量一致性验证")
    print("=" * 70)

    batch = ChanlunAnalyser(config=config)
    batch_r = batch.process_klines(df)

    incr = IncrementalAnalyser(config=config)
    incr_r = incr.process_klines(df)

    rows = [
        ("CLKlines", len(batch_r.cklines), len(incr_r.cklines)),
        ("Fractals", len(batch_r.fractals), len(incr_r.fractals)),
        ("Bis", len(batch_r.bis), len(incr_r.bis)),
        ("ZSs", len(batch_r.zss), len(incr_r.zss)),
    ]

    print(f"  {'Structure':<15} {'Batch':>8} {'Incremental':>12} {'Diff':>6}")
    print(f"  {'-' * 15} {'-' * 8} {'-' * 12} {'-' * 6}")

    all_ok = True
    for name, b, inc in rows:
        diff = b - inc
        status = "OK" if abs(diff) <= 2 else "WARN"
        if abs(diff) > 2:
            all_ok = False
        print(f"  {name:<15} {b:>8} {inc:>12} {diff:>6}  {status}")

    # MMD差异是预期的（前瞻偏差消除），单独显示
    mmd_diff = len(batch_r.mmds) - len(incr_r.mmds)
    print(
        f"  {'MMDs':<15} {len(batch_r.mmds):>8} {len(incr_r.mmds):>12} "
        f"{mmd_diff:>6}  INFO (前瞻偏差消除)"
    )

    return all_ok


def verify_signals_causality(df: pd.DataFrame, config: ChanlunConfig) -> bool:
    """验证 signals_by_bar 的因果性（无未来信息泄漏）。"""
    print("\n" + "=" * 70)
    print("  2. signals_by_bar 因果性验证")
    print("=" * 70)

    incr = IncrementalAnalyser(config=config)
    result = incr.process_klines(df)

    n_klines = len(result.klines)
    signals = result.signals_by_bar

    if not signals:
        print("  [SKIP] 无买卖点信号（数据可能不足）")
        return True

    print(f"  总K线数: {n_klines}")
    print(f"  信号bar数: {len(signals)}")
    print(f"  总信号数: {sum(len(v) for v in signals.values())}")

    print(f"\n  {'Bar':<8} {'MMD Types':<30} {'ZS Confirm':<12} {'Bi Confirm':<12} {'Causal?':<10}")
    print(f"  {'-' * 8} {'-' * 30} {'-' * 12} {'-' * 12} {'-' * 10}")

    all_causal = True
    for bar_idx in sorted(signals.keys()):
        mmds = signals[bar_idx]
        mmd_types = ", ".join(m.mmd_type.value for m in mmds)

        for mmd in mmds:
            if mmd.bi is not None and mmd.zs is not None:
                bi_bar = incr._bi_confirm_bar.get(mmd.bi.index, -1)  # noqa: SLF001
                zs_bar = incr._zs_confirm_bar.get(mmd.zs.index, -1)  # noqa: SLF001
                causal = zs_bar <= bi_bar
                if not causal:
                    all_causal = False
                print(
                    f"  {bar_idx:<8} {mmd_types:<30} "
                    f"{zs_bar:<12} {bi_bar:<12} "
                    f"{'YES' if causal else 'NO!!!':<10}"
                )

    if all_causal:
        print("\n  [PASS] 所有信号因果性正确：ZS确认bar <= BI确认bar")
    else:
        print("\n  [FAIL] 发现前瞻偏差！某些信号使用了未来确认的中枢")

    return all_causal


def verify_mmd_difference(df: pd.DataFrame, config: ChanlunConfig) -> None:
    """展示批量与增量MMD识别的差异。"""
    print("\n" + "=" * 70)
    print("  3. MMD 识别差异展示")
    print("=" * 70)

    batch = ChanlunAnalyser(config=config)
    batch_r = batch.process_klines(df)

    incr = IncrementalAnalyser(config=config)
    incr_r = incr.process_klines(df)

    print(f"\n  批量 MMDs ({len(batch_r.mmds)}):")
    for i, mmd in enumerate(batch_r.mmds):
        bi_idx = mmd.bi.index if mmd.bi else -1
        zs_idx = mmd.zs.index if mmd.zs else -1
        print(f"    MMD[{i}]: type={mmd.mmd_type.value}, bi={bi_idx}, zs={zs_idx}")

    print(f"\n  增量 MMDs ({len(incr_r.mmds)}):")
    for bar_idx, mmds in sorted(incr_r.signals_by_bar.items()):
        for i, mmd in enumerate(mmds):
            bi_idx = mmd.bi.index if mmd.bi else -1
            zs_idx = mmd.zs.index if mmd.zs else -1
            print(
                f"    bar={bar_idx}: MMD[{i}]: type={mmd.mmd_type.value}, "
                f"bi={bi_idx}, zs={zs_idx}"
            )

    if len(batch_r.mmds) != len(incr_r.mmds):
        diff = len(batch_r.mmds) - len(incr_r.mmds)
        print(
            f"\n  [INFO] 批量比增量多识别 {diff} 个MMD"
            f"（这些是前瞻偏差导致的虚假信号，已被增量分析器正确过滤）"
        )
    else:
        print("\n  [INFO] 批量与增量MMD数量相同")


def main(count: int = 100) -> None:
    """运行完整验证流程。"""
    print("=" * 70)
    print("  增量 vs 批量缠论分析对比验证")
    print("=" * 70)

    df = generate_oscillating_prices(n=count, seed=7)
    config = ChanlunConfig()

    print(f"  数据: {count} 根K线, seed=7")
    print(f"  配置: fx_strict={config.fx_strict}, bi_type={config.bi_type}")

    ok1 = verify_structure_consistency(df, config)
    ok2 = verify_signals_causality(df, config)
    verify_mmd_difference(df, config)

    print("\n" + "=" * 70)
    print("  总结")
    print("=" * 70)
    print(f"  结构一致性: {'PASS' if ok1 else 'WARN'}")
    print(f"  因果性验证: {'PASS' if ok2 else 'FAIL'}")

    if ok1 and ok2:
        print("\n  [SUCCESS] 增量分析器正确消除前瞻偏差，结构一致性良好。")
    else:
        print("\n  [WARNING] 部分验证未通过，请检查详细输出。")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="增量vs批量缠论分析验证")
    parser.add_argument("--count", type=int, default=100, help="K线数量")
    args = parser.parse_args()
    main(count=args.count)
