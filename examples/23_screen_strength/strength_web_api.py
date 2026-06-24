"""强势股排名 — Web API 调用示例。

演示如何通过 HTTP 调用 easy-tdx 的 REST API 获取强势股排名。

前提：
    1. 启动 Web API 服务：easy-tdx serve --port 8000
    2. 本地 vipdoc 数据已同步（扫描依赖本地 .day 文件）
    3. pip install requests

运行方式：
    python examples/23_screen_strength/strength_web_api.py
"""

from __future__ import annotations

import requests

BASE_URL = "http://localhost:8000/api/v1"


def fetch_strength(
    preset: str = "steady",
    top_n: int = 20,
    universe: str = "all",
    min_amount: float = 0.0,
) -> dict:
    """调用 GET /market/strength 获取强势股排名。

    Args:
        preset: 预设模式 steady / breakout / balanced
        top_n: 返回前 N 名
        universe: 范围 all / sh / sz
        min_amount: 日均成交额下限（元）

    Returns:
        {"data": [...], "count": N}
    """
    resp = requests.get(
        f"{BASE_URL}/market/strength",
        params={
            "preset": preset,
            "top_n": top_n,
            "universe": universe,
            "min_amount": min_amount,
        },
        timeout=120,  # 扫描全市场可能需要 30-60 秒
    )
    resp.raise_for_status()
    return resp.json()


def fetch_strength_custom_weights(
    w5: float = 0.5,
    w20: float = 0.3,
    w60: float = 0.2,
    top_n: int = 30,
) -> dict:
    """自定义权重调用（覆盖预设）。"""
    resp = requests.get(
        f"{BASE_URL}/market/strength",
        params={
            "w5": w5, "w20": w20, "w60": w60,
            "top_n": top_n,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def print_ranking(result: dict, title: str) -> None:
    """格式化打印排名结果。"""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")
    data = result.get("data", [])
    if not data:
        print("  无数据")
        return

    print(f"  {'排名':>4}  {'代码':<10} {'现价':>10} "
          f"{'5日':>8} {'20日':>8} {'60日':>8} {'强势分':>8}")
    print(f"  {'-' * 66}")
    for row in data:
        print(f"  {row['rank']:>4}  {row['market']}{row['code']:<9} "
              f"{row['last_close']:>9.2f} "
              f"{row['ret_5']:>7.2%} {row['ret_20']:>7.2%} "
              f"{row['ret_60']:>7.2%} {row['strength']:>8.2f}")


def main() -> None:
    # ── 1. steady 模式：中长期稳健 Top 20 ───────────────────────────────
    result = fetch_strength(preset="steady", top_n=20)
    print_ranking(result, "[steady] 中长期稳健强势 Top 20")

    # ── 2. breakout 模式：近期妖股 Top 10 ───────────────────────────────
    result = fetch_strength(preset="breakout", top_n=10)
    print_ranking(result, "[breakout] 近期妖股爆发 Top 10")

    # ── 3. 自定义权重 + 成交额过滤 ──────────────────────────────────────
    result = fetch_strength_custom_weights(w5=0.5, w20=0.3, w60=0.2, top_n=15)
    print_ranking(result, "[自定义 5:3:2] Top 15")

    # ── 4. 过滤低流动性（日均成交额 ≥ 5000 万）─────────────────────────
    result = fetch_strength(
        preset="breakout", top_n=20, min_amount=50_000_000
    )
    print_ranking(result, "[breakout + 流动性过滤] Top 20")


if __name__ == "__main__":
    main()
