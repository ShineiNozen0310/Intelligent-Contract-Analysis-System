from __future__ import annotations

import re
from html import escape
from typing import Any, Dict, List

SCHEMA_VERSION = "1.0.0"

_CN_CONTRACT_TYPE = "\u5408\u540c\u7c7b\u578b"
_CN_CONTRACT_TYPE_DETAIL = "\u5408\u540c\u7c7b\u578b\u660e\u7ec6"
_CN_STAMP = "\u662f\u5426\u76d6\u7ae0"
_CN_KEY_FACTS = "\u5408\u540c\u5173\u952e\u8981\u7d20"
_CN_OVERVIEW = "\u5ba1\u67e5\u6982\u8ff0"
_CN_RISKS = "\u98ce\u9669\u70b9"
_CN_RISKS_WITH_SUGGESTION = "\u98ce\u9669\u70b9\u53ca\u5efa\u8bae"
_CN_IMPROVEMENTS = "\u6539\u8fdb\u5efa\u8bae"
_CN_IMPROVEMENTS_ALT = "\u6539\u8fdb\u63aa\u65bd"

_DEFAULT_TYPE = "\u672a\u8bc6\u522b"
_DEFAULT_STAMP = "\u672a\u63d0\u53ca"
_DEFAULT_OVERVIEW = "\u6682\u65e0\u6982\u8ff0"

_REPORT_TITLE = "\u5408\u540c\u5ba1\u67e5\u62a5\u544a"


def normalize_result_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    return {"result_json_raw": raw}


def _first_non_empty(values: List[Any]) -> str:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            continue
        if value is not None:
            return str(value)
    return ""


def _first_non_empty_value(values: List[Any]) -> Any:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            continue
        if value is not None:
            return value
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return [value]


def _extract_review_items(result_json: Dict[str, Any], keys: List[str]) -> List[Any]:
    candidates: List[Any] = []
    for key in keys:
        candidates.append(result_json.get(key))
    for prefix in ("result", "review", "data"):
        node = result_json.get(prefix)
        if isinstance(node, dict):
            for key in keys:
                candidates.append(node.get(key))
    picked = _first_non_empty_value(candidates)
    return _as_list(picked)


def _normalize_review_item(item: Any, default_title: str) -> Dict[str, str]:
    if isinstance(item, str):
        text = item.strip()
        return {"title": text or default_title, "level": "", "problem": text, "suggestion": ""}

    if isinstance(item, dict):
        return {
            "title": _first_non_empty(
                [
                    item.get("title"),
                    item.get("name"),
                    item.get("item"),
                    item.get(_CN_RISKS),
                    item.get("\u95ee\u9898\u70b9"),
                    default_title,
                ]
            ),
            "level": _first_non_empty(
                [
                    item.get("level"),
                    item.get("severity"),
                    item.get("risk_level"),
                    item.get("\u98ce\u9669\u7b49\u7ea7"),
                ]
            ),
            "problem": _first_non_empty(
                [
                    item.get("problem"),
                    item.get("issue"),
                    item.get("desc"),
                    item.get("description"),
                    item.get("\u95ee\u9898"),
                ]
            ),
            "suggestion": _first_non_empty(
                [
                    item.get("suggestion"),
                    item.get("advice"),
                    item.get("fix"),
                    item.get("solution"),
                    item.get("\u5efa\u8bae"),
                    item.get("\u4fee\u6539\u5efa\u8bae"),
                ]
            ),
        }

    text = _first_non_empty([item]) or default_title
    return {"title": text, "level": "", "problem": text, "suggestion": ""}


def _has_meaningful_items(items: List[Dict[str, str]]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        if _first_non_empty([item.get("title"), item.get("problem"), item.get("suggestion")]):
            return True
    return False


def _extract_improvement_suggestions_from_markdown(markdown_text: str, max_items: int = 8) -> List[Dict[str, str]]:
    if not markdown_text:
        return []
    pattern = re.compile(
        r"(?:\u6539\u8fdb\u5efa\u8bae|\u4f18\u5316\u5efa\u8bae|\u5b8c\u5584\u5efa\u8bae|\u4fee\u6539\u5efa\u8bae|suggestion|recommendation)[:\uff1a]?\s*(.+)",
        re.IGNORECASE,
    )
    out: List[Dict[str, str]] = []
    seen = set()
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        suggestion = (match.group(1) or "").strip(" \uff1a:;,\u3002")
        if len(suggestion) < 6 or suggestion in seen:
            continue
        seen.add(suggestion)
        out.append({"title": _CN_IMPROVEMENTS, "level": "", "problem": "", "suggestion": suggestion})
        if len(out) >= max_items:
            break
    return out


def _fallback_improvements_from_risks(risks: List[Dict[str, str]], max_items: int = 8) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for item in risks:
        if not isinstance(item, dict):
            continue
        suggestion = _first_non_empty(
            [
                item.get("suggestion"),
                item.get("advice"),
                item.get("fix"),
                item.get("solution"),
                item.get("\u5efa\u8bae"),
                item.get("\u4fee\u6539\u5efa\u8bae"),
            ]
        ).strip()
        if len(suggestion) < 6 or suggestion in seen:
            continue
        seen.add(suggestion)
        out.append(
            {
                "title": _first_non_empty([item.get("title"), item.get("name"), item.get("item"), _CN_IMPROVEMENTS]),
                "level": "",
                "problem": _first_non_empty([item.get("problem"), item.get("issue"), item.get("description"), item.get("\u95ee\u9898")]),
                "suggestion": suggestion,
            }
        )
        if len(out) >= max_items:
            break
    return out


def build_report_payload(result_json: Dict[str, Any] | None, result_markdown: str = "") -> Dict[str, Any]:
    data = normalize_result_json(result_json)
    raw_markdown = (result_markdown or "").strip()

    if not data:
        return {
            "schema_version": SCHEMA_VERSION,
            "contract_type": _DEFAULT_TYPE,
            "confidence_text": "-",
            "type_source": "markdown",
            "stamp_text": _DEFAULT_STAMP,
            "stamp_color": "#6b7280",
            "key_facts": {
                "\u5408\u540c\u540d\u79f0": _DEFAULT_STAMP,
                "\u7532\u65b9": _DEFAULT_STAMP,
                "\u4e59\u65b9": _DEFAULT_STAMP,
                "\u91d1\u989d": _DEFAULT_STAMP,
                "\u671f\u9650": _DEFAULT_STAMP,
            },
            "overview": raw_markdown or _DEFAULT_OVERVIEW,
            "risks": [],
            "improvements": [],
        }

    contract_type = _first_non_empty(
        [
            data.get(_CN_CONTRACT_TYPE),
            data.get("contract_type"),
            data.get("type"),
            data.get("type_l2"),
        ]
    ) or _DEFAULT_TYPE

    type_detail_raw = (
        data.get(_CN_CONTRACT_TYPE_DETAIL)
        if isinstance(data.get(_CN_CONTRACT_TYPE_DETAIL), dict)
        else data.get("contract_type_detail")
        if isinstance(data.get("contract_type_detail"), dict)
        else data.get("type_detail")
    )
    type_detail = dict(type_detail_raw) if isinstance(type_detail_raw, dict) else {}
    conf = type_detail.get("confidence")
    if isinstance(conf, (int, float)):
        confidence_text = f"{round(float(conf) * 100)}%"
    elif conf is not None:
        confidence_text = str(conf)
    else:
        confidence_text = "-"

    stamp_value = data.get(_CN_STAMP)
    if not stamp_value:
        stamp_status = data.get("stamp_status")
        if stamp_status == "YES":
            stamp_value = "\u662f"
        elif stamp_status == "NO":
            stamp_value = "\u5426"
        elif stamp_status == "UNCERTAIN":
            stamp_value = "\u4e0d\u786e\u5b9a"
    stamp_text = _first_non_empty([stamp_value, _DEFAULT_STAMP])
    if stamp_text in ("\u662f", "YES", "True", "true"):
        stamp_color = "#0c7b48"
    elif stamp_text in ("\u5426", "NO", "False", "false"):
        stamp_color = "#b42318"
    else:
        stamp_color = "#6b7280"

    key_facts_bucket = data.get("key_facts")
    if not isinstance(key_facts_bucket, dict):
        key_facts_bucket = {}
    key_facts = {
        "\u5408\u540c\u540d\u79f0": _first_non_empty(
            [
                key_facts_bucket.get("\u5408\u540c\u540d\u79f0"),
                key_facts_bucket.get("\u534f\u8bae\u540d\u79f0"),
                key_facts_bucket.get("contract_name"),
                key_facts_bucket.get("name"),
                _DEFAULT_STAMP,
            ]
        ),
        "\u7532\u65b9": _first_non_empty(
            [
                key_facts_bucket.get("\u7532\u65b9"),
                key_facts_bucket.get("\u7532\u65b9\u540d\u79f0"),
                key_facts_bucket.get("partyA"),
                key_facts_bucket.get("party_a"),
                _DEFAULT_STAMP,
            ]
        ),
        "\u4e59\u65b9": _first_non_empty(
            [
                key_facts_bucket.get("\u4e59\u65b9"),
                key_facts_bucket.get("\u4e59\u65b9\u540d\u79f0"),
                key_facts_bucket.get("partyB"),
                key_facts_bucket.get("party_b"),
                _DEFAULT_STAMP,
            ]
        ),
        "\u91d1\u989d": _first_non_empty(
            [
                key_facts_bucket.get("\u91d1\u989d"),
                key_facts_bucket.get("\u5408\u540c\u91d1\u989d"),
                key_facts_bucket.get("\u603b\u91d1\u989d"),
                key_facts_bucket.get("amount"),
                _DEFAULT_STAMP,
            ]
        ),
        "\u671f\u9650": _first_non_empty(
            [
                key_facts_bucket.get("\u671f\u9650"),
                key_facts_bucket.get("\u5408\u540c\u671f\u9650"),
                key_facts_bucket.get("\u6709\u6548\u671f"),
                key_facts_bucket.get("term"),
                _DEFAULT_STAMP,
            ]
        ),
    }

    overview = _first_non_empty(
        [
            data.get(_CN_OVERVIEW),
            data.get("overview"),
            data.get("summary"),
            (data.get("result") or {}).get("overview") if isinstance(data.get("result"), dict) else None,
            raw_markdown,
        ]
    ) or _DEFAULT_OVERVIEW

    risks = [
        _normalize_review_item(item, _CN_RISKS)
        for item in _extract_review_items(data, [_CN_RISKS, "risks", "risk_points", _CN_RISKS_WITH_SUGGESTION])
    ]
    for item in risks:
        item["suggestion"] = ""

    improvements = [
        _normalize_review_item(item, _CN_IMPROVEMENTS)
        for item in _extract_review_items(
            data,
            [
                _CN_IMPROVEMENTS,
                _CN_IMPROVEMENTS_ALT,
                "improvements",
                "improvement",
                "improvement_suggestions",
                "suggestions",
                "recommendations",
                "\u4f18\u5316\u5efa\u8bae",
                "\u5b8c\u5584\u5efa\u8bae",
                "\u4fee\u6539\u5efa\u8bae",
                "\u5ba1\u67e5\u5efa\u8bae",
            ],
        )
    ]
    if not _has_meaningful_items(improvements):
        improvements = _extract_improvement_suggestions_from_markdown(raw_markdown)
    if not _has_meaningful_items(improvements):
        improvements = _fallback_improvements_from_risks(risks)

    return {
        "schema_version": SCHEMA_VERSION,
        "contract_type": contract_type,
        "confidence_text": confidence_text,
        "type_source": _first_non_empty([type_detail.get("source"), "result_json"]),
        "stamp_text": stamp_text,
        "stamp_color": stamp_color,
        "key_facts": key_facts,
        "overview": overview,
        "risks": risks,
        "improvements": improvements,
    }


def _render_items_html(title: str, items: List[Dict[str, str]]) -> str:
    if not items:
        return f"<h3>{escape(title)}</h3><div class='empty'>\u672a\u8bc6\u522b\u5230\u76f8\u5173\u5185\u5bb9</div>"
    lines = [f"<h3>{escape(title)}</h3><ol>"]
    for item in items:
        head = escape(_first_non_empty([item.get("title"), title]))
        level = _first_non_empty([item.get("level")])
        problem = _first_non_empty([item.get("problem")])
        suggestion = _first_non_empty([item.get("suggestion")])
        if level:
            head += f" <span class='muted'>({escape(level)})</span>"
        lines.append("<li>")
        lines.append(f"<div><b>{head}</b></div>")
        if problem:
            lines.append(f"<div><b>\u95ee\u9898\uff1a</b>{escape(problem)}</div>")
        if suggestion:
            lines.append(f"<div>{escape(suggestion)}</div>")
        lines.append("</li>")
    lines.append("</ol>")
    return "".join(lines)


def build_report_html(report_payload: Dict[str, Any]) -> str:
    key_facts = report_payload.get("key_facts") or {}
    if not isinstance(key_facts, dict):
        key_facts = {}
    facts_rows = "".join(f"<tr><td class='k'>{escape(str(k))}</td><td>{escape(str(v))}</td></tr>" for k, v in key_facts.items())

    risks = report_payload.get("risks") if isinstance(report_payload.get("risks"), list) else []
    improvements = report_payload.get("improvements") if isinstance(report_payload.get("improvements"), list) else []
    stamp_color = str(report_payload.get("stamp_color") or "#6b7280")

    risks_html = _render_items_html(_CN_RISKS, risks)
    improve_html = _render_items_html(_CN_IMPROVEMENTS, improvements)

    return f"""
<style>
body {{ font-family: 'Microsoft YaHei','PingFang SC',sans-serif; color:#0f172a; }}
.title {{ font-size:24px; font-weight:800; margin:0 0 10px; color:#4b6fae; }}
.meta {{ color:#64748b; margin:2px 0 10px; font-size:13px; }}
.panel {{
  border:1px solid #d6e3e0; border-radius:12px; background:#ffffff;
  padding:12px 14px; margin:0 0 12px;
}}
.panel h3 {{ margin:0 0 8px; color:#2f4b6e; }}
.stamp {{ font-weight:800; color:{stamp_color}; }}
table {{ border-collapse:collapse; width:100%; }}
td {{ padding:7px 6px; border-bottom:1px solid #edf2f7; vertical-align:top; }}
td.k {{ width:120px; color:#475569; font-weight:700; }}
ol {{ margin:8px 0 0; padding-left:20px; }}
li {{ margin:8px 0; line-height:1.6; }}
.muted {{ color:#64748b; font-weight:400; }}
.empty {{ color:#64748b; font-size:13px; }}
</style>
<div class='title'>{_REPORT_TITLE}</div>
<div class='meta'><b>{_CN_CONTRACT_TYPE}\uff1a</b>{escape(str(report_payload.get('contract_type', _DEFAULT_TYPE)))} | <b>{_CN_STAMP}\uff1a</b><span class='stamp'>{escape(str(report_payload.get('stamp_text', _DEFAULT_STAMP)))}</span></div>
<div class='meta'><b>\u7c7b\u578b\u7f6e\u4fe1\u5ea6\uff1a</b>{escape(str(report_payload.get('confidence_text', '-')))} | <b>\u7c7b\u578b\u6765\u6e90\uff1a</b>{escape(str(report_payload.get('type_source', 'result_json')))}</div>
<div class='panel'><h3>{_CN_KEY_FACTS}</h3><table>{facts_rows}</table></div>
<div class='panel'><h3>{_CN_OVERVIEW}</h3><div>{escape(str(report_payload.get('overview', _DEFAULT_OVERVIEW)))}</div></div>
<div class='panel'>{risks_html}</div>
<div class='panel'>{improve_html}</div>
"""


def build_report_markdown(report_payload: Dict[str, Any]) -> str:
    lines = [
        f"# {_REPORT_TITLE}",
        "",
        f"- {_CN_CONTRACT_TYPE}\uff1a{report_payload.get('contract_type', _DEFAULT_TYPE)}",
        f"- {_CN_STAMP}\uff1a{report_payload.get('stamp_text', _DEFAULT_STAMP)}",
        f"- \u7c7b\u578b\u7f6e\u4fe1\u5ea6\uff1a{report_payload.get('confidence_text', '-')}",
        f"- \u7c7b\u578b\u6765\u6e90\uff1a{report_payload.get('type_source', 'result_json')}",
        "",
        f"## {_CN_KEY_FACTS}",
    ]
    key_facts = report_payload.get("key_facts") if isinstance(report_payload.get("key_facts"), dict) else {}
    for key, value in key_facts.items():
        lines.append(f"- {key}\uff1a{value}")

    lines.extend(
        [
            "",
            f"## {_CN_OVERVIEW}",
            str(report_payload.get("overview", _DEFAULT_OVERVIEW)),
            "",
            f"## {_CN_RISKS}",
        ]
    )

    risks = report_payload.get("risks") if isinstance(report_payload.get("risks"), list) else []
    if risks:
        for idx, item in enumerate(risks, 1):
            title = _first_non_empty([item.get("title"), _CN_RISKS]) if isinstance(item, dict) else _CN_RISKS
            problem = item.get("problem") if isinstance(item, dict) else ""
            lines.append(f"{idx}. {title}")
            if problem:
                lines.append(f"   - \u95ee\u9898\uff1a{problem}")
    else:
        lines.append("- \u672a\u8bc6\u522b\u5230\u76f8\u5173\u5185\u5bb9")

    lines.extend(["", f"## {_CN_IMPROVEMENTS}"])
    improvements = report_payload.get("improvements") if isinstance(report_payload.get("improvements"), list) else []
    if improvements:
        for idx, item in enumerate(improvements, 1):
            suggestion = item.get("suggestion") if isinstance(item, dict) else ""
            title = _first_non_empty([item.get("title"), _CN_IMPROVEMENTS]) if isinstance(item, dict) else _CN_IMPROVEMENTS
            lines.append(f"{idx}. {title}")
            if suggestion:
                lines.append(f"   - {suggestion}")
    else:
        lines.append("- \u672a\u8bc6\u522b\u5230\u76f8\u5173\u5185\u5bb9")

    return "\n".join(lines).strip()

