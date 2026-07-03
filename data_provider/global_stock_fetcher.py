# -*- coding: utf-8 -*-
"""
===================================
GlobalStockFetcher - 美股/港股备用数据源 (Priority 3)
===================================

数据来源（来自 github.com/simonlin1212/global-stock-data）：
1. 新浪财经直连接口  - 美股实时行情（36字段）
2. 腾讯财经直连接口  - 美股（71字段）/ 港股（78字段）实时行情
3. 东方财富 push2   - 美股/港股统一实时行情（含换手率）
4. 新浪财经 JSONP   - 美股历史K线（最远至1984年）
5. Yahoo Chart v8   - 美股/港股历史K线（无需登录，自动管理 crumb）

定位：
- 美股：作为 YfinanceFetcher 之前的备用链路，零认证、仅需 requests
- 港股：作为 AkshareFetcher 之前的补充，提供多源实时行情

特点：
- 零 API Key 要求
- 仅依赖标准库 + requests（项目已有）
- 按来源熔断，单个接口挂掉不影响其他

市场代码前缀（东方财富）：
  105 = NASDAQ, 106 = NYSE, 107 = US ETF, 116 = HK
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS
from .realtime_types import (
    UnifiedRealtimeQuote, RealtimeSource,
    get_realtime_circuit_breaker, safe_float, safe_int,
)
from .us_index_mapping import is_us_stock_code, is_us_index_code

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 东方财富市场前缀
_EM_PREFIX_NASDAQ = 105
_EM_PREFIX_NYSE = 106
_EM_PREFIX_HK = 116

# Yahoo Chart 缓存 session（管理 crumb）
_yahoo_session: Optional[requests.Session] = None
_yahoo_session_ts: float = 0
_YAHOO_SESSION_TTL = 3600  # crumb 1小时刷新一次


def _get_yahoo_session() -> requests.Session:
    """获取带有有效 crumb 的 Yahoo Finance Session（带 TTL 缓存，自动重试）。"""
    global _yahoo_session, _yahoo_session_ts

    now = time.time()
    if _yahoo_session is not None and (now - _yahoo_session_ts) < _YAHOO_SESSION_TTL:
        return _yahoo_session

    s = requests.Session()
    s.headers["User-Agent"] = _UA
    crumb = ""
    for attempt in range(3):
        try:
            s.get("https://fc.yahoo.com", timeout=8)
            r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=8)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.debug(f"[GlobalStock] Yahoo crumb 429，等待 {wait}s 重试...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            crumb = r.text.strip()
            break
        except Exception as e:
            logger.debug(f"[GlobalStock] Yahoo crumb 获取失败(attempt {attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2 ** attempt)

    s._crumb = crumb  # type: ignore[attr-defined]
    _yahoo_session = s
    _yahoo_session_ts = now
    return s


def _is_hk_code(stock_code: str) -> bool:
    """判断是否为港股代码（5位数字，或带 hk 前缀/HK 后缀）。"""
    code = stock_code.strip().lower()
    if code.endswith(".hk"):
        return code[:-3].isdigit()
    if code.startswith("hk"):
        return code[2:].isdigit() and 1 <= len(code[2:]) <= 5
    return code.isdigit() and len(code) == 5


def _normalize_hk_code(stock_code: str) -> str:
    """统一港股代码为 5 位数字字符串，如 'HK00700' -> '00700'。"""
    code = stock_code.strip().lower()
    if code.endswith(".hk"):
        code = code[:-3]
    if code.startswith("hk"):
        code = code[2:]
    return code.zfill(5)


def _to_yahoo_symbol(stock_code: str) -> str:
    """将股票代码转换为 Yahoo Finance 格式。"""
    if _is_hk_code(stock_code):
        digits = _normalize_hk_code(stock_code).lstrip("0") or "0"
        return f"{digits}.HK"
    # A 股 (不常用，兜底)
    code = stock_code.strip().split(".")[0]
    if code.startswith(("6", "5", "90")):
        return f"{code}.SS"
    if code.isdigit():
        return f"{code}.SZ"
    # 美股直接大写
    return stock_code.strip().upper()


def _guess_em_prefix(stock_code: str) -> int:
    """推断东方财富市场前缀（美股默认 NASDAQ=105）。"""
    # 港股
    if _is_hk_code(stock_code):
        return _EM_PREFIX_HK
    # 美股：无法精确区分 NASDAQ/NYSE，默认 NASDAQ（105）
    # 若查不到可由调用方重试 106
    return _EM_PREFIX_NASDAQ


# ---------------------------------------------------------------------------
# 各接口原子函数
# ---------------------------------------------------------------------------

def _quote_us_sina(ticker: str) -> dict:
    """新浪财经美股实时行情（36字段）。"""
    url = f"https://hq.sinajs.cn/list=gb_{ticker.lower()}"
    r = requests.get(url, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": _UA,
    }, timeout=10)
    r.encoding = "gbk"
    m = re.search(r'"(.+)"', r.text)
    if not m:
        return {}
    fields = m.group(1).split(",")
    if len(fields) < 30:
        return {}
    return {
        "name": fields[0],
        "price": safe_float(fields[1]),
        "change_pct": safe_float(fields[2]),
        "prev_close": safe_float(fields[26]),
        "open": safe_float(fields[5]),
        "high": safe_float(fields[6]),
        "low": safe_float(fields[7]),
        "volume": safe_float(fields[10]),
        "high_52w": safe_float(fields[8]),
        "low_52w": safe_float(fields[9]),
        "market_cap": safe_float(fields[12]),
        "pe": safe_float(fields[14]),
    }


def _quote_us_tencent(ticker: str) -> dict:
    """腾讯财经美股实时行情（71字段）。"""
    url = f"https://qt.gtimg.cn/q=us{ticker.upper()}"
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
    r.encoding = "gbk"
    m = re.search(r'"(.+)"', r.text)
    if not m:
        return {}
    fields = m.group(1).split("~")
    if len(fields) < 50:
        return {}
    return {
        "name": fields[1],
        "price": safe_float(fields[3]),
        "prev_close": safe_float(fields[4]),
        "open": safe_float(fields[5]),
        "volume": safe_int(fields[6]),
        "high": safe_float(fields[33]),
        "low": safe_float(fields[34]),
        "high_52w": safe_float(fields[35]),
        "low_52w": safe_float(fields[36]),
        "change_pct": safe_float(fields[32]),
        "market_cap": safe_float(fields[44]),
        "pe": safe_float(fields[53]),
        "pb": safe_float(fields[56]),
    }


def _quote_hk_tencent(code: str) -> dict:
    """腾讯财经港股实时行情（78字段）。"""
    url = f"https://qt.gtimg.cn/q=r_hk{code}"
    r = requests.get(url, headers={"User-Agent": _UA}, timeout=10)
    r.encoding = "gbk"
    m = re.search(r'"(.+)"', r.text)
    if not m:
        return {}
    fields = m.group(1).split("~")
    if len(fields) < 50:
        return {}
    return {
        "name": fields[1],
        "price": safe_float(fields[3]),
        "prev_close": safe_float(fields[4]),
        "open": safe_float(fields[5]),
        "high": safe_float(fields[33]),
        "low": safe_float(fields[34]),
        "volume": safe_int(fields[6]),
        "amount": safe_float(fields[37]),
        "change_pct": safe_float(fields[32]),
        "pe": safe_float(fields[39]),
        "pb": safe_float(fields[56]),
        "high_52w": safe_float(fields[35]),
        "low_52w": safe_float(fields[36]),
        "market_cap": safe_float(fields[44]),
    }


def _quote_eastmoney(ticker_or_code: str, secid_prefix: int) -> dict:
    """东方财富 push2 实时行情（美股/港股统一）。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": f"{secid_prefix}.{ticker_or_code}",
        "fields": "f43,f44,f45,f46,f47,f48,f55,f57,f58,f59,f60,f170",
    }
    r = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=10)
    d = r.json().get("data") or {}
    if not d:
        return {}
    dec = d.get("f59", 3)
    div = 10 ** dec

    def _p(key):
        v = d.get(key)
        if v is None or v == "-":
            return None
        try:
            return round(v / div, dec)
        except (TypeError, ZeroDivisionError):
            return None

    return {
        "name": d.get("f58"),
        "price": _p("f43"),
        "high": _p("f44"),
        "low": _p("f45"),
        "open": _p("f46"),
        "volume": d.get("f47"),
        "amount": d.get("f48"),
        "turnover_rate": d.get("f55"),
        "prev_close": _p("f60"),
        "change_pct": round(d["f170"] / 100, 2) if d.get("f170") is not None else None,
    }


def _kline_us_sina(ticker: str, num: int = 500) -> list:
    """新浪财经美股历史K线（最远1984年）。"""
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp.php"
        "/var/US_MinKService.getDailyK"
    )
    r = requests.get(url, params={"symbol": ticker.upper(), "num": num},
                     headers={"Referer": "https://finance.sina.com.cn/",
                               "User-Agent": _UA}, timeout=15)
    m = re.search(r'\((\[.+\])\)', r.text)
    if not m:
        return []
    items = json.loads(m.group(1))
    return [
        {
            "date": item.get("d"),
            "open": safe_float(item.get("o", 0)) or 0.0,
            "high": safe_float(item.get("h", 0)) or 0.0,
            "low": safe_float(item.get("l", 0)) or 0.0,
            "close": safe_float(item.get("c", 0)) or 0.0,
            "volume": safe_int(item.get("v", 0)) or 0,
        }
        for item in items
    ]


def _kline_yahoo(symbol: str, interval: str = "1d", range_: str = "5y") -> list:
    """Yahoo Finance Chart v8 历史K线（美股/港股通用）。"""
    session = _get_yahoo_session()
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    params: dict = {"interval": interval, "range": range_}
    crumb = getattr(session, "_crumb", "")
    if crumb:
        params["crumb"] = crumb

    r = session.get(url, params=params, timeout=15)
    r.raise_for_status()

    chart = r.json().get("chart", {}).get("result", [{}])[0]
    timestamps = chart.get("timestamp", [])
    quote = chart.get("indicators", {}).get("quote", [{}])[0]

    is_intraday = "m" in interval or "h" in interval
    result = []
    for i, ts in enumerate(timestamps):
        date_str = (
            datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            if is_intraday
            else datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        )
        o = quote.get("open", [])[i] if i < len(quote.get("open", [])) else None
        h = quote.get("high", [])[i] if i < len(quote.get("high", [])) else None
        lo = quote.get("low", [])[i] if i < len(quote.get("low", [])) else None
        c = quote.get("close", [])[i] if i < len(quote.get("close", [])) else None
        v = quote.get("volume", [])[i] if i < len(quote.get("volume", [])) else None
        if c is None:
            continue
        result.append({
            "date": date_str,
            "open": round(o, 4) if o else 0.0,
            "high": round(h, 4) if h else 0.0,
            "low": round(lo, 4) if lo else 0.0,
            "close": round(c, 4),
            "volume": int(v) if v else 0,
        })
    return result


# ---------------------------------------------------------------------------
# Fetcher 主类
# ---------------------------------------------------------------------------

class GlobalStockFetcher(BaseFetcher):
    """
    美股 / 港股备用数据源（新浪 / 腾讯 / 东财 / Yahoo 直连）

    优先级：3（位于 PytdxFetcher 之后、BaostockFetcher 之前）
    支持范围：美股（NASDAQ/NYSE）+ 港股
    历史数据：新浪（美股）或 Yahoo Chart v8（美股/港股）
    实时行情：多源自动选优，带熔断保护
    """

    name = "GlobalStockFetcher"
    priority = int(os.getenv("GLOBAL_STOCK_PRIORITY", "3"))

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # BaseFetcher 接口实现
    # ------------------------------------------------------------------

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取历史 K 线数据。

        美股：优先新浪（更长历史），备用 Yahoo Chart v8
        港股：仅使用 Yahoo Chart v8
        其他：不支持，抛出 DataFetchError
        """
        if _is_hk_code(stock_code):
            return self._fetch_history_yahoo(stock_code, start_date, end_date)

        if is_us_stock_code(stock_code):
            try:
                df = self._fetch_history_sina(stock_code, start_date, end_date)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.warning(f"[GlobalStock] 新浪历史K线失败 {stock_code}: {e}，切换 Yahoo")
            return self._fetch_history_yahoo(stock_code, start_date, end_date)

        raise DataFetchError(
            f"GlobalStockFetcher 仅支持美股/港股，{stock_code} 不适用"
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """数据已在 _fetch_history_* 中标准化，此处做最终列名对齐。"""
        df = df.copy()
        rename_map = {
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        df["code"] = stock_code
        if "amount" not in df.columns:
            df["amount"] = 0.0
        if "pct_chg" not in df.columns and "close" in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
            df["pct_chg"] = df["pct_chg"].fillna(0)
        keep = ["code"] + [c for c in STANDARD_COLUMNS if c in df.columns]
        return df[keep]

    def get_realtime_quote(self, stock_code: str, source: str = "auto") -> Optional[UnifiedRealtimeQuote]:
        """
        获取实时行情。

        美股查询顺序：新浪 → 腾讯 → 东方财富
        港股查询顺序：腾讯 → 东方财富
        其他市场：返回 None
        """
        if is_us_index_code(stock_code):
            return None

        if _is_hk_code(stock_code):
            return self._realtime_hk(stock_code)

        if is_us_stock_code(stock_code):
            return self._realtime_us(stock_code)

        return None

    # ------------------------------------------------------------------
    # 历史数据内部方法
    # ------------------------------------------------------------------

    def _fetch_history_sina(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """新浪财经美股历史K线，按日期过滤。"""
        logger.info(f"[GlobalStock] 新浪历史K线: {ticker}")
        rows = _kline_us_sina(ticker, num=2000)
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)
        df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)].copy()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        logger.info(f"[GlobalStock] 新浪历史K线 {ticker}: {len(df)} 行")
        return df

    def _fetch_history_yahoo(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Yahoo Chart v8 历史K线（按日期过滤）。"""
        symbol = _to_yahoo_symbol(stock_code)
        # 计算覆盖所需日期范围的 range_ 参数
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        days = (end_dt - start_dt).days
        if days <= 30:
            range_ = "1mo"
        elif days <= 90:
            range_ = "3mo"
        elif days <= 180:
            range_ = "6mo"
        elif days <= 365:
            range_ = "1y"
        elif days <= 730:
            range_ = "2y"
        else:
            range_ = "5y"

        logger.info(f"[GlobalStock] Yahoo K线: {symbol}, range={range_}")
        try:
            rows = _kline_yahoo(symbol, interval="1d", range_=range_)
        except Exception as e:
            raise DataFetchError(f"Yahoo K线失败 {symbol}: {e}") from e

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        start_dt_ts = pd.to_datetime(start_date)
        end_dt_ts = pd.to_datetime(end_date)
        df = df[(df["date"] >= start_dt_ts) & (df["date"] <= end_dt_ts)].copy()
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        logger.info(f"[GlobalStock] Yahoo K线 {symbol}: {len(df)} 行")
        return df

    # ------------------------------------------------------------------
    # 实时行情内部方法
    # ------------------------------------------------------------------

    def _realtime_us(self, ticker: str) -> Optional[UnifiedRealtimeQuote]:
        """美股实时行情：新浪 → 腾讯 → 东方财富。"""
        cb = get_realtime_circuit_breaker()
        symbol = ticker.strip().upper()

        # 1. 新浪
        if cb.is_available("global_sina_us"):
            try:
                d = _quote_us_sina(symbol)
                if d and d.get("price"):
                    cb.record_success("global_sina_us")
                    q = self._build_quote_us(symbol, d, RealtimeSource.AKSHARE_SINA)
                    logger.info(f"[GlobalStock-新浪] {symbol} 价格={q.price}, 涨跌={q.change_pct}%")
                    return q
            except Exception as e:
                cb.record_failure("global_sina_us", str(e))
                logger.debug(f"[GlobalStock] 新浪美股行情失败 {symbol}: {e}")

        # 2. 腾讯
        if cb.is_available("global_tencent_us"):
            try:
                d = _quote_us_tencent(symbol)
                if d and d.get("price"):
                    cb.record_success("global_tencent_us")
                    q = self._build_quote_us(symbol, d, RealtimeSource.TENCENT)
                    logger.info(f"[GlobalStock-腾讯] {symbol} 价格={q.price}, 涨跌={q.change_pct}%")
                    return q
            except Exception as e:
                cb.record_failure("global_tencent_us", str(e))
                logger.debug(f"[GlobalStock] 腾讯美股行情失败 {symbol}: {e}")

        # 3. 东方财富（先试 NASDAQ，再试 NYSE）
        for prefix, prefix_name in [(_EM_PREFIX_NASDAQ, "NASDAQ"), (_EM_PREFIX_NYSE, "NYSE")]:
            key = f"global_em_us_{prefix}"
            if not cb.is_available(key):
                continue
            try:
                d = _quote_eastmoney(symbol, prefix)
                if d and d.get("price"):
                    cb.record_success(key)
                    q = self._build_quote_us(symbol, d, RealtimeSource.AKSHARE_EM)
                    logger.info(f"[GlobalStock-东财({prefix_name})] {symbol} 价格={q.price}")
                    return q
            except Exception as e:
                cb.record_failure(key, str(e))
                logger.debug(f"[GlobalStock] 东财({prefix_name})美股行情失败 {symbol}: {e}")

        logger.warning(f"[GlobalStock] 美股 {symbol} 所有实时行情接口均失败")
        return None

    def _realtime_hk(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        """港股实时行情：腾讯 → 东方财富。"""
        cb = get_realtime_circuit_breaker()
        code = _normalize_hk_code(stock_code)

        # 1. 腾讯（78字段，最全）
        if cb.is_available("global_tencent_hk"):
            try:
                d = _quote_hk_tencent(code)
                if d and d.get("price"):
                    cb.record_success("global_tencent_hk")
                    q = UnifiedRealtimeQuote(
                        code=stock_code,
                        name=d.get("name", ""),
                        source=RealtimeSource.TENCENT,
                        price=d.get("price"),
                        change_pct=d.get("change_pct"),
                        open_price=d.get("open"),
                        high=d.get("high"),
                        low=d.get("low"),
                        pre_close=d.get("prev_close"),
                        volume=d.get("volume"),
                        amount=d.get("amount"),
                        pe_ratio=d.get("pe"),
                        pb_ratio=d.get("pb"),
                        total_mv=d.get("market_cap"),
                        high_52w=d.get("high_52w"),
                        low_52w=d.get("low_52w"),
                    )
                    logger.info(f"[GlobalStock-腾讯] HK{code} 价格={q.price}, 涨跌={q.change_pct}%")
                    return q
            except Exception as e:
                cb.record_failure("global_tencent_hk", str(e))
                logger.debug(f"[GlobalStock] 腾讯港股行情失败 {code}: {e}")

        # 2. 东方财富
        if cb.is_available("global_em_hk"):
            try:
                d = _quote_eastmoney(code, _EM_PREFIX_HK)
                if d and d.get("price"):
                    cb.record_success("global_em_hk")
                    q = UnifiedRealtimeQuote(
                        code=stock_code,
                        name=d.get("name", ""),
                        source=RealtimeSource.AKSHARE_EM,
                        price=d.get("price"),
                        change_pct=d.get("change_pct"),
                        open_price=d.get("open"),
                        high=d.get("high"),
                        low=d.get("low"),
                        pre_close=d.get("prev_close"),
                        volume=d.get("volume"),
                        amount=d.get("amount"),
                        turnover_rate=d.get("turnover_rate"),
                    )
                    logger.info(f"[GlobalStock-东财] HK{code} 价格={q.price}, 涨跌={q.change_pct}%")
                    return q
            except Exception as e:
                cb.record_failure("global_em_hk", str(e))
                logger.debug(f"[GlobalStock] 东财港股行情失败 {code}: {e}")

        logger.warning(f"[GlobalStock] 港股 {stock_code} 所有实时行情接口均失败")
        return None

    def _build_quote_us(
        self,
        ticker: str,
        d: dict,
        source: RealtimeSource,
    ) -> UnifiedRealtimeQuote:
        """从原始字典构建美股 UnifiedRealtimeQuote。"""
        price = d.get("price")
        pre_close = d.get("prev_close")
        change_pct = d.get("change_pct")
        if change_pct is None and price and pre_close and pre_close > 0:
            change_pct = round((price - pre_close) / pre_close * 100, 2)

        return UnifiedRealtimeQuote(
            code=ticker,
            name=d.get("name", ""),
            source=source,
            price=price,
            change_pct=change_pct,
            open_price=d.get("open"),
            high=d.get("high"),
            low=d.get("low"),
            pre_close=pre_close,
            volume=d.get("volume"),
            amount=d.get("amount"),
            pe_ratio=d.get("pe"),
            pb_ratio=d.get("pb"),
            total_mv=d.get("market_cap"),
            turnover_rate=d.get("turnover_rate"),
            high_52w=d.get("high_52w"),
            low_52w=d.get("low_52w"),
        )
