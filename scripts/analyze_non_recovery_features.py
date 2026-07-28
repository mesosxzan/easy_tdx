"""未恢复亏损单特征分析与过滤规则挖掘脚本。

合并亏损单10日恢复标签与买入日全量技术指标，对比"已恢复44笔"与
"未恢复14笔"的特征差异，挖掘能有效过滤未恢复亏损单的指标规则，
并在全量139笔交易上验证规则影响（误伤盈利单的风险）。

用法::

    python scripts/analyze_non_recovery_features.py

输入:
  - scripts/loss_recovery_analysis.csv  (58笔亏损单 + recovered标签)
  - scripts/factor_analysis_results.csv (139笔交易 + 52列全量指标)

输出:
  1. 特征区分度排名（已恢复 vs 未恢复）
  2. Top特征分布分析（寻找分离阈值）
  3. 候选过滤规则评估（precision/recall/误伤率）
  4. 最佳规则对全量交易的影响验证
  5. scripts/non_recovery_feature_analysis.csv (明细数据)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RECOVERY_CSV = PROJECT_ROOT / "scripts" / "loss_recovery_analysis.csv"
FACTOR_CSV = PROJECT_ROOT / "scripts" / "factor_analysis_results.csv"
OUTPUT_CSV = PROJECT_ROOT / "scripts" / "non_recovery_feature_analysis.csv"

# 数值型指标列（排除非指标列）
NUMERIC_FEATURES: list[str] = [
    # 策略已有指标
    "adx", "pdi", "mdi", "pdi_mdi_ratio", "atr_pct",
    "vs_ma5", "vs_ma10", "vs_ma20", "vs_ma60",
    "ma5_slope", "recent_gain3", "recent_gain5",
    "body_pct", "body_ratio", "upper_shadow_ratio", "lower_shadow_ratio",
    "vol_ratio", "reversal_ratio", "prev5_drop",
    # 新因子
    "rsi", "macd_diff", "macd_dea", "macd_hist", "macd_hist_prev", "macd_hist_slope",
    "kdj_k", "kdj_d", "kdj_j",
    "boll_pctb", "boll_width",
    "cci", "wr", "mfi",
    "bias6", "bias12", "bias24",
]

# 布尔型指标列
BOOL_FEATURES: list[str] = ["is_strong_trend", "is_bullish_align", "kdj_cross"]


def _load_and_merge() -> pd.DataFrame:
    """加载两份数据并合并，返回含 recovered 标签的全量指标 DataFrame。"""
    rec = pd.read_csv(RECOVERY_CSV, encoding="utf-8-sig", dtype={"stock": str})
    fac = pd.read_csv(FACTOR_CSV, encoding="utf-8-sig", dtype={"stock": str})

    # 统一日期格式
    rec["buy_date_norm"] = pd.to_datetime(rec["buy_date"]).dt.strftime("%Y-%m-%d")
    fac["buy_date_norm"] = pd.to_datetime(fac["buy_date"]).dt.strftime("%Y-%m-%d")

    rec["key"] = rec["stock"] + "_" + rec["buy_date_norm"]
    fac["key"] = fac["stock"] + "_" + fac["buy_date_norm"]

    # 合并：以亏损单为基础，左连接因子数据
    merged = rec.merge(fac, on="key", how="left", suffixes=("", "_fac"))

    # 清理重复列
    drop_cols = [c for c in merged.columns if c.endswith("_fac")]
    merged = merged.drop(columns=drop_cols)

    print(f"合并完成: {len(merged)} 笔亏损单, 指标非空: {merged['rsi'].notna().sum()}")
    return merged


def _discrimination_analysis(df: pd.DataFrame) -> list[tuple[str, float, float, float, float, str]]:
    """对比已恢复 vs 未恢复的各指标均值，返回按区分度排序的结果。

    返回: [(feature, recovered_mean, not_recovered_mean, diff, discriminant_score, direction)]
    """
    recovered = df[df["recovered"]]
    not_recovered = df[~df["recovered"]]

    print(f"\n{'=' * 90}")
    print(f"特征区分度分析（已恢复 {len(recovered)} 笔 vs 未恢复 {len(not_recovered)} 笔）")
    print(f"{'=' * 90}")
    print(
        f"{'特征':<22} {'已恢复均值':>12} {'未恢复均值':>12} "
        f"{'差异':>10} {'区分度':>8} {'方向':>6}"
    )
    print("-" * 75)

    results: list[tuple[str, float, float, float, float, str]] = []
    for col in NUMERIC_FEATURES:
        if col not in df.columns:
            continue
        rec_mean = float(recovered[col].mean())
        not_rec_mean = float(not_recovered[col].mean())
        diff = rec_mean - not_rec_mean
        std = float(df[col].std())
        discriminant = abs(diff) / std if std > 1e-9 else 0.0
        direction = "↑" if diff > 0 else "↓"
        results.append((col, rec_mean, not_rec_mean, diff, discriminant, direction))

    # 按区分度排序
    results.sort(key=lambda x: x[4], reverse=True)
    for col, rec_mean, not_rec_mean, diff, disc, direction in results:
        print(
            f"{col:<22} {rec_mean:>12.3f} {not_rec_mean:>12.3f} "
            f"{diff:>+10.3f} {disc:>8.3f} {direction:>6}"
        )

    return results


def _bool_feature_analysis(df: pd.DataFrame) -> None:
    """布尔型特征的恢复率对比。"""
    print(f"\n{'=' * 90}")
    print("布尔型特征恢复率对比")
    print(f"{'=' * 90}")

    for col in BOOL_FEATURES:
        if col not in df.columns:
            continue
        print(f"\n  {col}:")
        for val in [True, False]:
            subset = df[df[col] == val]
            if len(subset) == 0:
                continue
            rec_rate = subset["recovered"].mean() * 100
            print(
                f"    {col}={val}: {len(subset)}笔, "
                f"恢复率{rec_rate:.1f}%, "
                f"未恢复{len(subset) - int(subset['recovered'].sum())}笔"
            )


def _distribution_analysis(df: pd.DataFrame, top_features: list[str]) -> None:
    """对 Top 区分度特征做分布分析，寻找分离阈值。"""
    print(f"\n{'=' * 90}")
    print("Top 特征分布分析（已恢复 vs 未恢复）")
    print(f"{'=' * 90}")

    recovered = df[df["recovered"]]
    not_recovered = df[~df["recovered"]]

    for col in top_features[:8]:
        if col not in df.columns:
            continue
        print(f"\n--- {col} ---")
        print(
            f"  已恢复:   min={recovered[col].min():.3f}  "
            f"p25={recovered[col].quantile(0.25):.3f}  "
            f"median={recovered[col].median():.3f}  "
            f"p75={recovered[col].quantile(0.75):.3f}  "
            f"max={recovered[col].max():.3f}"
        )
        print(
            f"  未恢复:   min={not_recovered[col].min():.3f}  "
            f"p25={not_recovered[col].quantile(0.25):.3f}  "
            f"median={not_recovered[col].median():.3f}  "
            f"p75={not_recovered[col].quantile(0.75):.3f}  "
            f"max={not_recovered[col].max():.3f}"
        )

        # 分桶恢复率
        all_vals = df[col].dropna()
        if len(all_vals) == 0:
            continue
        lo, hi = all_vals.min(), all_vals.max()
        if hi - lo < 1e-9:
            continue
        bins = np.linspace(lo, hi, 7)
        print("  分桶恢复率:")
        print(f"    {'区间':<22} {'总数':>6} {'恢复':>6} {'未恢复':>6} {'恢复率':>8}")
        for i in range(len(bins) - 1):
            mask = (df[col] >= bins[i]) & (df[col] < bins[i + 1])
            if i == len(bins) - 2:
                mask = (df[col] >= bins[i]) & (df[col] <= bins[i + 1])
            subset = df[mask]
            if len(subset) == 0:
                continue
            rec_count = int(subset["recovered"].sum())
            not_rec_count = len(subset) - rec_count
            rec_rate = subset["recovered"].mean() * 100
            label = f"[{bins[i]:.2f}, {bins[i + 1]:.2f})"
            if i == len(bins) - 2:
                label = f"[{bins[i]:.2f}, {bins[i + 1]:.2f}]"
            print(
                f"    {label:<22} {len(subset):>6} {rec_count:>6} "
                f"{not_rec_count:>6} {rec_rate:>7.1f}%"
            )


def _evaluate_filter_rule(
    df: pd.DataFrame,
    rule_name: str,
    filter_mask: pd.Series,
    full_df: pd.DataFrame | None = None,
) -> dict[str, float | str]:
    """评估单个过滤规则的效果。

    filter_mask: True = 该交易应被过滤（不买入）
    返回: {rule, filtered_count, not_rec_caught, rec_killed, precision, recall, ...}
    """
    recovered = df["recovered"]
    not_recovered = ~df["recovered"]

    filtered = filter_mask

    # 在亏损单中的效果
    total_filtered = int(filtered.sum())
    not_rec_caught = int((filtered & not_recovered).sum())  # 未恢复中被过滤
    rec_killed = int((filtered & recovered).sum())  # 已恢复中被误杀

    # precision: 被过滤的交易中，有多少是未恢复的
    precision = not_rec_caught / total_filtered if total_filtered > 0 else 0.0
    # recall: 未恢复的交易中，有多少被过滤
    recall = not_rec_caught / int(not_recovered.sum()) if not_recovered.sum() > 0 else 0.0
    # 误伤率: 已恢复的交易中，有多少被误杀
    false_positive = rec_killed / int(recovered.sum()) if recovered.sum() > 0 else 0.0

    result: dict[str, float | str] = {
        "rule": rule_name,
        "filtered": total_filtered,
        "not_rec_caught": not_rec_caught,
        "rec_killed": rec_killed,
        "precision": precision,
        "recall": recall,
        "false_positive": false_positive,
    }

    # 在全量交易中的影响（如果提供了全量数据）
    if full_df is not None:
        # 需要在全量数据上重新计算 filter_mask
        # 这里只统计亏损单层面的效果，全量影响由调用方处理
        pass

    return result


def _test_candidate_rules(df: pd.DataFrame, full_df: pd.DataFrame) -> None:
    """测试候选过滤规则，评估效果。"""
    print(f"\n{'=' * 90}")
    print("候选过滤规则评估")
    print(f"{'=' * 90}")
    print(
        f"{'规则':<45} {'过滤':>6} {'捕获未恢复':>10} {'误杀已恢复':>10} "
        f"{'精度':>7} {'召回':>7} {'误伤率':>7}"
    )
    print("-" * 100)

    rules: list[tuple[str, pd.Series]] = []

    # ── 单因子规则 ──
    # 基于 distribution_analysis 的发现构建规则

    # RSI 规则: 未恢复 RSI 偏高
    for threshold in [60, 62, 64, 65, 66]:
        mask = df["rsi"] >= threshold
        rules.append((f"RSI >= {threshold}", mask))

    # CCI 规则: 未恢复 CCI 偏高（接近100但未触发）
    for threshold in [50, 60, 70, 80, 85, 90]:
        mask = df["cci"] >= threshold
        rules.append((f"CCI >= {threshold}", mask))

    # MFI 规则
    for threshold in [60, 65, 70, 72, 75]:
        mask = df["mfi"] >= threshold
        rules.append((f"MFI >= {threshold}", mask))

    # BOLL %b 规则
    for threshold in [0.75, 0.80, 0.85, 0.90]:
        mask = df["boll_pctb"] >= threshold
        rules.append((f"BOLL %b >= {threshold}", mask))

    # vs_ma20 规则: 偏离均线
    for threshold in [3, 4, 5, 6, 7]:
        mask = df["vs_ma20"] >= threshold
        rules.append((f"vs_ma20 >= {threshold}", mask))

    # KDJ-J 规则
    for threshold in [50, 55, 60, 70, 75, 80]:
        mask = df["kdj_j"] >= threshold
        rules.append((f"KDJ-J >= {threshold}", mask))

    # ADX 规则
    for threshold in [25, 30, 35, 40, 50]:
        mask = df["adx"] >= threshold
        rules.append((f"ADX >= {threshold}", mask))

    # WR 规则 (WR 越接近0越超买)
    for threshold in [-30, -25, -20, -15]:
        mask = df["wr"] >= threshold
        rules.append((f"WR >= {threshold}", mask))

    # vol_ratio 规则
    for threshold in [1.0, 1.2, 1.5, 1.8, 2.0]:
        mask = df["vol_ratio"] >= threshold
        rules.append((f"vol_ratio >= {threshold}", mask))

    # bias6 规则
    for threshold in [3, 4, 5, 6, 7]:
        mask = df["bias6"] >= threshold
        rules.append((f"bias6 >= {threshold}", mask))

    # macd_hist 规则
    for threshold in [0.2, 0.3, 0.4, 0.5]:
        mask = df["macd_hist"] >= threshold
        rules.append((f"MACD_hist >= {threshold}", mask))

    # ── 组合规则 ──
    # RSI + CCI 组合
    rules.append(("RSI>=62 & CCI>=50", (df["rsi"] >= 62) & (df["cci"] >= 50)))
    rules.append(("RSI>=62 & CCI>=70", (df["rsi"] >= 62) & (df["cci"] >= 70)))
    rules.append(("RSI>=60 & MFI>=65", (df["rsi"] >= 60) & (df["mfi"] >= 65)))
    rules.append(("RSI>=62 & MFI>=70", (df["rsi"] >= 62) & (df["mfi"] >= 70)))
    rules.append(("CCI>=70 & MFI>=65", (df["cci"] >= 70) & (df["mfi"] >= 65)))
    rules.append(("CCI>=50 & MFI>=70", (df["cci"] >= 50) & (df["mfi"] >= 70)))

    # BOLL %b + RSI
    rules.append(("BOLL%b>=0.8 & RSI>=60", (df["boll_pctb"] >= 0.8) & (df["rsi"] >= 60)))
    rules.append(("BOLL%b>=0.85 & RSI>=62", (df["boll_pctb"] >= 0.85) & (df["rsi"] >= 62)))

    # vs_ma20 + RSI (追高 + 超买)
    rules.append(("vs_ma20>=5 & RSI>=60", (df["vs_ma20"] >= 5) & (df["rsi"] >= 60)))
    rules.append(("vs_ma20>=4 & CCI>=50", (df["vs_ma20"] >= 4) & (df["cci"] >= 50)))

    # KDJ-J + RSI (双超买)
    rules.append(("KDJ-J>=70 & RSI>=60", (df["kdj_j"] >= 70) & (df["rsi"] >= 60)))
    rules.append(("KDJ-J>=60 & CCI>=50", (df["kdj_j"] >= 60) & (df["cci"] >= 50)))

    # 三因子组合
    rules.append((
        "RSI>=60 & CCI>=50 & MFI>=65",
        (df["rsi"] >= 60) & (df["cci"] >= 50) & (df["mfi"] >= 65),
    ))
    rules.append((
        "RSI>=62 & BOLL%b>=0.8 & MFI>=65",
        (df["rsi"] >= 62) & (df["boll_pctb"] >= 0.8) & (df["mfi"] >= 65),
    ))
    rules.append((
        "CCI>=50 & MFI>=65 & vs_ma20>=4",
        (df["cci"] >= 50) & (df["mfi"] >= 65) & (df["vs_ma20"] >= 4),
    ))

    # 评估所有规则
    best_rules: list[dict[str, float | str]] = []
    for rule_name, mask in rules:
        result = _evaluate_filter_rule(df, rule_name, mask)
        best_rules.append(result)
        print(
            f"{rule_name:<45} {result['filtered']:>6} {result['not_rec_caught']:>10} "
            f"{result['rec_killed']:>10} {result['precision']:>6.1%} "
            f"{result['recall']:>6.1%} {result['false_positive']:>6.1%}"
        )

    # 筛选最优规则：recall >= 50% 且 precision >= 50% 且 false_positive <= 30%
    print(f"\n{'=' * 90}")
    print("最优规则筛选（recall>=50% & precision>=50% & 误伤率<=30%）")
    print(f"{'=' * 90}")

    good_rules = [
        r for r in best_rules
        if r["recall"] >= 0.5 and r["precision"] >= 0.5 and r["false_positive"] <= 0.3
    ]
    good_rules.sort(key=lambda x: (x["recall"], x["precision"], -x["false_positive"]), reverse=True)

    if not good_rules:
        print("  未找到满足条件的规则，放宽标准（recall>=40% & precision>=40%）")
        good_rules = [
            r for r in best_rules
            if r["recall"] >= 0.4 and r["precision"] >= 0.4
        ]
        good_rules.sort(key=lambda x: (x["recall"], x["precision"]), reverse=True)

    for r in good_rules[:10]:
        print(
            f"  {r['rule']:<45} 过滤{r['filtered']}笔, "
            f"捕获未恢复{r['not_rec_caught']}/14, "
            f"误杀已恢复{r['rec_killed']}/44, "
            f"精度{r['precision']:.1%} 召回{r['recall']:.1%} 误伤{r['false_positive']:.1%}"
        )

    # 对最优规则验证全量影响
    if good_rules:
        print(f"\n{'=' * 90}")
        print("最优规则对全量139笔交易的影响验证")
        print(f"{'=' * 90}")
        _validate_on_full_trades(df, full_df, good_rules[:5])


def _validate_on_full_trades(
    loss_df: pd.DataFrame,
    full_df: pd.DataFrame,
    rules: list[dict[str, float | str]],
) -> None:
    """验证最优规则对全量交易的影响。

    将亏损单上发现的规则应用到全量139笔交易，看会误伤多少盈利单。
    """
    # 全量数据中，亏损单的 key
    loss_keys = set(loss_df["key"])
    full_df["is_loss"] = full_df["key"].isin(loss_keys)

    # 基准绩效
    total = len(full_df)
    wins = full_df[full_df["is_win"]]
    losses = full_df[~full_df["is_win"]]
    base_win_rate = len(wins) / total * 100
    base_pf = (
        wins["profit_pct"].sum() / abs(losses["profit_pct"].sum())
        if len(losses) > 0 else float("inf")
    )

    print(f"\n  基准: {total}笔, 胜率{base_win_rate:.1f}%, PF {base_pf:.2f}")
    print(
        f"  {'规则':<45} {'剩余':>6} {'过滤盈利':>8} {'过滤亏损':>8} "
        f"{'新胜率':>8} {'新PF':>8}"
    )
    print("  " + "-" * 90)

    for r in rules:
        rule_name = str(r["rule"])
        # 解析规则名并构建全量 mask
        mask = _parse_and_apply_rule(full_df, rule_name)
        if mask is None:
            continue

        remaining = full_df[~mask]
        rem_total = len(remaining)
        filtered_wins = int((mask & full_df["is_win"]).sum())
        filtered_losses = int((mask & ~full_df["is_win"]).sum())

        rem_wins = remaining[remaining["is_win"]]
        rem_losses = remaining[~remaining["is_win"]]
        new_win_rate = len(rem_wins) / rem_total * 100 if rem_total > 0 else 0
        new_pf = (
            rem_wins["profit_pct"].sum() / abs(rem_losses["profit_pct"].sum())
            if len(rem_losses) > 0 and rem_losses["profit_pct"].sum() != 0
            else float("inf")
        )

        print(
            f"  {rule_name:<45} {rem_total:>6} {filtered_wins:>8} {filtered_losses:>8} "
            f"{new_win_rate:>7.1f}% {new_pf:>8.2f}"
        )


def _parse_and_apply_rule(df: pd.DataFrame, rule_name: str) -> pd.Series | None:
    """解析规则名字符串，在 DataFrame 上构建过滤 mask。

    支持简单的 "col>=val & col>=val" 格式，列名大小写不敏感。
    """
    # 构建小写列名映射，支持别名
    col_map: dict[str, str] = {c.lower(): c for c in df.columns}
    col_map["boll%b"] = "boll_pctb" if "boll_pctb" in df.columns else ""

    mask = pd.Series([True] * len(df), index=df.index)
    parts = rule_name.split(" & ")
    for part in parts:
        part = part.strip()
        matched = False
        for op in [">=", "<=", ">", "<"]:
            if op in part:
                col_str, val_str = part.split(op, 1)
                col = col_str.strip()
                try:
                    val = float(val_str.strip())
                except ValueError:
                    return None
                # 大小写不敏感匹配列名
                actual_col = col_map.get(col.lower(), "")
                if not actual_col or actual_col not in df.columns:
                    return None
                if op == ">=":
                    mask &= df[actual_col] >= val
                elif op == "<=":
                    mask &= df[actual_col] <= val
                elif op == ">":
                    mask &= df[actual_col] > val
                elif op == "<":
                    mask &= df[actual_col] < val
                matched = True
                break
        if not matched:
            return None
    return mask


def _print_not_recovered_detail(df: pd.DataFrame) -> None:
    """打印未恢复14笔的详细指标。"""
    not_rec = df[~df["recovered"]].sort_values("loss_pct")

    print(f"\n{'=' * 90}")
    print("未恢复14笔交易关键指标明细")
    print(f"{'=' * 90}")
    print(
        f"{'股票':<8} {'买入日':>12} {'亏损%':>8} {'RSI':>6} {'CCI':>7} {'MFI':>6} "
        f"{'BOLL%b':>7} {'KDJ-J':>7} {'ADX':>6} {'vsMA20':>7} {'volR':>6} {'bias6':>6}"
    )
    print("-" * 100)
    for _, r in not_rec.iterrows():
        print(
            f"{r['stock']:<8} {r['buy_date_norm']:>12} {r['loss_pct']:>+7.2f}% "
            f"{r['rsi']:>6.1f} {r['cci']:>7.1f} {r['mfi']:>6.1f} "
            f"{r['boll_pctb']:>7.3f} {r['kdj_j']:>7.1f} {r['adx']:>6.1f} "
            f"{r['vs_ma20']:>7.2f} {r['vol_ratio']:>6.2f} {r['bias6']:>6.2f}"
        )


def main() -> None:
    print("=" * 90)
    print("未恢复亏损单特征分析与过滤规则挖掘")
    print("=" * 90)

    # 1. 加载并合并数据
    df = _load_and_merge()
    if df.empty:
        print("[!] 无数据")
        return

    # 2. 未恢复14笔明细
    _print_not_recovered_detail(df)

    # 3. 特征区分度分析
    results = _discrimination_analysis(df)
    top_feature_names = [r[0] for r in results[:10]]

    # 4. 布尔特征分析
    _bool_feature_analysis(df)

    # 5. 分布分析
    _distribution_analysis(df, top_feature_names)

    # 6. 加载全量数据用于验证
    full_df = pd.read_csv(FACTOR_CSV, encoding="utf-8-sig", dtype={"stock": str})
    full_df["buy_date_norm"] = pd.to_datetime(full_df["buy_date"]).dt.strftime("%Y-%m-%d")
    full_df["key"] = full_df["stock"] + "_" + full_df["buy_date_norm"]

    # 7. 候选规则评估
    _test_candidate_rules(df, full_df)

    # 8. 保存明细
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n明细数据已保存至: {OUTPUT_CSV}")

    print(f"\n{'=' * 90}")
    print("分析完成")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
