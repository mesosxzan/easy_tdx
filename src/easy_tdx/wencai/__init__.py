"""同花顺问财语义查询 -- 独立于 TDX 协议的数据源。

直接请求问财 ``get-stock-pick`` 接口，要求有效的登录 Cookie。
"""

from __future__ import annotations

from .client import WencaiClient
from .models import WencaiError, WencaiStock

__all__ = [
    "WencaiClient",
    "WencaiError",
    "WencaiStock",
]
