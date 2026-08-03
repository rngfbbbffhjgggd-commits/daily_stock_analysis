# -*- coding: utf-8 -*-
"""同花顺 iFind 数据源。

通过同花顺 iFind 远程 MCP 代理（api-mcp.51ifind.com）获取数据。
当前实现聚焦 ETF/基金实时行情快照，作为实时行情的高优先级兜底源。

iFind 走专用通道，从海外（GitHub Actions）访问比东财/新浪稳定得多，
可缓解行情接口 502 / 卡死问题。

配置：IFIND_AUTH_TOKEN（iFind MCP JWE Token，从 mcp.51ifind.com 获取）
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.request
import urllib.error
from typing import Optional, Any, Dict, List

from .base import BaseFetcher
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource

logger = logging.getLogger(__name__)

_IFIND_BASE_URL = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
_IFIND_FUND_SERVER = "hexin-ifind-ds-fund-mcp"
_HTTP_TIMEOUT_SECONDS = 15


class IFindFetcher(BaseFetcher):
    """同花顺 iFind 数据源（当前只提供 ETF 实时行情快照）。"""

    name = "IFindFetcher"
    # 作为实时行情补充源，不影响日线数据源优先级
    priority = 98
    allow_empty_daily_data = True

    def __init__(self, auth_token: Optional[str] = None) -> None:
        self._auth_token = auth_token or _read_ifind_token()
        self._session_id: Optional[str] = None
        self._request_id = 0
        # 兼容 GitHub Actions 的 CA 环境
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @classmethod
    def has_configured_credentials(cls, config: Any = None) -> bool:
        """检查是否配置了 iFind 密钥。"""
        if config is not None:
            token = (getattr(config, "ifind_auth_token", None) or "").strip()
            if token:
                return True
        return bool(_read_ifind_token())

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _post(self, payload: Dict[str, Any]) -> tuple:
        """向 iFind 基金服务发起 JSON-RPC 请求。"""
        url = f"{_IFIND_BASE_URL}/{_IFIND_FUND_SERVER}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self._auth_token,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers
        )
        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except Exception as e:
            return -1, str(e)

    def _initialize(self) -> bool:
        """初始化 iFind MCP 会话（幂等）。"""
        if not self._auth_token:
            logger.debug("[IFind] 未配置 IFIND_AUTH_TOKEN")
            return False
        if self._session_id:
            return True
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ifind-fetcher", "version": "1.0.0"},
            },
        }
        status, text = self._post(payload)
        if status != 200:
            logger.warning("[IFind] initialize 失败: status=%s body=%s", status, text[:200])
            return False
        # notifications/initialized
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return True

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        """调用 iFind 工具，返回 data 字段的 JSON 字符串（失败返回 None）。"""
        if not self._initialize():
            return None
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        status, text = self._post(payload)
        if status != 200:
            logger.warning("[IFind] tools/call 失败: status=%s", status)
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if "error" in data:
            logger.warning("[IFind] tools/call 返回 error: %s", str(data.get("error"))[:300])
            return None
        content = data.get("result", {}).get("content", [])
        for c in content:
            if c.get("type") == "text":
                return c.get("text", "")
        return None

    # ------------------------------------------------------------------
    # 实时行情（核心）
    # ------------------------------------------------------------------
    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """获取 ETF/基金实时行情快照。

        iFind fund_highfreq_quotes 返回的 volume 单位为「手」，
        此处换算为「股」以匹配系统 UnifiedRealtimeQuote.volume 口径。
        """
        code = _normalize_code(stock_code)
        if not code or not _is_etf_like(code):
            return None

        text = self._call_tool(
            "fund_highfreq_quotes",
            {
                "data_mode": "real_time",
                "symbols": code,
                "indicators": "最新价,最高价,最低价,涨跌,涨跌幅,成交量,成交额",
            },
        )
        if not text:
            logger.info("[IFind] %s 实时行情返回空", stock_code)
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, str):
            return None
        try:
            inner = json.loads(data)
        except json.JSONDecodeError:
            return None

        tables = inner.get("tables") or []
        if not tables or len(tables) < 2:
            return None
        header = tables[0]
        row = tables[1]
        if len(row) < 9:
            return None

        # 列: 代码, 名称, time, 最新价, 最高, 最低, 涨跌, 涨跌幅, 成交量, 成交额
        try:
            name = str(row[1])
            time_str = str(row[2])
            price = _to_float(row[3])
            high = _to_float(row[4])
            low = _to_float(row[5])
            change_amount = _to_float(row[6])
            change_pct = _to_float(row[7])
            volume_hand = _to_int(row[8])
            amount = _to_float(row[9])
        except (IndexError, ValueError):
            return None

        if volume_hand is None:
            return None

        # 手 -> 股（1 手 = 100 股），与系统日线成交量口径一致
        volume_share = volume_hand * 100

        quote = UnifiedRealtimeQuote(
            code=code,
            name=name,
            source=RealtimeSource.FALLBACK,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=volume_share,
            amount=amount,
            high=high,
            low=low,
            provider_timestamp=time_str,
            market="cn",
        )
        return quote

    # ------------------------------------------------------------------
    # 占位（日线等能力 iFind 不支持，保持默认）
    # ------------------------------------------------------------------
    def _fetch_raw_data(self, stock_code, start_date, end_date):
        import pandas as pd
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])

    def _normalize_data(self, df, stock_code):
        return df


def _normalize_code(stock_code: str) -> str:
    code = str(stock_code or "").strip()
    if not code:
        return ""
    # 去掉常见后缀
    for suffix in (".SH", ".SZ", ".BJ", ".OF", ".SH"):
        if code.upper().endswith(suffix):
            code = code[:-len(suffix)]
            break
    return code if code.isdigit() and len(code) == 6 else ""


def _is_etf_like(code: str) -> bool:
    """判断是否为 A 股场内基金/ETF 代码（5 开头沪市，1 开头深市）。"""
    return code.startswith(("5", "1"))


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "" or value == "-":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "" or value == "-":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_ifind_token() -> str:
    import os
    return os.getenv("IFIND_AUTH_TOKEN", "").strip()
