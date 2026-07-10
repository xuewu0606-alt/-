# -*- coding: utf-8 -*-
"""国信证券「小信智慧助手」资金流数据源（个股主力资金净流入）。

国信 skill 后端 (dgzt.guosen.com.cn) 提供个股主力资金净流入，按 period(日) 累计。
用作资金流兜底源：东财(akshare)/Tushare moneyflow 均不可用时补齐 stock_flow，
以支撑 analyzer 的资金流决策（买入信号缺资金流会被降级，见 _downgrade_buy_without_capital_flow）。

鉴权：环境变量 GS_API_KEY（国信 Skills 中心「获取 KEY」签发）。未配置则本源不可用。
安全：使用正常 TLS 校验（实测 dgzt.guosen.com.cn 证书有效，无需像官方脚本那样禁用校验）。
"""
from __future__ import annotations

import json
import logging
import os
import ssl
from typing import Any, Dict, Optional
from urllib import request as urllib_request
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

_BASE_URL = "https://dgzt.guosen.com.cn/skills/gsnews/market/agentbot/queryFundFlow/1.0"
_SOFT_NAME = "agent_skills"
_TIMEOUT = 8.0


def _ssl_context() -> ssl.SSLContext:
    """dgzt.guosen.com.cn 要求 legacy TLS renegotiation。

    仅启用 OP_LEGACY_SERVER_CONNECT，**保留证书与主机名校验**
    （比国信官方脚本的 CERT_NONE 全禁校验更安全）。
    """
    ctx = ssl.create_default_context()
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return ctx


def _resolve_api_key() -> str:
    key = os.environ.get("GS_API_KEY", "")
    if key:
        return key.strip()
    try:
        from src.config import get_config

        return (getattr(get_config(), "guosen_api_key", "") or "").strip()
    except Exception:
        return ""


def _set_code_for(stock_code: str) -> Optional[int]:
    """派生国信 setCode：1=上海, 0=深圳。北交所/其他返回 None（本源不支持）。"""
    digits = "".join(ch for ch in str(stock_code or "") if ch.isdigit())
    if len(digits) < 6:
        return None
    head = digits[-6:][0]
    if digits[-6:].startswith("6"):
        return 1  # 沪 A
    if head in ("0", "3"):
        return 0  # 深 A / 创业板
    return None  # 北交所(8/4) 等暂不支持


def _query_main_net_inflow(code6: str, set_code: int, period: int, api_key: str) -> Optional[float]:
    params = {
        "code": code6,
        "setCode": str(set_code),
        "period": str(period),
        "softName": _SOFT_NAME,
        "apiKey": api_key,
    }
    url = f"{_BASE_URL}?{urlencode(params)}"
    try:
        req = urllib_request.Request(url, headers={"User-Agent": "dsa-guosen-fetcher"})
        with urllib_request.urlopen(req, timeout=_TIMEOUT, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:  # noqa: BLE001 - fail-open
        logger.debug("Guosen fund flow request failed for %s(p=%d): %s", code6, period, exc)
        return None
    if not isinstance(payload, dict) or payload.get("result", {}).get("code") != 0:
        return None
    obj = payload.get("object") or {}
    value = obj.get("mainNetInflow")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GuosenFetcher:
    """国信资金流兜底源。仅实现个股主力资金净流入。"""

    @staticmethod
    def is_available() -> bool:
        return bool(_resolve_api_key())

    def get_stock_money_flow(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """返回 {main_net_inflow, inflow_5d, inflow_10d}（元），与 Tushare 兜底同构。fail-open。"""
        api_key = _resolve_api_key()
        if not api_key:
            return None
        set_code = _set_code_for(stock_code)
        if set_code is None:
            return None
        digits = "".join(ch for ch in str(stock_code) if ch.isdigit())[-6:]

        # 国信 mainNetInflow 按 period 天累计；period=1/5/10 对应三字段
        main_1d = _query_main_net_inflow(digits, set_code, 1, api_key)
        if main_1d is None:
            return None  # 主字段拿不到则整体失败，交由上游继续兜底
        inflow_5d = _query_main_net_inflow(digits, set_code, 5, api_key)
        inflow_10d = _query_main_net_inflow(digits, set_code, 10, api_key)
        return {
            "main_net_inflow": main_1d,
            "inflow_5d": inflow_5d,
            "inflow_10d": inflow_10d,
        }
