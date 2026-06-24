"""强势股排名 — Python API 示例。

本示例演示如何用 StrengthRanker 扫描全市场，按 5/20/60 日涨幅加权选出强势股。

运行前提：
    1. 本地安装通达信，且 vipdoc/{sh,sz}/lday/*.day 数据已同步（含最新交易日）。
    2. 可通过 easy-tdx offline sync 命令同步数据。
    3. pip install easy-tdx

运行方式：
    python examples/23_screen_strength/strength_api.py
"""

from __future__ import annotations

from easy_tdx.screen.strength import STRENGTH_PRESETS, StrengthRanker


def main() -> None:
    # ── 1. 查看所有预设模式 ──────────────────────────────────────────────
    print("=" * 60)
    print("可用预设模式：")
    print("=" * 60)
    for name, cfg in STRENGTH_PRESETS.items():
        print(f"  {name:10} w5={cfg['w5']:.2f} w20={cfg['w20']:.2f} "
              f"w60={cfg['w60']:.2f} vol_adjusted={cfg['vol_adjusted']}")
        print(f"             {cfg['desc']}")
    print()

    # ── 2. steady 模式：中长期稳健强势 Top 20 ────────────────────────────
    print("=" * 60)
    print("[steady] 中长期稳健强势 Top 20")
    print("=" * 60)
    ranker = StrengthRanker(preset="steady")

    # 进度回调（扫描 ~5000 只约 30-60 秒）
    def on_progress(current: int, total: int, name: str) -> None:
        if name == "done":
            print(f"\r扫描完成: {total} 只")
        else:
            pct = current * 100 // total if total > 0 else 0
            print(f"\r[{current}/{total}] {pct}% scanning {name}", end="")

    results = ranker.rank(top_n=20, progress_callback=on_progress)
    data_date = results[0].last_date if results else 0
    print()
    print(ranker.to_table(results, "steady", data_date))
    print()

    # ── 3. breakout 模式：近期妖股爆发 Top 10 ───────────────────────────
    print("=" * 60)
    print("[breakout] 近期妖股爆发 Top 10")
    print("=" * 60)
    breakout_ranker = StrengthRanker(preset="breakout")
    results = breakout_ranker.rank(top_n=10)
    data_date = results[0].last_date if results else 0
    print(breakout_ranker.to_table(results, "breakout", data_date))
    print()

    # ── 4. 自定义权重 + 成交额过滤 ──────────────────────────────────────
    print("=" * 60)
    print("[自定义] 5:3:2 权重 + 日均成交额 ≥ 5000 万")
    print("=" * 60)
    custom_ranker = StrengthRanker(
        w5=0.5, w20=0.3, w60=0.2,
        vol_adjusted=False,           # 纯加权涨幅
        min_amount=50_000_000,        # 最近 5 日日均成交额 ≥ 5000 万
    )
    results = custom_ranker.rank(top_n=15)
    data_date = results[0].last_date if results else 0
    print(custom_ranker.to_table(results, "custom", data_date))
    print()

    # ── 5. 并发扫描 + JSON 输出到文件 ───────────────────────────────────
    print("=" * 60)
    print("[并发] balanced 模式 + 4 进程 + 输出 JSON")
    print("=" * 60)
    parallel_ranker = StrengthRanker(preset="balanced")
    results = parallel_ranker.rank(
        top_n=50,
        workers=4,                    # 4 进程并发，速度提升约 4 倍
        progress_callback=on_progress,
    )
    data_date = results[0].last_date if results else 0
    json_str = parallel_ranker.to_json(results, "balanced", data_date)

    output_file = "strength_balanced.json"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(json_str)
    print(f"\n排名: {len(results)} 只 → {output_file}")

    # ── 6. 编程式访问排名数据 ───────────────────────────────────────────
    print()
    print("=" * 60)
    print("[编程式访问] 遍历前 5 名")
    print("=" * 60)
    for r in results[:5]:
        print(f"  #{r.rank} {r.market}{r.code}  现价={r.last_close:.2f}  "
              f"5日={r.ret_5:+.2%}  20日={r.ret_20:+.2%}  60日={r.ret_60:+.2%}  "
              f"强势分={r.strength:.2f}")


if __name__ == "__main__":
    main()
