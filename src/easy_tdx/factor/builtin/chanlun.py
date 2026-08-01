"""缠论因子 — 桥接 ChanlunAnalyser。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from easy_tdx.factor.base import Factor, register_factor


def _ensure_datetime_col(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime" in df.columns or "date" in df.columns:
        return df
    out = df.copy()
    if isinstance(out.index, pd.DatetimeIndex):
        out["datetime"] = out.index
        return out
    dt = pd.to_datetime(out.index, errors="coerce")
    if isinstance(dt, pd.DatetimeIndex) and len(dt.dropna()) > 0:
        out["datetime"] = dt
        return out
    out["datetime"] = pd.date_range("2000-01-01", periods=len(out), freq="D")
    return out


def _ensure_vol_col(df: pd.DataFrame) -> pd.DataFrame:
    if "vol" in df.columns:
        return df
    if "amount" not in df.columns:
        return df
    out = df.copy()
    out["vol"] = out["amount"]
    return out


@register_factor
class ChanlunBiDir(Factor):
    name = "chanlun_bi_dir"
    category = "chanlun"
    description = "当前笔方向（+1=向上笔，-1=向下笔，0=无笔）"
    inputs = ("open", "high", "low", "close", "vol", "amount")

    def compute(self, df: pd.DataFrame) -> pd.Series:
        result = pd.Series(0.0, index=df.index, dtype=np.float64)

        try:
            from easy_tdx.chanlun.incremental import IncrementalAnalyser
            from easy_tdx.chanlun.types import Direction

            df2 = _ensure_vol_col(_ensure_datetime_col(df))
            analyser = IncrementalAnalyser(frequency="DAILY")
            chanlun_result = analyser.process_klines(df2)
            bis = chanlun_result.bis

            if not bis:
                return result

            for bi in bis:
                direction = 1.0 if bi.direction == Direction.UP else -1.0
                start = bi.start.k.k_index
                end = bi.end.k.k_index
                lo = max(0, min(start, end))
                hi = min(len(df), max(start, end) + 1)
                result.iloc[lo:hi] = np.float64(direction)

            last_bi = bis[-1]
            direction = 1.0 if last_bi.direction == Direction.UP else -1.0
            result.iloc[-1] = np.float64(direction)

        except Exception:
            pass

        return result


@register_factor
class ChanlunMMD(Factor):
    name = "chanlun_mmd"
    category = "chanlun"
    description = "最近买卖点类型编码（正=买点，负=卖点，0=无信号）"
    inputs = ("open", "high", "low", "close", "vol", "amount")

    _MMD_MAP: dict[str, float] = {
        "1buy": 1.0,
        "2buy": 2.0,
        "3buy": 3.0,
        "l3buy": 3.0,
        "1sell": -1.0,
        "2sell": -2.0,
        "3sell": -3.0,
        "s3sell": -3.0,
    }

    def compute(self, df: pd.DataFrame) -> pd.Series:
        result = pd.Series(0.0, index=df.index, dtype=np.float64)

        try:
            from easy_tdx.chanlun.incremental import IncrementalAnalyser

            df2 = _ensure_vol_col(_ensure_datetime_col(df))
            analyser = IncrementalAnalyser(frequency="DAILY")
            chanlun_result = analyser.process_klines(df2)

            if chanlun_result.signals_by_bar:
                for bar_idx, signals in chanlun_result.signals_by_bar.items():
                    if not signals:
                        continue
                    mmd = signals[-1]
                    value = self._MMD_MAP.get(mmd.mmd_type.value, 0.0)
                    if 0 <= bar_idx < len(df):
                        result.iloc[bar_idx] = np.float64(value)
                return result

            mmds = chanlun_result.mmds
            for mmd in mmds:
                if mmd.bi is None:
                    continue
                bar_idx = mmd.bi.end.k.k_index
                value = self._MMD_MAP.get(mmd.mmd_type.value, 0.0)
                if 0 <= bar_idx < len(df):
                    result.iloc[bar_idx] = np.float64(value)

        except Exception:
            pass

        return result
