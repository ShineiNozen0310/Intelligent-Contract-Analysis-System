from __future__ import annotations

from typing import Any, Dict, Optional

from packages.shared_contract_schema import normalize_result_json


STAMP_STATUS_KEY = "stamp_status"
STAMP_TEXT_KEY = "是否盖章"


def stamp_status_to_cn(status: Any) -> str:
    value = str(status or "").strip().upper()
    if value == "YES":
        return "是"
    if value == "NO":
        return "否"
    if value == "UNCERTAIN":
        return "不确定"
    return "未提及"


def merge_stamp_result(result_json: Any, stamp_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    result = normalize_result_json(result_json)
    if not isinstance(stamp_result, dict):
        return result

    result.update(stamp_result)
    if STAMP_STATUS_KEY in stamp_result:
        result[STAMP_TEXT_KEY] = stamp_status_to_cn(stamp_result.get(STAMP_STATUS_KEY))
    return result


def build_error_result(error: Any, mode: Optional[str] = None, meta: Optional[Dict[str, Any]] = None, stamp_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {"error": str(error)}
    if mode is not None:
        base["mode"] = mode
    if meta is not None:
        base["meta"] = meta
    return merge_stamp_result(base, stamp_result)

