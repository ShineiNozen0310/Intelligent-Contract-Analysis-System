import os
import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Optional

from openai import OpenAI

BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=BASE_URL,
    )

SYSTEM_JSON_RULE = """你是资深合同审查助手。你必须只输出 JSON（不要 markdown，不要多余文字，不要代码块）。
请严格按下面 schema 输出（字段名必须一致）：

{
  "合同类型": "字符串：优先写二级类型（更具体），如无法确定就写“未知/其他”",
  "合同类型明细": {
    "type_l1": "一级类型（如 采购/租赁/服务/授权/工程/劳动/合作 等）",
    "type_l2": "二级类型（如 技术服务/软件开发/设备采购/房屋租赁 等）",
    "labels": ["一级类型","二级类型"],
    "confidence": 0.0,
    "evidence": [
      {"quote":"证据原文片段","where":"出处位置（如 第2页/第3条/标题）"}
    ],
    "alternatives": [
      {"type_l2":"备选类型","confidence":0.12}
    ],
    "need_info": "若不确定，请给出缺失信息或需要补充的条款/页面"
  },
  "审查概述": "一段中文概述，包含主要法律风险与缺失要件（多句话）",
  "风险点": [
    {"title":"风险点标题","level":"高/中/低/不确定","problem":"问题描述","suggestion":"修改建议/补充条款建议"}
  ],
  "改进措施": [
    {"title":"改进项标题","problem":"问题描述","suggestion":"修改建议/补充条款建议"}
  ],
  "key_facts": {
    "合同名称":"必须是合同原文中的真实标题/名称（优先书名号《》或文首标题），不要用你推断的“合同类型”；找不到写“未提及”",
    "甲方":"从合同中抽取，找不到写“未提及”",
    "乙方":"从合同中抽取，找不到写“未提及”",
    "金额":"从合同中抽取，找不到写“未提及”",
    "期限":"从合同中抽取，找不到写“未提及”"
  }
}

要求：
1) 风险点尽量>= 5 条；不足也输出你能识别到的风险/缺失要素。
2) key_facts 若无法明确抽取，填“未提及”。
3) 只输出 JSON 本体。
4) 合同类型必须给出 evidence 佐证；若 confidence < 0.6，请将“合同类型”置为“未知/其他”，并给出 alternatives + need_info。
5) type_l1/type_l2 必须从给定 taxonomy 中选择；若不匹配则使用“未知/其他”。
6) 报告只允许输出“与合同内容相关”的风险与修改建议；禁止输出系统、模型、OCR、识别噪声、技术缺陷类问题。
7) 输出语言必须为简体中文。除合同原文中依法必须保留的专有名词、条款编号、金额、百分比外，禁止输出英文句子或英文短语。
8) 若出现英文表达，必须在同字段内改写为中文法律表述，不得仅保留英文。
"""

CN_REWRITE_SYSTEM_RULE = """你是合同审查结果“中文化规整”助手。你必须只输出 JSON 本体。
任务：
1) 将输入 JSON 中的英文句子/英文短语改写为简体中文法律表述。
2) 保持原 JSON 结构、字段名和关键信息不变（数值、比例、金额、条款编号、主体名称不要改错）。
3) 不要新增技术类缺陷描述（如OCR噪声、模型问题等）。
4) 输出仍需是可解析的 JSON 对象，不能有 markdown 和解释文字。
"""

_EN_FRAGMENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9%/._-]{2,}")
_NARRATIVE_ITEM_KEYS = (
    "title",
    "problem",
    "issue",
    "description",
    "suggestion",
    "advice",
    "fix",
    "solution",
    "建议",
    "修改建议",
)
_COMMON_EN_CN_REPLACEMENTS = (
    (r"\bcapped at\b", "上限为"),
    (r"\boverdue amount\b", "逾期金额"),
    (r"\boverdue\b", "逾期"),
    (r"\bamount\b", "金额"),
    (r"\bliquidated damages?\b", "违约金"),
    (r"\bpenalt(?:y|ies)\b", "违约责任"),
    (r"\bservice fee\b", "服务费"),
    (r"\bpayment terms?\b", "付款条款"),
    (r"\bper day\b", "按日"),
    (r"\bper month\b", "按月"),
    (r"\bforce majeure\b", "不可抗力"),
    (r"\btermination\b", "解除"),
    (r"\bdefault\b", "违约"),
    (r"\bof\b", "的"),
    (r"\band\b", "且"),
    (r"\bor\b", "或"),
)


def _pick_title(lines: List[str]) -> str:
    for line in lines:
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^#{1,6}\s*", "", s).strip()
        if 2 <= len(s) <= 80:
            return s
    return ""


def _pick_headings(lines: List[str], max_n: int = 20) -> List[str]:
    out: List[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            s = re.sub(r"^#{1,6}\s*", "", s).strip()
            if s:
                out.append(s[:120])
                if len(out) >= max_n:
                    break
    return out


def _pick_key_paragraphs(text: str, max_n: int = 8) -> List[str]:
    keywords = [
        "定义",
        "目的",
        "合作",
        "服务内容",
        "交付",
        "验收",
        "费用",
        "付款",
        "价款",
        "结算",
        "范围",
        "期限",
        "保密",
        "知识产权",
        "争议解决",
        "违约",
    ]
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    picked: List[str] = []
    for p in paras:
        if any(k in p for k in keywords):
            picked.append(p[:500])
        if len(picked) >= max_n:
            break
    return picked


def build_type_clues(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    title = _pick_title(lines)
    headings = _pick_headings(lines)

    head_lines = []
    for line in lines:
        if line.strip():
            head_lines.append(line.strip())
        if len(head_lines) >= 80:
            break
    head_preview = "\n".join(head_lines)

    key_paras = _pick_key_paragraphs(markdown_text)

    sections: List[str] = []
    if title:
        sections.append(f"【合同名称/标题】\n{title}")
    if head_preview:
        sections.append(f"【前若干行摘要】\n{head_preview[:2000]}")
    if headings:
        sections.append("【关键标题】\n" + "\n".join(f"- {h}" for h in headings))
    if key_paras:
        sections.append("【关键条款摘录】\n" + "\n\n".join(key_paras))

    clues = "\n\n".join(sections).strip()
    return clues[:4000]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _truncate_for_prompt(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "\n\n...[TRUNCATED_FOR_SPEED]...\n\n"
    head = int(max_chars * 0.75)
    tail = max(0, max_chars - head - len(marker))
    if tail <= 0:
        return text[:max_chars]
    return text[:head] + marker + text[-tail:]


@lru_cache(maxsize=1)
def load_taxonomy() -> Dict[str, Any]:
    default_path = Path(__file__).resolve().parents[1] / "contract_type_taxonomy.json"
    env_path = os.getenv("CONTRACT_TYPE_TAXONOMY_PATH")
    path = Path(env_path) if env_path else default_path
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "服务": ["技术服务"],
        "采购": ["设备采购"],
        "租赁": ["房屋租赁"],
        "其他": ["未知/其他"],
    }


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("model response is not a JSON object")
    except Exception:
        # Fallback: some responses may wrap JSON with extra text.
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            raise ValueError("extracted payload is not a JSON object")
        return data


def _call_qwen_json(messages: List[Dict[str, str]], req_timeout: int) -> Dict[str, Any]:
    retries = max(1, _env_int("QWEN_API_RETRY", 2))
    last_exc: Exception | None = None
    client = get_client()

    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=os.getenv("QWEN_MODEL", "qwen-plus"),
                messages=messages,
                temperature=float(os.getenv("QWEN_TEMPERATURE", "0.2")),
                response_format={"type": "json_object"},
                timeout=req_timeout,
            )
            content = (resp.choices[0].message.content or "").strip()
            return _extract_json_object(content)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(4.0, 0.8 * attempt))

    raise RuntimeError(f"Qwen request failed after {retries} attempts: {last_exc}")


def _call_qwen_text(messages: List[Dict[str, str]], req_timeout: int) -> str:
    retries = max(1, _env_int("QWEN_API_RETRY", 2))
    last_exc: Exception | None = None
    client = get_client()

    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=os.getenv("QWEN_MODEL", "qwen-plus"),
                messages=messages,
                temperature=_env_float("QWEN_TEMPERATURE", 0.2),
                timeout=req_timeout,
            )
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                raise RuntimeError("empty model response")
            return content
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(4.0, 0.8 * attempt))

    raise RuntimeError(f"Qwen text request failed after {retries} attempts: {last_exc}")


def _normalize_narrative_cn_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    for pat, repl in _COMMON_EN_CN_REPLACEMENTS:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _contains_english_fragment(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    for token in _EN_FRAGMENT_RE.findall(s):
        lower = token.lower()
        # URLs / file paths are not report narrative.
        if lower.startswith(("http://", "https://", "www.")):
            continue
        return True
    return False


def _normalize_review_language_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data

    def _normalize_node(node: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(node)
        for key in ("审查概述", "overview", "summary"):
            if isinstance(out.get(key), str):
                out[key] = _normalize_narrative_cn_text(out[key])

        for key in ("风险点", "改进措施", "risks", "risk_points", "improvements", "suggestions", "recommendations"):
            items = out.get(key)
            if not isinstance(items, list):
                continue
            cleaned = []
            for item in items:
                if isinstance(item, str):
                    cleaned.append(_normalize_narrative_cn_text(item))
                    continue
                if isinstance(item, dict):
                    d = dict(item)
                    for k in _NARRATIVE_ITEM_KEYS:
                        if isinstance(d.get(k), str):
                            d[k] = _normalize_narrative_cn_text(d[k])
                    cleaned.append(d)
                    continue
                cleaned.append(item)
            out[key] = cleaned
        return out

    root = _normalize_node(data)
    for key in ("result", "review", "data"):
        node = root.get(key)
        if isinstance(node, dict):
            root[key] = _normalize_node(node)
    return root


def _collect_narrative_texts(data: Dict[str, Any]) -> List[str]:
    if not isinstance(data, dict):
        return []

    out: List[str] = []

    def _collect_node(node: Dict[str, Any]) -> None:
        for key in ("审查概述", "overview", "summary"):
            v = node.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())

        for key in ("风险点", "改进措施", "risks", "risk_points", "improvements", "suggestions", "recommendations"):
            items = node.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                    continue
                if isinstance(item, dict):
                    for k in _NARRATIVE_ITEM_KEYS:
                        v = item.get(k)
                        if isinstance(v, str) and v.strip():
                            out.append(v.strip())

    _collect_node(data)
    for key in ("result", "review", "data"):
        node = data.get(key)
        if isinstance(node, dict):
            _collect_node(node)
    return out


def _english_fragment_count(data: Dict[str, Any]) -> int:
    count = 0
    for text in _collect_narrative_texts(data):
        for token in _EN_FRAGMENT_RE.findall(text):
            lower = token.lower()
            if lower.startswith(("http://", "https://", "www.")):
                continue
            count += 1
    return count


def _rewrite_review_json_to_chinese(data: Dict[str, Any], req_timeout: int) -> Dict[str, Any]:
    payload = json.dumps(data, ensure_ascii=False)
    messages = [
        {"role": "system", "content": CN_REWRITE_SYSTEM_RULE},
        {
            "role": "user",
            "content": (
                "请将下列审查结果 JSON 中所有英文自然语言改写为简体中文；"
                "保持结构和字段不变，只改写文本表达：\n\n"
                f"{payload}"
            ),
        },
    ]
    rewritten = _call_qwen_json(messages=messages, req_timeout=req_timeout)
    if not isinstance(rewritten, dict):
        raise ValueError("rewrite payload is not a JSON object")
    return rewritten


def _contains_ocr_noise_claim(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    keys = [
        "ocr",
        "识别错误",
        "识别误差",
        "噪声",
        "乱码",
        "错字",
        "误识",
        "误作",
        "应为",
        "技术缺陷",
        "系统问题",
    ]
    return any(k in t for k in keys)


def _item_text_blob(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts = [
            str(item.get("title", "") or ""),
            str(item.get("problem", "") or ""),
            str(item.get("issue", "") or ""),
            str(item.get("description", "") or ""),
            str(item.get("suggestion", "") or ""),
            str(item.get("advice", "") or ""),
            str(item.get("fix", "") or ""),
            str(item.get("solution", "") or ""),
            str(item.get("建议", "") or ""),
            str(item.get("修改建议", "") or ""),
        ]
        return " ".join(parts)
    return str(item)


def _has_legal_clause_evidence(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    blob = _item_text_blob(item)
    if not blob:
        return False
    # requires clause/article anchors to keep OCR-related claim
    return bool(re.search(r"(第[一二三四五六七八九十百千0-9]+条|第[一二三四五六七八九十百千0-9]+款|条款|义务|违约|付款|验收|交付|争议)", blob))


def _filter_ocr_noise_items(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    hide_tech_defects = _env_flag("REPORT_HIDE_TECH_DEFECTS", True)
    out = []
    for item in items:
        blob = _item_text_blob(item)
        if _contains_ocr_noise_claim(blob):
            if hide_tech_defects:
                continue
            if not _has_legal_clause_evidence(item):
                continue
        if isinstance(item, str) and hide_tech_defects and re.search(r"(系统|模型|技术|识别|OCR|噪声|乱码)", item, re.IGNORECASE):
            continue
        out.append(item)
    return out




def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_non_empty_text(values: List[Any]) -> str:
    for value in values:
        if isinstance(value, str):
            s = value.strip()
            if s:
                return s
    return ""


def _clean_suggestion_text(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    # Remove "risk-point" references and keep plain recommendation text.
    s = re.sub(r"^\s*(?:\u9488\u5bf9|\u5173\u4e8e)?\s*\u98ce\u9669\u70b9\s*[:\uff1a#\-]?\s*\d*\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\u98ce\u9669\u70b9\s*[:\uff1a]?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bRISK[-_ ]?\d+\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" \uff1a:，,；;。")


def _normalize_improvement_item(item: Any, idx: int) -> Dict[str, Any] | None:
    if isinstance(item, str):
        suggestion = _clean_suggestion_text(item)
        if not suggestion:
            return None
        return {"title": f"\u5efa\u8bae{idx}", "problem": "", "suggestion": suggestion}
    if isinstance(item, dict):
        suggestion = _first_non_empty_text(
            [
                item.get("suggestion"),
                item.get("advice"),
                item.get("fix"),
                item.get("solution"),
                item.get("\u5efa\u8bae"),
                item.get("\u4fee\u6539\u5efa\u8bae"),
            ]
        )
        suggestion = _clean_suggestion_text(suggestion)
        if not suggestion:
            return None
        return {"title": f"\u5efa\u8bae{idx}", "problem": "", "suggestion": suggestion}
    return None


def _enforce_risk_suggestion_split(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data

    risk_keys = ("\u98ce\u9669\u70b9", "risks", "risk_points")
    improve_keys = (
        "\u6539\u8fdb\u63aa\u65bd",
        "\u6539\u8fdb\u5efa\u8bae",
        "improvements",
        "improvement",
        "improvement_suggestions",
        "suggestions",
        "recommendations",
    )
    suggestion_keys = ("suggestion", "advice", "fix", "solution", "\u5efa\u8bae", "\u4fee\u6539\u5efa\u8bae")

    def _process_node(node: Dict[str, Any]) -> Dict[str, Any]:
        out_node = dict(node)

        risk_key_found = None
        for key in risk_keys:
            if key in out_node and isinstance(out_node.get(key), list):
                risk_key_found = key
                break
        risks = _as_list(out_node.get(risk_key_found)) if risk_key_found else []

        moved_suggestions: List[str] = []
        normalized_risks: List[Any] = []
        for item in risks:
            if not isinstance(item, dict):
                normalized_risks.append(item)
                continue
            risk_item = dict(item)
            moved = _first_non_empty_text([risk_item.get(k) for k in suggestion_keys])
            moved = _clean_suggestion_text(moved)
            if moved:
                moved_suggestions.append(moved)
            for key in suggestion_keys:
                if key in risk_item:
                    risk_item[key] = ""
            normalized_risks.append(risk_item)

        if risk_key_found:
            out_node[risk_key_found] = normalized_risks
            if risk_key_found != "\u98ce\u9669\u70b9":
                out_node["\u98ce\u9669\u70b9"] = normalized_risks
            if risk_key_found != "risks":
                out_node["risks"] = normalized_risks

        improvement_raw: List[Any] = []
        for key in improve_keys:
            value = out_node.get(key)
            if isinstance(value, list):
                improvement_raw.extend(value)
        improvement_raw.extend(moved_suggestions)

        ordered_items: List[Dict[str, Any]] = []
        seen = set()
        idx = 1
        for raw in improvement_raw:
            normalized = _normalize_improvement_item(raw, idx=idx)
            if not normalized:
                continue
            dedupe_key = str(normalized.get("suggestion", "")).strip().lower()
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized["title"] = f"\u5efa\u8bae{idx}"
            ordered_items.append(normalized)
            idx += 1

        out_node["\u6539\u8fdb\u63aa\u65bd"] = ordered_items
        out_node["improvements"] = ordered_items
        return out_node

    root = _process_node(data)
    for key in ("result", "review", "data"):
        node = root.get(key)
        if isinstance(node, dict):
            root[key] = _process_node(node)
    return root

def _postprocess_review_json(data: Dict[str, Any], force_chinese: Optional[bool] = None) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return data

    def _filter_node(node: Dict[str, Any]) -> Dict[str, Any]:
        out_node = dict(node)
        for key in ("风险点", "改进措施", "risks", "risk_points", "improvements", "suggestions", "recommendations"):
            if key in out_node:
                out_node[key] = _filter_ocr_noise_items(out_node.get(key))
        return out_node

    out = _filter_node(data)
    for root in ("result", "review", "data"):
        node = out.get(root)
        if isinstance(node, dict):
            out[root] = _filter_node(node)

    out = _normalize_review_language_fields(out)

    # 强制中文：若仍出现英文碎片，自动做一轮“JSON中文化重写”。
    should_force_chinese = _env_flag("REVIEW_FORCE_CHINESE", True) if force_chinese is None else bool(force_chinese)
    if should_force_chinese:
        en_count = _english_fragment_count(out)
        if en_count > 0:
            rewrite_timeout = _env_int("REVIEW_CN_REWRITE_TIMEOUT", 90)
            try:
                rewritten = _rewrite_review_json_to_chinese(out, req_timeout=rewrite_timeout)
                rewritten = _normalize_review_language_fields(rewritten)
                rewritten = _filter_node(rewritten)
                for root in ("result", "review", "data"):
                    node = rewritten.get(root)
                    if isinstance(node, dict):
                        rewritten[root] = _filter_node(node)

                if _english_fragment_count(rewritten) <= en_count:
                    out = rewritten
            except Exception:
                # 保底：重写失败时保留原结果，不中断审查流程。
                pass
    out = _enforce_risk_suggestion_split(out)
    return out


OCR_FIX_SYSTEM_RULE = """你是合同OCR纠错助手。你的任务是只做“文本纠错与规整”，不做总结，不做分析，不改变原始语义。
输出要求：
1) 只输出修正后的正文文本，不要JSON，不要markdown代码块，不要解释。
2) 保留原有段落结构与行序；禁止扩写、删减关键条款。
3) 修正常见OCR噪声（错字、漏字、断裂词、异常符号、金额与编号格式错误），尤其是合同义务、金额、日期、主体名称、条款编号。
4) 如果无法确定某处字符，保留原文，不要猜测。
"""


def qwen_fix_ocr_text(raw_text: str) -> str:
    if not (os.getenv("DASHSCOPE_API_KEY") or "").strip():
        return raw_text
    if not _env_flag("OCR_LLM_FIX_ENABLED", True):
        return raw_text

    fix_max_chars = _env_int("OCR_LLM_FIX_MAX_CHARS", 90000)
    req_timeout = _env_int("OCR_LLM_FIX_TIMEOUT", 90)
    prompt_text = _truncate_for_prompt(raw_text, fix_max_chars)

    messages = [
        {"role": "system", "content": OCR_FIX_SYSTEM_RULE},
        {"role": "user", "content": prompt_text},
    ]
    fixed = _call_qwen_text(messages=messages, req_timeout=req_timeout).strip()
    if not fixed:
        return raw_text

    # avoid accidental over-truncation/summarization
    raw_len = len((raw_text or "").strip())
    fixed_len = len(fixed)
    if raw_len > 0 and fixed_len < max(120, int(raw_len * 0.45)):
        return raw_text
    return fixed


def qwen_plus_review(markdown_text: str) -> dict:
    if not (os.getenv("DASHSCOPE_API_KEY") or "").strip():
        raise RuntimeError("DASHSCOPE_API_KEY is missing")

    prompt_max_chars = _env_int("QWEN_PROMPT_TEXT_MAX_CHARS", 80000)
    prompt_text = _truncate_for_prompt(markdown_text, prompt_max_chars)
    type_clues = build_type_clues(markdown_text)
    taxonomy = load_taxonomy()
    taxonomy_json = json.dumps(taxonomy, ensure_ascii=False)
    req_timeout = _env_int("QWEN_API_TIMEOUT", 120)

    messages = [
        {"role": "system", "content": SYSTEM_JSON_RULE},
        {
            "role": "user",
            "content": (
                "以下是合同类型判别参考信息（已挑选高信息密度片段）：\n\n"
                f"{type_clues}\n\n"
                "以下是可选合同类型 taxonomy（必须从中选择 type_l1/type_l2，若不匹配请用未知/其他）：\n\n"
                f"{taxonomy_json}\n\n"
                "以下是合同内容（Markdown，可能有 OCR 噪声）：\n\n"
                f"{prompt_text}"
            ),
        },
    ]

    result = _call_qwen_json(messages=messages, req_timeout=req_timeout)
    return _postprocess_review_json(result)
