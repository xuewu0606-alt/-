# -*- coding: utf-8 -*-
"""Helpers for parsing report sniper-point price values."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, Optional


SNIPER_KEYS = ("ideal_buy", "secondary_buy", "stop_loss", "take_profit")


def parse_sniper_value(value: Any) -> Optional[float]:
    """Parse a sniper point value from report text into a positive price."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if parsed > 0 else None

    text = str(value).replace(",", "").replace("，", "").strip()
    if not text or text in {"-", "—", "N/A"}:
        return None

    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except ValueError:
        pass

    colon_pos = max(text.rfind("："), text.rfind(":"))
    yuan_pos = text.find("元", colon_pos + 1 if colon_pos != -1 else 0)
    if yuan_pos != -1:
        segment_start = colon_pos + 1 if colon_pos != -1 else 0
        segment = text[segment_start:yuan_pos]
        valid_numbers = []
        for match in re.finditer(r"-?\d+(?:\.\d+)?", segment):
            start_idx = match.start()
            if start_idx >= 2 and segment[start_idx - 2:start_idx].upper() == "MA":
                continue
            valid_numbers.append(match.group())
        if valid_numbers:
            try:
                parsed = abs(float(valid_numbers[-1]))
                return parsed if parsed > 0 else None
            except ValueError:
                pass

    paren_pos = len(text)
    for paren_char in ("(", "（"):
        pos = text.find(paren_char)
        if pos != -1:
            paren_pos = min(paren_pos, pos)
    search_text = text[:paren_pos].strip() or text

    valid_numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", search_text):
        start_idx = match.start()
        if start_idx >= 2 and search_text[start_idx - 2:start_idx].upper() == "MA":
            continue
        valid_numbers.append(match.group())
    if valid_numbers:
        try:
            parsed = float(valid_numbers[-1])
            return parsed if parsed > 0 else None
        except ValueError:
            pass
    return None


def extract_sniper_points(result: Any) -> Dict[str, Optional[float]]:
    """Extract normalized sniper-point prices from a completed analysis result."""

    raw_points: Mapping[str, Any] = {}

    if hasattr(result, "get_sniper_points"):
        candidate = result.get_sniper_points() or {}
        if isinstance(candidate, Mapping):
            raw_points = candidate

    if not _has_any_sniper_value(raw_points):
        dashboard = getattr(result, "dashboard", None)
        if isinstance(dashboard, Mapping):
            raw_points = find_sniper_points(dashboard) or raw_points

    if not _has_any_sniper_value(raw_points):
        raw_response = getattr(result, "raw_response", None)
        if isinstance(raw_response, Mapping):
            raw_points = find_sniper_points(raw_response) or raw_points

    return {key: parse_sniper_value(raw_points.get(key)) for key in SNIPER_KEYS}


def _has_any_sniper_value(points: Mapping[str, Any]) -> bool:
    return any(points.get(key) not in (None, "") for key in SNIPER_KEYS)


# 点位一致性校验阈值。
# 起因：002532 天山铝业 2026-07-09 报告给出 买入11.85/止损10.00/止盈12.50，
# 盈亏比仅 0.35；601899 紫金矿业 2026-07-06 记录 止损(27.8)高于买入(26.6)。
# LLM 自由生成的点位缺乏确定性校验，直通报告与推送，误导每日操作。
MIN_RISK_REWARD_RATIO = 1.5
MAX_STOP_DISTANCE_PCT = 12.0
MAX_POINT_DEVIATION_PCT = 30.0


def validate_sniper_points(
    points: Mapping[str, Optional[float]],
    current_price: Optional[float] = None,
) -> list:
    """Deterministic coherence checks on parsed sniper points.

    Returns a list of zh violation strings; empty list means coherent.
    Only checks fields that are present — missing values are not violations.
    """
    violations: list = []
    buy = points.get("ideal_buy")
    secondary = points.get("secondary_buy")
    stop = points.get("stop_loss")
    target = points.get("take_profit")

    if buy and stop and stop >= buy:
        violations.append(f"止损位({stop:g})不低于理想买入点({buy:g})，点位失真")
    if buy and target and target <= buy:
        violations.append(f"止盈位({target:g})不高于理想买入点({buy:g})，点位失真")
    if buy and stop and target and stop < buy < target:
        risk_reward = (target - buy) / (buy - stop)
        if risk_reward < MIN_RISK_REWARD_RATIO:
            violations.append(
                f"盈亏比仅{risk_reward:.2f}（<{MIN_RISK_REWARD_RATIO:g}），赔率不佳"
            )
    if buy and stop and 0 < stop < buy:
        stop_distance_pct = (buy - stop) / buy * 100
        if stop_distance_pct > MAX_STOP_DISTANCE_PCT:
            violations.append(
                f"止损距买入点{stop_distance_pct:.1f}%过宽（>{MAX_STOP_DISTANCE_PCT:g}%）"
            )
    if secondary and buy and secondary > buy:
        violations.append(f"次优买点({secondary:g})高于理想买点({buy:g})")
    if current_price and current_price > 0:
        for label, value in (("理想买入", buy), ("止损", stop), ("止盈", target)):
            if value:
                deviation_pct = abs(value - current_price) / current_price * 100
                if deviation_pct > MAX_POINT_DEVIATION_PCT:
                    violations.append(
                        f"{label}位({value:g})偏离现价{deviation_pct:.0f}%，疑似失真"
                    )
    return violations


def find_sniper_points(data: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    if not isinstance(data, Mapping):
        return None

    if any(key in data for key in SNIPER_KEYS):
        return data

    sniper_points = data.get("sniper_points")
    if isinstance(sniper_points, Mapping) and sniper_points:
        return sniper_points

    battle_plan = data.get("battle_plan")
    if isinstance(battle_plan, Mapping):
        sniper_points = battle_plan.get("sniper_points")
        if isinstance(sniper_points, Mapping) and sniper_points:
            return sniper_points

    inner_dashboard = data.get("dashboard")
    if isinstance(inner_dashboard, Mapping):
        found = find_sniper_points(inner_dashboard)
        if found:
            return found

    return None
