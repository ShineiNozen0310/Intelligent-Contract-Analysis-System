from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Tuple

from openai import OpenAI
import requests

from .llm_client import (
    _extract_json_object,
    _postprocess_review_json,
    _truncate_for_prompt,
    build_type_clues,
    load_taxonomy,
    qwen_fix_ocr_text,
    qwen_plus_review,
)


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _postprocess_local_review_json(data: Dict[str, Any]) -> Dict[str, Any]:
    # Reuse existing postprocess rules while preventing implicit remote rewrite call.
    return _postprocess_review_json(data, force_chinese=False)


class BaseLLMClient:
    name = "base"

    def review_contract(self, markdown_text: str) -> Dict[str, Any]:
        raise NotImplementedError

    def fix_ocr_text(self, raw_text: str) -> str:
        raise NotImplementedError


class RemoteLLMClient(BaseLLMClient):
    name = "remote"

    def review_contract(self, markdown_text: str) -> Dict[str, Any]:
        return qwen_plus_review(markdown_text)

    def fix_ocr_text(self, raw_text: str) -> str:
        return qwen_fix_ocr_text(raw_text)


@dataclass(frozen=True)
class LocalVLLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: int
    max_retries: int
    max_tokens: int
    temperature: float
    input_char_limit: int
    prompt_text_max_chars: int
    ocr_fix_max_chars: int
    context_window: int
    min_output_tokens: int
    context_safety_margin: int
    overview_min_chars: int
    overview_max_chars: int
    disable_thinking: bool
    healthcheck_enabled: bool
    healthcheck_timeout_s: int
    unhealthy_cooldown_s: int

    @staticmethod
    def from_env() -> "LocalVLLMConfig":
        model_raw = (os.environ.get("LOCAL_VLLM_MODEL") or "./hf_models/Qwen3-8B-AWQ").strip()
        served_model = (os.environ.get("LOCAL_VLLM_SERVED_MODEL") or model_raw).strip()
        return LocalVLLMConfig(
            base_url=(os.environ.get("LOCAL_VLLM_BASE_URL") or "http://127.0.0.1:8002/v1").strip(),
            api_key=(os.environ.get("LOCAL_VLLM_API_KEY") or "dummy").strip(),
            model=served_model,
            timeout_s=_env_int("LOCAL_VLLM_TIMEOUT", 60),
            max_retries=max(0, _env_int("LOCAL_VLLM_MAX_RETRIES", 0)),
            max_tokens=max(16, _env_int("LOCAL_VLLM_MAX_TOKENS", 48)),
            temperature=_env_float("LOCAL_VLLM_TEMPERATURE", 0.1),
            input_char_limit=max(300, _env_int("LOCAL_VLLM_INPUT_CHAR_LIMIT", 800)),
            prompt_text_max_chars=max(240, _env_int("LOCAL_VLLM_PROMPT_TEXT_MAX_CHARS", 700)),
            ocr_fix_max_chars=max(240, _env_int("LOCAL_VLLM_OCR_FIX_MAX_CHARS", 700)),
            context_window=max(128, _env_int("LOCAL_VLLM_CONTEXT_WINDOW", _env_int("VLLM_MAX_MODEL_LEN", 256))),
            min_output_tokens=max(8, _env_int("LOCAL_VLLM_MIN_OUTPUT_TOKENS", 24)),
            context_safety_margin=max(8, _env_int("LOCAL_VLLM_CONTEXT_SAFETY_MARGIN", 16)),
            overview_min_chars=max(120, _env_int("LOCAL_VLLM_OVERVIEW_MIN_CHARS", 200)),
            overview_max_chars=max(200, _env_int("LOCAL_VLLM_OVERVIEW_MAX_CHARS", 400)),
            disable_thinking=_env_flag("LOCAL_VLLM_DISABLE_THINKING", True),
            healthcheck_enabled=_env_flag("LOCAL_VLLM_HEALTHCHECK_ENABLED", True),
            healthcheck_timeout_s=max(1, _env_int("LOCAL_VLLM_HEALTHCHECK_TIMEOUT", 2)),
            unhealthy_cooldown_s=max(5, _env_int("LOCAL_VLLM_UNHEALTHY_COOLDOWN", 45)),
        )


@lru_cache(maxsize=4)
def _get_local_openai_client(base_url: str, api_key: str, max_retries: int) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)


class LocalVLLMClient(BaseLLMClient):
    name = "local_vllm"

    def __init__(self, config: LocalVLLMConfig) -> None:
        self.cfg = config
        self.client = _get_local_openai_client(config.base_url, config.api_key, config.max_retries)
        self._unhealthy_until = 0.0

    def _models_url(self) -> str:
        base = self.cfg.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base + "/models"
        return base + "/v1/models"

    def _auth_headers(self) -> Dict[str, str]:
        key = (self.cfg.api_key or "").strip()
        if not key:
            return {}
        return {"Authorization": f"Bearer {key}"}

    def _mark_unhealthy(self, reason: str) -> None:
        self._unhealthy_until = time.time() + self.cfg.unhealthy_cooldown_s
        print(
            f"[llm] local_vllm marked unhealthy for {self.cfg.unhealthy_cooldown_s}s: {reason}",
            flush=True,
        )

    def _extract_status_code(self, exc: Exception) -> int | None:
        for attr in ("status_code", "http_status", "code"):
            val = getattr(exc, attr, None)
            if isinstance(val, int):
                return val
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
            if isinstance(status, int):
                return status
        text = str(exc)
        for code in ("502", "503", "504", "500"):
            if code in text:
                try:
                    return int(code)
                except Exception:
                    pass
        return None

    def _is_server_side_failure(self, exc: Exception) -> bool:
        status = self._extract_status_code(exc)
        if status is not None and status >= 500:
            return True
        text = str(exc).lower()
        return ("bad gateway" in text) or ("gateway" in text and "502" in text)

    def _preflight_health(self) -> None:
        now = time.time()
        if now < self._unhealthy_until:
            wait_s = int(self._unhealthy_until - now)
            raise RuntimeError(f"local vllm in cooldown ({wait_s}s remaining)")

        if not self.cfg.healthcheck_enabled:
            return
        try:
            r = requests.get(
                self._models_url(),
                headers=self._auth_headers(),
                timeout=self.cfg.healthcheck_timeout_s,
            )
            if r.status_code >= 500:
                self._mark_unhealthy(f"health endpoint {r.status_code}")
                raise RuntimeError(f"local vllm health failed: status={r.status_code}")
            if r.status_code >= 400:
                raise RuntimeError(f"local vllm health unexpected status={r.status_code}")
        except Exception as exc:
            if self._is_server_side_failure(exc) or "health failed" in str(exc):
                raise
            self._mark_unhealthy(f"health probe exception: {exc}")
            raise RuntimeError(f"local vllm health probe failed: {exc}") from exc

    def _compact_taxonomy(self) -> str:
        taxonomy = load_taxonomy()
        if not isinstance(taxonomy, dict):
            return "{}"
        small_ctx = self.cfg.input_char_limit <= 1200
        max_types = 6 if small_ctx else 10
        max_labels = 4 if small_ctx else 8
        compact: Dict[str, List[str]] = {}
        for idx, (k, v) in enumerate(taxonomy.items()):
            if idx >= max_types:
                break
            if isinstance(v, list):
                compact[str(k)] = [str(x) for x in v[:max_labels]]
            else:
                compact[str(k)] = [str(v)]
        return json.dumps(compact, ensure_ascii=False)

    def _segment_for_small_context(self, text: str) -> str:
        src = (text or "").strip()
        limit = self.cfg.input_char_limit
        if len(src) <= limit:
            return src

        clues = build_type_clues(src)
        # Keep first/middle/last slices so local model sees distributed evidence.
        chunk_size = max(240, min(1200, limit // 3))
        chunks = [src[i : i + chunk_size] for i in range(0, len(src), chunk_size)]
        picked: List[str] = []
        if chunks:
            picked.append(chunks[0])
        if len(chunks) > 2 and limit > 2000:
            picked.append(chunks[len(chunks) // 2])
        if len(chunks) > 1:
            picked.append(chunks[-1])

        pieces: List[str] = []
        for idx, seg in enumerate(picked, start=1):
            seg_clip = _truncate_for_prompt(seg, max(120, min(600, chunk_size)))
            pieces.append(f"[SEGMENT {idx}/{len(picked)}]\n{seg_clip}")

        clue_budget = 300 if limit <= 1200 else 1500
        merged = f"[TYPE CLUES]\n{clues[:clue_budget]}\n\n" + "\n\n".join(pieces)
        return _truncate_for_prompt(merged, limit)

    def _estimate_text_tokens(self, text: str) -> int:
        src = (text or "").strip()
        if not src:
            return 0
        ascii_chars = sum(1 for ch in src if ord(ch) < 128)
        non_ascii_chars = len(src) - ascii_chars
        punctuation = len(re.findall(r"[,:;{}\[\]\n]", src))
        return max(1, non_ascii_chars + math.ceil(ascii_chars / 4) + punctuation)

    def _estimate_messages_tokens(self, messages: List[Dict[str, str]]) -> int:
        total = 6
        for msg in messages:
            total += 8
            total += self._estimate_text_tokens(msg.get("content", ""))
        return total

    def _fit_messages_to_context(
        self,
        messages: List[Dict[str, str]],
        desired_max_tokens: int,
    ) -> Tuple[List[Dict[str, str]], int]:
        fitted = [dict(msg) for msg in messages]
        max_tokens = min(max(8, desired_max_tokens), max(8, self.cfg.context_window // 3))
        max_tokens = max(self.cfg.min_output_tokens, max_tokens)
        user_indexes = [idx for idx, msg in enumerate(fitted) if msg.get("role") == "user"]

        for _ in range(8):
            input_budget = self.cfg.context_window - max_tokens - self.cfg.context_safety_margin
            estimate = self._estimate_messages_tokens(fitted)
            if estimate <= input_budget:
                return fitted, max_tokens

            if max_tokens > self.cfg.min_output_tokens:
                max_tokens = max(self.cfg.min_output_tokens, max_tokens - 8)
                continue

            if not user_indexes:
                break

            over = estimate - input_budget
            changed = False
            for idx in user_indexes:
                content = fitted[idx].get("content", "")
                if not content:
                    continue
                current_tokens = self._estimate_text_tokens(content)
                if current_tokens <= 48:
                    continue
                keep_ratio = max(0.35, min(0.95, (current_tokens - over) / max(current_tokens, 1)))
                next_chars = max(80, int(len(content) * keep_ratio * 0.9))
                next_content = _truncate_for_prompt(content, next_chars)
                if next_content != content:
                    fitted[idx]["content"] = next_content
                    changed = True
            if not changed:
                break

        return fitted, max(8, min(max_tokens, self.cfg.max_tokens))

    def _strip_think_content(self, text: str) -> str:
        src = (text or "").strip()
        if not src:
            return ""
        # Qwen3 may emit reasoning block first; remove it before parsing payload.
        if src.startswith("<think>"):
            end = src.find("</think>")
            if end >= 0:
                src = src[end + len("</think>") :].strip()
            else:
                # Unclosed think block means no usable answer payload.
                return ""
        return src

    def _chat_extra_body(self) -> Dict[str, Any] | None:
        if not self.cfg.disable_thinking:
            return None
        # Qwen3 templates support enable_thinking flag in vLLM OpenAI-compatible endpoint.
        return {"chat_template_kwargs": {"enable_thinking": False}}

    def _is_json_parse_failure(self, exc: Exception) -> bool:
        text = str(exc).strip().lower()
        if not text:
            return False
        markers = (
            "unterminated string",
            "expecting value",
            "expecting ',' delimiter",
            "extra data",
            "empty model response",
            "model response is not a json object",
            "json",
        )
        return any(marker in text for marker in markers)

    def _build_review_messages(
        self,
        compact_text: str,
        taxonomy: str,
        aggressive: bool = False,
    ) -> List[Dict[str, str]]:
        if aggressive:
            return [
                {
                    "role": "system",
                    "content": (
                        "JSON only. "
                        "Schema: {\"contract_type\":\"\",\"overview\":\"\",\"risks\":[],"
                        "\"improvements\":[],\"key_facts\":[]}. "
                        "Chinese only. Arrays max 4 items. "
                        "overview target 200-400 Chinese chars. "
                        "Each risk/improvement should be specific and actionable."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "contract:\n"
                        f"{compact_text}\n\n"
                        "Return valid JSON only."
                    ),
                },
            ]

        return [
            {
                "role": "system",
                "content": (
                    "Return JSON only. "
                    "Keys: contract_type, overview, risks, improvements, key_facts. "
                    "Max 5 risks, max 5 improvements. "
                    "overview should be 200-400 Chinese chars. "
                    "Each risk/improvement should be concrete and not generic."
                ),
            },
            {
                "role": "user",
                "content": (
                    "taxonomy:\n"
                    f"{taxonomy}\n\n"
                    "contract_markdown:\n"
                    f"{compact_text}"
                ),
            },
        ]

    def _build_text_fallback_messages(self, compact_text: str) -> List[Dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Output exactly 5 lines in Chinese. "
                    "No JSON. No markdown. No explanation. "
                    "Format:\n"
                    "contract_type: ...\n"
                    "overview: ...\n"
                    "risks: item1 | item2\n"
                    "improvements: item1 | item2\n"
                    "key_facts: 合同名称=...;甲方=...;乙方=...;金额=...;期限=..."
                ),
            },
            {
                "role": "user",
                "content": (
                    "请基于以下合同片段给出极简审查结果。\n"
                    "每个风险和建议都要具体，不少于20字。\n"
                    "若无法确定请写未提及。\n\n"
                    f"{compact_text}"
                ),
            },
        ]

    def _parse_pipe_list(self, text: str) -> List[str]:
        items: List[str] = []
        for piece in re.split(r"[|；;]", text or ""):
            val = re.sub(r"^[\-\d\.\)\(、\s]+", "", piece).strip()
            if val:
                items.append(val[:220])
        return items[:4]

    def _parse_key_facts_text(self, text: str) -> Dict[str, str]:
        out = {
            "合同名称": "未提及",
            "甲方": "未提及",
            "乙方": "未提及",
            "金额": "未提及",
            "期限": "未提及",
        }
        raw = (text or "").strip()
        if not raw:
            return out
        for part in re.split(r"[;；]", raw):
            if "=" in part:
                key, value = part.split("=", 1)
            elif "：" in part:
                key, value = part.split("：", 1)
            elif ":" in part:
                key, value = part.split(":", 1)
            else:
                continue
            k = key.strip()
            v = value.strip() or "未提及"
            if k in out:
                out[k] = v[:80]
        return out

    def _rule_extract(self, patterns: List[str], text: str, default: str = "未提及") -> str:
        src = text or ""
        for pattern in patterns:
            m = re.search(pattern, src, re.IGNORECASE)
            if not m:
                continue
            value = (m.group(1) if m.lastindex else m.group(0)).strip()
            value = re.sub(r"\s+", " ", value)
            if value:
                return value[:120]
        return default

    def _contains_any(self, text: str, keywords: List[str]) -> bool:
        src = text or ""
        for kw in keywords:
            if kw and kw in src:
                return True
        return False

    def _guess_contract_name(self, text: str) -> str:
        lines = [re.sub(r"\s+", " ", line.strip()) for line in (text or "").splitlines()]
        lines = [line for line in lines if len(line) >= 4]
        if not lines:
            return "未提及"

        explicit_name_patterns = [
            re.compile(r"[《<【][^》>】]{2,80}(?:合同|协议)[》>】]"),
            re.compile(r"(?:^|[\s:：])([^\n\r]{2,80}(?:合同|协议))$"),
        ]

        for line in lines[:60]:
            if re.search(r"合同编号|项目编号|招标编号|采购编号", line):
                continue
            for pattern in explicit_name_patterns:
                m = pattern.search(line)
                if m:
                    value = (m.group(1) if m.lastindex else m.group(0)).strip()
                    if value:
                        return value[:80]

        for line in lines[:80]:
            if "合同" in line and len(line) <= 80 and not re.search(r"合同编号|项目编号|招标编号|采购编号", line):
                return line[:80]
        return "未提及"

    def _guess_contract_type(self, text: str) -> str:
        src = text or ""
        rules = [
            ("政府采购", "政府采购服务合同"),
            ("技术服务", "技术服务合同"),
            ("服务", "服务合同"),
            ("采购", "采购合同"),
            ("租赁", "租赁合同"),
            ("劳动", "劳动合同"),
            ("保密", "保密协议"),
            ("授权", "授权许可合同"),
            ("合作", "合作协议"),
        ]
        for kw, label in rules:
            if kw in src:
                return label
        return "未知/其他"

    def _map_type_l1(self, contract_type: str) -> str:
        t = (contract_type or "").strip()
        if not t or t == "未知/其他":
            return "其他"
        if "采购" in t or "买卖" in t:
            return "采购"
        if "服务" in t:
            return "服务"
        if "租赁" in t:
            return "租赁"
        if "劳动" in t:
            return "劳动"
        if "保密" in t:
            return "保密"
        if "授权" in t or "许可" in t:
            return "授权"
        if "合作" in t:
            return "合作"
        return "其他"

    def _extract_type_evidence(self, text: str, contract_type: str) -> List[Dict[str, str]]:
        src = text or ""
        if not src:
            return []
        keyword_map: Dict[str, List[str]] = {
            "政府采购服务合同": ["政府采购", "采购人", "成交", "服务类"],
            "技术服务合同": ["技术服务", "技术支持", "实施服务", "开发服务"],
            "服务合同": ["服务", "服务内容", "服务标准", "服务期限"],
            "采购合同": ["采购", "供货", "货物", "设备"],
            "租赁合同": ["租赁", "租金", "承租", "出租"],
            "劳动合同": ["劳动", "聘用", "员工", "试用期"],
            "保密协议": ["保密", "商业秘密", "保密义务"],
            "授权许可合同": ["授权", "许可", "使用权"],
            "合作协议": ["合作", "双方合作", "联合"],
        }
        keywords = keyword_map.get(contract_type, [contract_type]) if contract_type else []
        evidence: List[Dict[str, str]] = []
        for kw in keywords:
            if not kw:
                continue
            idx = src.find(kw)
            if idx < 0:
                continue
            left = max(0, idx - 14)
            right = min(len(src), idx + len(kw) + 26)
            quote = re.sub(r"\s+", " ", src[left:right]).strip()
            if quote:
                evidence.append({"quote": quote[:80], "where": "规则命中"})
            if len(evidence) >= 3:
                break
        return evidence

    def _build_rule_type_detail(
        self,
        contract_type: str,
        contract_name: str,
        party_a: str,
        party_b: str,
        amount: str,
        term: str,
        source_text: str,
    ) -> Dict[str, Any]:
        evidence = self._extract_type_evidence(source_text, contract_type)
        confidence = 0.45
        if contract_type and contract_type != "未知/其他":
            confidence += 0.18
        confidence += min(0.18, len(evidence) * 0.06)
        if contract_name != "未提及":
            confidence += 0.07
        if party_a != "未提及" and party_b != "未提及":
            confidence += 0.06
        if amount != "未提及":
            confidence += 0.03
        if term != "未提及":
            confidence += 0.03
        confidence = round(min(0.97, max(0.35, confidence)), 2)

        type_l1 = self._map_type_l1(contract_type)
        type_l2 = contract_type or "未知/其他"
        detail = {
            "type_l1": type_l1,
            "type_l2": type_l2,
            "labels": [type_l1, type_l2],
            "confidence": confidence,
            "evidence": evidence,
            "source": "local_rule",
        }
        return detail

    def _build_rule_based_review(self, contract_text: str) -> Dict[str, Any]:
        src = (contract_text or "").strip()
        contract_type = self._guess_contract_type(src)
        contract_name = self._guess_contract_name(src)
        party_a = self._rule_extract(
            [r"甲方[：:]\s*([^\n\r]{2,80})", r"采购人[：:]\s*([^\n\r]{2,80})"],
            src,
        )
        party_b = self._rule_extract(
            [r"乙方[：:]\s*([^\n\r]{2,80})", r"供应商[：:]\s*([^\n\r]{2,80})"],
            src,
        )
        amount = self._rule_extract(
            [
                r"总金额[^¥\n\r]{0,20}(¥\s*[\d,]+(?:\.\d+)?)",
                r"合计金额[^¥\n\r]{0,20}(¥\s*[\d,]+(?:\.\d+)?)",
                r"(人民币[^\n\r]{4,40})",
            ],
            src,
        )
        term = self._rule_extract(
            [
                r"服务完成时间[：:]\s*([^\n\r]{4,80})",
                r"(20\d{2}年\d{1,2}月\d{1,2}日\s*[-至到]+\s*20\d{2}年\d{1,2}月\d{1,2}日)",
                r"(期限[：:]\s*[^\n\r]{2,60})",
            ],
            src,
        )
        type_detail = self._build_rule_type_detail(
            contract_type=contract_type,
            contract_name=contract_name,
            party_a=party_a,
            party_b=party_b,
            amount=amount,
            term=term,
            source_text=src,
        )
        min_items = max(3, _env_int("LOCAL_VLLM_RULE_MIN_ITEMS", 4))
        max_items = max(min_items, _env_int("LOCAL_VLLM_RULE_MAX_ITEMS", 8))
        max_items = min(12, max_items)
        risks_struct: List[Dict[str, str]] = []
        improve_struct: List[Dict[str, str]] = []
        seen_risk_keys: set[str] = set()
        seen_improve_keys: set[str] = set()

        def _evidence_from_keywords(keywords: List[str], max_len: int = 88) -> str:
            if not src:
                return ""
            for kw in keywords:
                if not kw:
                    continue
                idx = src.find(kw)
                if idx < 0:
                    continue
                left = max(0, idx - 20)
                right = min(len(src), idx + len(kw) + 36)
                snippet = re.sub(r"\s+", " ", src[left:right]).strip()
                if snippet:
                    return snippet[:max_len]
            return ""

        def _add_finding(
            *,
            title: str,
            level: str,
            risk_core: str,
            suggestion_core: str,
            evidence_keywords: List[str],
            missing_keywords: List[str] | None = None,
        ) -> None:
            evidence = _evidence_from_keywords(evidence_keywords)
            if not evidence and missing_keywords:
                evidence = "未检索到关键词：" + "/".join(missing_keywords)
            risk_text = risk_core.strip()
            if evidence:
                risk_text = f"{risk_text}（证据：{evidence}）"
            risk_key = re.sub(r"\s+", "", risk_text).lower()
            if risk_key and risk_key not in seen_risk_keys:
                seen_risk_keys.add(risk_key)
                risks_struct.append(
                    {
                        "title": title[:32] or "风险点",
                        "level": (level or "中")[:8],
                        "problem": risk_text[:260],
                        "suggestion": "",
                    }
                )

            suggestion_text = suggestion_core.strip()
            improve_key = re.sub(r"\s+", "", suggestion_text).lower()
            if improve_key and improve_key not in seen_improve_keys:
                seen_improve_keys.add(improve_key)
                improve_struct.append(
                    {
                        "title": f"建议{len(improve_struct) + 1}",
                        "problem": "",
                        "suggestion": suggestion_text[:260],
                    }
                )

        has_payment = self._contains_any(src, ["付款", "支付", "结算"])
        has_payment_schedule = bool(
            re.search(
                r"(?:付款|支付|结算)[^\n\r]{0,28}(?:工作日|发票|验收|节点|比例|一次性|分期|按月|按季度|尾款|预付款|到账|银行账户)",
                src,
            )
        )
        has_dispute_route = self._contains_any(src, ["争议解决", "管辖法院", "人民法院", "仲裁", "仲裁委员会"])
        dispute_is_specific = bool(re.search(r"(?:仲裁委员会|人民法院|法院)", src))
        has_acceptance = self._contains_any(src, ["验收", "考核"])
        has_acceptance_detail = self._contains_any(src, ["验收标准", "不合格", "整改", "复验", "考核指标", "验收结果"])
        has_subcontract_clause = self._contains_any(src, ["转包", "分包", "委托第三方", "第三方履约"])
        has_tax_invoice = self._contains_any(src, ["税率", "含税", "发票", "增值税"])
        has_force_majeure = self._contains_any(src, ["不可抗力"])
        has_termination = self._contains_any(src, ["解除", "终止"])
        has_data_security = self._contains_any(src, ["数据", "个人信息", "信息安全", "网络安全", "保密"])
        has_service_scene = self._contains_any(src, ["服务", "运营", "平台", "用户", "新媒体"])
        has_penalty_clause = self._contains_any(src, ["违约责任", "违约", "违约金", "赔偿"])
        has_penalty_formula = bool(re.search(r"(?:违约金|赔偿)[^\n\r]{0,20}(?:%|千分之|万分之|元|按日|按月|上限)", src))
        has_change_clause = self._contains_any(src, ["变更", "调整", "补充协议", "需求变更"])
        has_handover_clause = self._contains_any(src, ["交接", "移交", "归档", "交付物", "源文件"])
        has_external_dependency = self._contains_any(src, ["按照采购文件要求", "按采购文件要求", "按招标文件", "见附件"])
        has_liability_cap = self._contains_any(src, ["赔偿上限", "责任上限", "最高不超过", "上限"])
        placeholder_amount = bool(re.search(r"(?:\bY元\b|Y元\)|[¥￥]\s*Y\b)", src, re.IGNORECASE))
        vague_service_requirement = src.count("根据要求") >= 2 or src.count("按照采购文件要求") >= 1

        if placeholder_amount:
            _add_finding(
                title="价款条款可执行性不足",
                level="高",
                risk_core="合同价款字段包含占位符（如Y元），付款依据和最终结算金额不具备直接执行条件。",
                suggestion_core="将占位金额替换为明确数字，并同步写明计价依据、税费口径、开票条件和对应付款节点。",
                evidence_keywords=["Y元", "价款", "金额", "总金额", "合计金额"],
            )
        if has_payment and not has_payment_schedule:
            _add_finding(
                title="付款触发条件不清",
                level="高",
                risk_core="付款条款虽已出现，但未明确“何时付、按何依据付、逾期如何处理”，容易在结算节点产生争议。",
                suggestion_core="补充付款触发条件（验收通过/资料齐备）、具体时点（X个工作日内）、逾期利息与拒付处理流程。",
                evidence_keywords=["付款", "支付", "结算"],
            )
        if has_acceptance and not has_acceptance_detail:
            _add_finding(
                title="验收标准粒度不足",
                level="中",
                risk_core="验收条款存在但缺少量化标准、整改时限和复验机制，导致“是否合格”缺乏统一口径。",
                suggestion_core="按交付物逐项列明验收标准、不合格整改期限、复验流程及验收失败后的责任承担方式。",
                evidence_keywords=["验收", "考核", "服务标准"],
            )
        if has_service_scene and not has_data_security:
            _add_finding(
                title="数据与保密边界不完整",
                level="中",
                risk_core="合同涉及运营/信息处理场景，但数据使用范围、存储期限和泄露责任边界不够完整，存在合规风险。",
                suggestion_core="补充数据分类分级、最小必要使用、保存与销毁规则，并明确泄露事件通知时限与违约责任。",
                evidence_keywords=["服务", "运营", "平台", "数据", "保密"],
            )
        if contract_type in {"服务合同", "政府采购服务合同", "技术服务合同"} and not has_subcontract_clause:
            _add_finding(
                title="分包约束缺失",
                level="中",
                risk_core="服务类合同未明确分包/转包限制及审批路径，关键岗位替换和履约质量控制存在不确定性。",
                suggestion_core="增加分包前置审批、关键岗位替换告知与交接要求，并约定分包情形下乙方的连带责任。",
                evidence_keywords=["服务", "履约", "人员", "转包", "分包"],
                missing_keywords=["转包", "分包"],
            )
        if (amount != "未提及" or has_payment) and not has_tax_invoice:
            _add_finding(
                title="价税票条款不闭合",
                level="中",
                risk_core="合同虽涉及金额或付款，但未形成“含税口径+发票类型+开票时点”的闭环，可能影响财务结算与报销合规。",
                suggestion_core="明确含税/不含税口径、发票类型与税率、开票条件和开票时限，并约定发票异常时的处理方案。",
                evidence_keywords=["金额", "付款", "发票", "税率", "含税"],
            )
        if not has_dispute_route:
            _add_finding(
                title="争议解决路径缺失",
                level="高",
                risk_core="未明确争议解决方式与管辖机构，纠纷发生后可能出现程序性拉扯和维权成本上升。",
                suggestion_core="明确争议解决路径（诉讼或仲裁）及具体管辖法院/仲裁委员会，并约定适用法律条款。",
                evidence_keywords=["争议", "仲裁", "法院"],
                missing_keywords=["争议解决", "管辖法院", "仲裁"],
            )
        elif not dispute_is_specific:
            _add_finding(
                title="争议条款不够具体",
                level="中",
                risk_core="虽有争议处理表述，但未指向具体法院或仲裁机构，执行阶段仍可能产生管辖异议。",
                suggestion_core="将争议条款细化至具体法院或仲裁委员会，并补充送达地址和电子送达约定。",
                evidence_keywords=["争议", "仲裁", "法院"],
            )
        if has_penalty_clause and not has_penalty_formula:
            _add_finding(
                title="违约责任量化不足",
                level="中",
                risk_core="违约责任虽有原则性表述，但违约金计算口径或赔偿上限不清，难以直接用于追责。",
                suggestion_core="补充违约金计算方式（比例/日罚则）、赔偿范围与上限、损失举证与扣款顺序。",
                evidence_keywords=["违约", "赔偿", "违约责任"],
            )
        elif not has_penalty_clause:
            _add_finding(
                title="违约条款缺失",
                level="高",
                risk_core="合同未形成完整违约责任体系，无法对迟延履行、质量不达标等情形进行有效约束。",
                suggestion_core="补齐违约定义、违约金标准、损失赔偿与解除条件，建立完整违约追责链路。",
                evidence_keywords=["违约", "赔偿", "违约责任"],
                missing_keywords=["违约责任", "违约金", "赔偿"],
            )
        if not has_change_clause:
            _add_finding(
                title="变更管理机制不清",
                level="中",
                risk_core="未明确需求变更、服务范围调整和费用变更的审批流程，后续容易出现追加工作与费用争议。",
                suggestion_core="补充变更申请、评审、确认和生效流程，并约定变更对应的工期与费用调整规则。",
                evidence_keywords=["变更", "调整", "补充协议", "服务范围"],
                missing_keywords=["变更", "补充协议"],
            )
        if not has_handover_clause and has_service_scene:
            _add_finding(
                title="成果交接约定不足",
                level="中",
                risk_core="合同对服务成果、过程文档和账号权限交接要求不完整，可能影响项目切换与后续持续运营。",
                suggestion_core="明确交付清单、交接时点、交接验收标准及交接失败责任，必要时约定源文件与数据回迁义务。",
                evidence_keywords=["交付", "成果", "交接", "源文件", "账号"],
                missing_keywords=["交接", "移交", "交付物"],
            )
        if has_external_dependency:
            _add_finding(
                title="核心标准外部依赖较强",
                level="中",
                risk_core="多处以“按采购文件/招标文件要求”为准，合同正文自足性不足，执行时可能出现解释分歧。",
                suggestion_core="将关键服务标准、验收口径、时限和违约触发条件写入合同正文，降低对外部文件的解释依赖。",
                evidence_keywords=["按照采购文件要求", "按采购文件要求", "按招标文件", "见附件"],
            )
        if not has_liability_cap:
            _add_finding(
                title="赔偿边界未封顶",
                level="中",
                risk_core="未见明确责任上限或赔偿边界设置，极端争议场景下赔偿敞口不可控。",
                suggestion_core="约定赔偿责任上限、间接损失排除条款及可主张损失范围，控制法律与财务敞口。",
                evidence_keywords=["赔偿", "责任", "上限", "违约"],
                missing_keywords=["责任上限", "赔偿上限"],
            )
        if vague_service_requirement:
            _add_finding(
                title="服务要求表述偏笼统",
                level="中",
                risk_core="条款存在“根据要求”等模糊表达，未细化至可核验指标，可能导致交付与验收标准不一致。",
                suggestion_core="将抽象要求拆解为可量化KPI（频次、时效、质量阈值）并绑定验收证据留存方式。",
                evidence_keywords=["根据要求", "按要求", "服务要求", "服务标准"],
            )
        if not has_force_majeure:
            _add_finding(
                title="不可抗力条款不足",
                level="中",
                risk_core="未形成完整不可抗力处理机制，突发事件下免责边界、通知义务和恢复履行规则不清晰。",
                suggestion_core="补充不可抗力定义、通知时限、减损义务、恢复履行和费用分担规则。",
                evidence_keywords=["不可抗力", "免责"],
                missing_keywords=["不可抗力"],
            )
        if not has_termination:
            _add_finding(
                title="退出机制不完整",
                level="中",
                risk_core="提前解除/终止条件和结算方式未充分明确，项目中止时易引发费用与成果归属争议。",
                suggestion_core="明确单方解除触发条件、通知期、已完成工作量结算规则和成果归属处理方式。",
                evidence_keywords=["解除", "终止", "结算"],
                missing_keywords=["解除", "终止"],
            )

        if not risks_struct:
            _add_finding(
                title="关键条款需人工复核",
                level="中",
                risk_core="自动规则未识别出高确定性问题，但付款、验收、违约与争议条款仍建议人工逐条核验。",
                suggestion_core="按“付款-验收-违约-争议”四个维度进行人工复核，并对高风险条款形成修订闭环。",
                evidence_keywords=["付款", "验收", "违约", "争议"],
            )

        # Keep a minimum actionable set, but do not force a fixed count.
        if len(risks_struct) < min_items:
            supplement_pool = [
                (
                    "服务边界约定不足",
                    "中",
                    "服务范围和成果边界仍存在抽象描述，可能导致“是否完成交付”判断不一致。",
                    "按模块拆分服务边界，明确每项服务的输入、输出、完成判定和对应责任人。",
                    ["服务范围", "服务内容", "工作量"],
                ),
                (
                    "履约过程留痕不足",
                    "中",
                    "合同未充分约定过程留痕和台账要求，后续举证可能依赖单方材料。",
                    "增加过程文档、周报/月报、确认邮件与验收记录等留痕要求，作为付款与争议处理依据。",
                    ["验收", "工作记录", "报告"],
                ),
                (
                    "项目协同机制不清",
                    "中",
                    "双方联络、确认与反馈机制不够细，跨部门协作时易出现响应时效争议。",
                    "补充联络机制、反馈时限、默认确认规则及升级路径，减少沟通层面的执行摩擦。",
                    ["联系人", "联系方式", "确认"],
                ),
            ]
            for title, level, risk_core, suggestion_core, kws in supplement_pool:
                if len(risks_struct) >= min_items:
                    break
                _add_finding(
                    title=title,
                    level=level,
                    risk_core=risk_core,
                    suggestion_core=suggestion_core,
                    evidence_keywords=kws,
                )

        risks_struct = risks_struct[:max_items]
        improve_struct = improve_struct[:max_items]

        key_facts = {
            "合同名称": contract_name,
            "甲方": party_a,
            "乙方": party_b,
            "金额": amount,
            "期限": term,
        }
        overview = self._compose_detailed_overview(
            contract_type=contract_type,
            key_facts=key_facts,
            risks=risks_struct,
            improvements=improve_struct,
            source_text=src,
        )
        return {
            "contract_type": contract_type,
            "合同类型": contract_type,
            "contract_type_detail": type_detail,
            "合同类型明细": type_detail,
            "overview": overview[: self.cfg.overview_max_chars],
            "审查概述": overview[: self.cfg.overview_max_chars],
            "risks": risks_struct,
            "风险点": risks_struct,
            "improvements": improve_struct,
            "改进措施": improve_struct,
            "key_facts": key_facts,
        }

    def _normalize_risk_items(self, values: Any) -> List[Dict[str, str]]:
        raw_items: List[Any] = []
        if isinstance(values, str):
            raw_items.extend(self._parse_pipe_list(values))
        elif isinstance(values, list):
            raw_items.extend(values)

        out: List[Dict[str, str]] = []
        for item in raw_items:
            if isinstance(item, str):
                problem = item.strip()
                if not problem:
                    continue
                out.append({"title": "风险点", "level": "中", "problem": problem, "suggestion": ""})
                continue
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    out.append({"title": "风险点", "level": "中", "problem": text, "suggestion": ""})
                continue

            title = str(item.get("title") or item.get("name") or item.get("item") or item.get("风险点") or "风险点").strip()
            level = str(item.get("level") or item.get("severity") or item.get("risk_level") or item.get("风险等级") or "中").strip()
            problem = (
                str(item.get("problem") or "").strip()
                or str(item.get("issue") or "").strip()
                or str(item.get("desc") or "").strip()
                or str(item.get("description") or "").strip()
                or str(item.get("suggestion") or "").strip()
                or str(item.get("建议") or "").strip()
                or title
            )
            problem = re.sub(r"\s+", " ", problem).strip()
            if not problem:
                continue
            out.append({"title": title[:32] or "风险点", "level": level or "中", "problem": problem[:260], "suggestion": ""})

        dedup: List[Dict[str, str]] = []
        seen = set()
        for item in out:
            key = str(item.get("problem") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            dedup.append(item)
            if len(dedup) >= 10:
                break
        return dedup

    def _normalize_improvement_items(self, values: Any) -> List[Dict[str, str]]:
        raw_items: List[Any] = []
        if isinstance(values, str):
            raw_items.extend(self._parse_pipe_list(values))
        elif isinstance(values, list):
            raw_items.extend(values)

        out: List[Dict[str, str]] = []
        for item in raw_items:
            if isinstance(item, str):
                suggestion = item.strip()
                if not suggestion:
                    continue
                if re.fullmatch(r"(建议|建议\d+|改进|改进\d+)", suggestion):
                    continue
                out.append({"title": "改进建议", "problem": "", "suggestion": suggestion[:260]})
                continue
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    out.append({"title": "改进建议", "problem": "", "suggestion": text[:260]})
                continue

            title = str(item.get("title") or item.get("name") or item.get("item") or "改进建议").strip()
            problem = (
                str(item.get("problem") or "").strip()
                or str(item.get("issue") or "").strip()
                or str(item.get("description") or "").strip()
            )
            suggestion = (
                str(item.get("suggestion") or "").strip()
                or str(item.get("advice") or "").strip()
                or str(item.get("fix") or "").strip()
                or str(item.get("solution") or "").strip()
                or str(item.get("建议") or "").strip()
                or str(item.get("修改建议") or "").strip()
                or problem
                or title
            )
            suggestion = re.sub(r"\s+", " ", suggestion).strip()
            if not suggestion:
                continue
            out.append({"title": title[:32] or "改进建议", "problem": problem[:220], "suggestion": suggestion[:260]})

        dedup: List[Dict[str, str]] = []
        seen = set()
        for item in out:
            key = str(item.get("suggestion") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            dedup.append(item)
            if len(dedup) >= 10:
                break
        for idx, item in enumerate(dedup, start=1):
            if item.get("title") in {"", "改进建议"}:
                item["title"] = f"建议{idx}"
        return dedup

    def _compose_detailed_overview(
        self,
        contract_type: str,
        key_facts: Dict[str, str],
        risks: List[Dict[str, str]],
        improvements: List[Dict[str, str]],
        source_text: str,
    ) -> str:
        min_chars = max(120, int(self.cfg.overview_min_chars))
        max_chars = max(min_chars + 40, int(self.cfg.overview_max_chars))
        facts = key_facts or {}
        name = str(facts.get("合同名称") or "未提及")
        party_a = str(facts.get("甲方") or "未提及")
        party_b = str(facts.get("乙方") or "未提及")
        amount = str(facts.get("金额") or "未提及")
        term = str(facts.get("期限") or "未提及")

        risk_texts = [str(item.get("problem") or "").strip() for item in risks if isinstance(item, dict)]
        improve_texts = [str(item.get("suggestion") or "").strip() for item in improvements if isinstance(item, dict)]
        risk_summary = "；".join([re.sub(r"\s+", " ", t)[:48] for t in risk_texts[:3] if t]) or "当前文本中未识别到高确定性的实质性风险。"
        improve_summary = "；".join([re.sub(r"\s+", " ", t)[:48] for t in improve_texts[:3] if t]) or "建议补充付款、验收、违约和争议解决条款的可执行细节。"

        pieces = [
            f"本次审查基于已提取的合同正文进行结构化分析，综合条款关键词、主体信息和履约要素判断，该文本类型倾向于“{contract_type}”。",
            f"关键信息方面，合同名称为“{name}”，甲方为“{party_a}”，乙方为“{party_b}”，金额为“{amount}”，期限为“{term}”。",
            "从履约执行和争议处理视角看，当前文本在条款可执行性与责任边界方面仍存在需要重点复核的内容。",
            f"风险侧重点包括：{risk_summary}",
            f"改进方向建议优先覆盖：{improve_summary}",
            "建议在正式签署前按“事实要素完整、触发条件明确、责任口径可追溯”的原则进行条款修订，并结合业务实际完成法务复核。"
        ]
        overview = "".join([p for p in pieces if p])
        overview = re.sub(r"\s+", " ", overview).strip()
        if len(overview) < min_chars:
            overview += "同时建议将关键条款与履约证据留存机制绑定，确保后续在付款、验收、变更及争议阶段均有明确的执行依据。"
        if len(overview) < min_chars:
            overview += "若后续出现业务范围调整，应同步更新服务边界、交付标准及费用结算规则，防止因版本不一致导致合同解释分歧。"
        if len(overview) <= max_chars:
            return overview
        clipped = overview[:max_chars]
        best_end = -1
        for sep in ("。", "；", "！", "?", "？"):
            pos = clipped.rfind(sep)
            if pos > best_end:
                best_end = pos
        if best_end >= int(max_chars * 0.7):
            return clipped[: best_end + 1]
        return clipped

    def _is_placeholder_fact(self, value: Any) -> bool:
        s = str(value or "").strip()
        if not s:
            return True
        low = s.lower()
        if low in {"n/a", "na", "none", "null", "unknown", "-", "--"}:
            return True
        if s in {"未提及", "未知", "待补充", "暂无", "无"}:
            return True
        if "合同名称/标题" in s:
            return True
        if re.search(r"[【\[].{0,20}(合同名称|甲方|乙方|金额|期限|标题).{0,20}[】\]]", s):
            return True
        if re.fullmatch(r"(合同名称|标题|甲方|乙方|金额|期限)", s):
            return True
        return False

    def _merge_review_with_rule_hints(self, review_data: Dict[str, Any], source_text: str) -> Dict[str, Any]:
        merged = dict(review_data or {})
        rule = self._build_rule_based_review(source_text)
        merge_cap = max(4, _env_int("LOCAL_VLLM_RULE_MAX_ITEMS", 8))
        merge_cap = min(12, merge_cap)
        rule_detail_raw = rule.get("合同类型明细") if isinstance(rule.get("合同类型明细"), dict) else rule.get("contract_type_detail")
        rule_detail = dict(rule_detail_raw) if isinstance(rule_detail_raw, dict) else {}
        rule_conf = float(rule_detail.get("confidence", 0.0)) if isinstance(rule_detail.get("confidence"), (int, float)) else 0.0

        contract_type = (
            str(merged.get("contract_type") or merged.get("合同类型") or "").strip()
            or "未知/其他"
        )
        generic_types = {"服务合同", "采购合同", "合作协议"}
        model_evidence = self._extract_type_evidence(source_text, contract_type)
        should_use_rule_type = (
            contract_type in {"未知/其他", "未识别", "unknown", "n/a"}
            or (
                contract_type in generic_types
                and str(rule.get("contract_type") or "").strip()
                and rule.get("contract_type") != contract_type
            )
            or (
                contract_type not in {"未知/其他", "未识别", "unknown", "n/a"}
                and not model_evidence
                and rule_conf >= 0.75
            )
        )
        if should_use_rule_type:
            merged["contract_type"] = rule["contract_type"]
        else:
            merged["contract_type"] = contract_type
        merged["合同类型"] = merged.get("contract_type", "未知/其他")

        detail_raw = (
            merged.get("合同类型明细")
            if isinstance(merged.get("合同类型明细"), dict)
            else merged.get("contract_type_detail")
            if isinstance(merged.get("contract_type_detail"), dict)
            else merged.get("type_detail")
        )
        detail: Dict[str, Any] = dict(detail_raw) if isinstance(detail_raw, dict) else {}
        conf = detail.get("confidence")
        if not isinstance(conf, (int, float)) or float(conf) < 0 or float(conf) > 1:
            detail["confidence"] = float(rule_detail.get("confidence", 0.5))
        else:
            detail["confidence"] = round(float(conf), 2)
        if detail["confidence"] < 0.35 and isinstance(rule_detail.get("confidence"), (int, float)):
            detail["confidence"] = round(float(rule_detail["confidence"]), 2)
        detail["type_l2"] = merged.get("contract_type", "未知/其他")
        detail["type_l1"] = self._map_type_l1(detail["type_l2"])
        detail["labels"] = [detail["type_l1"], detail["type_l2"]]
        if not isinstance(detail.get("evidence"), list) or not detail.get("evidence"):
            detail["evidence"] = rule_detail.get("evidence", [])
        detail["source"] = str(detail.get("source") or rule_detail.get("source") or "local_rule")
        merged["contract_type_detail"] = detail
        merged["合同类型明细"] = detail

        risks = self._normalize_risk_items(merged.get("risks") or merged.get("风险点"))
        for item in self._normalize_risk_items(rule.get("risks") or rule.get("风险点")):
            if len(risks) >= merge_cap:
                break
            item_key = str(item.get("problem") or "").strip().lower()
            if not item_key:
                continue
            if any(str(existing.get("problem") or "").strip().lower() == item_key for existing in risks):
                continue
            risks.append(item)
        merged["risks"] = risks
        merged["风险点"] = list(risks)

        improvements = self._normalize_improvement_items(
            merged.get("improvements") or merged.get("改进措施") or merged.get("改进建议")
        )
        for item in self._normalize_improvement_items(rule.get("improvements") or rule.get("改进措施")):
            if len(improvements) >= merge_cap:
                break
            item_key = str(item.get("suggestion") or "").strip().lower()
            if not item_key:
                continue
            if any(str(existing.get("suggestion") or "").strip().lower() == item_key for existing in improvements):
                continue
            improvements.append(item)
        merged["improvements"] = improvements
        merged["改进措施"] = list(improvements)
        merged["改进建议"] = list(improvements)

        key_facts_raw = merged.get("key_facts")
        key_facts: Dict[str, Any] = key_facts_raw if isinstance(key_facts_raw, dict) else {}
        alias_map = {
            "合同名称": ["合同名称", "contract_name", "title", "name"],
            "甲方": ["甲方", "party_a", "partyA", "buyer"],
            "乙方": ["乙方", "party_b", "partyB", "seller", "vendor"],
            "金额": ["金额", "amount", "price", "total_amount"],
            "期限": ["期限", "term", "period", "duration"],
        }
        for key, aliases in alias_map.items():
            current = ""
            for alias in aliases:
                value = key_facts.get(alias)
                if isinstance(value, str) and value.strip():
                    current = value.strip()
                    break
            if self._is_placeholder_fact(current):
                key_facts[key] = rule["key_facts"][key]
            else:
                key_facts[key] = current[:120]
        merged["key_facts"] = key_facts

        overview_raw = str(merged.get("overview") or merged.get("审查概述") or "").strip()
        min_chars = max(120, int(self.cfg.overview_min_chars))
        max_chars = max(min_chars + 40, int(self.cfg.overview_max_chars))
        composed_overview = self._compose_detailed_overview(
            contract_type=str(merged.get("contract_type") or "未知/其他"),
            key_facts={k: str(v) for k, v in key_facts.items()},
            risks=risks,
            improvements=improvements,
            source_text=source_text,
        )
        if (
            not overview_raw
            or overview_raw in {"暂无概述", "未提及"}
            or ("软件开发" in overview_raw and "软件开发" not in source_text)
            or len(overview_raw) < min_chars
        ):
            overview = composed_overview
        else:
            overview = overview_raw
            if len(overview) < min_chars:
                overview = (overview + " " + composed_overview).strip()
            overview = overview[:max_chars]
        merged["overview"] = overview
        merged["审查概述"] = overview

        merged.setdefault("合同类型", merged.get("contract_type", "未知/其他"))
        return merged

    def _review_via_text_template(self, compact_text: str, source_text_for_rule: str = "") -> Dict[str, Any]:
        source_text = (source_text_for_rule or compact_text or "").strip()
        messages = self._build_text_fallback_messages(compact_text)
        messages, max_tokens = self._fit_messages_to_context(messages, max(self.cfg.max_tokens, 64))
        content = self._chat_text(messages, max_tokens)
        if not content:
            return self._build_rule_based_review(source_text)

        parsed: Dict[str, str] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = line.lstrip("-*0123456789. ").strip()
            if ":" in line:
                key, value = line.split(":", 1)
            elif "：" in line:
                key, value = line.split("：", 1)
            else:
                continue
            norm_key = key.strip().lower().replace(" ", "").replace("_", "")
            parsed[norm_key] = value.strip()

        contract_type = (
            parsed.get("contracttype")
            or parsed.get("合同类型")
            or parsed.get("type")
            or "未知/其他"
        )
        overview = parsed.get("overview") or parsed.get("审查概述") or parsed.get("summary") or "暂无概述"
        risks = self._parse_pipe_list(parsed.get("risks", "") or parsed.get("风险点", ""))
        improvements = self._parse_pipe_list(parsed.get("improvements", "") or parsed.get("改进建议", ""))
        key_facts = self._parse_key_facts_text(parsed.get("keyfacts", "") or parsed.get("key_facts", ""))

        # If model output is still not parsable (common under tiny context), use rule extraction.
        if (
            contract_type == "未知/其他"
            and overview in {"暂无概述", ""}
            and not risks
            and not improvements
            and all(v == "未提及" for v in key_facts.values())
        ):
            return self._build_rule_based_review(source_text)

        return {
            "contract_type": contract_type[:40],
            "overview": overview[: self.cfg.overview_max_chars],
            "risks": risks,
            "improvements": improvements,
            "key_facts": key_facts,
        }

    def _chat_json(self, messages: List[Dict[str, str]], max_tokens: int) -> Dict[str, Any]:
        self._preflight_health()
        kwargs = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": max_tokens,
            "timeout": self.cfg.timeout_s,
        }
        extra_body = self._chat_extra_body()
        if extra_body:
            kwargs["extra_body"] = extra_body
        first_exc: Exception | None = None

        try:
            resp = self.client.chat.completions.create(
                response_format={"type": "json_object"},
                **kwargs,
            )
            content = self._strip_think_content(resp.choices[0].message.content or "")
            if not content:
                raise ValueError("empty model response")
            return _extract_json_object(content)
        except Exception as exc:
            first_exc = exc
            if self._is_server_side_failure(exc):
                self._mark_unhealthy(f"chat json first attempt failed: {exc}")
                raise RuntimeError(f"local vllm json request failed fast: {exc}") from exc

        try:
            resp = self.client.chat.completions.create(**kwargs)
            content = self._strip_think_content(resp.choices[0].message.content or "")
            if not content:
                raise ValueError("empty model response")
            return _extract_json_object(content)
        except Exception as exc:
            if self._is_server_side_failure(exc):
                self._mark_unhealthy(f"chat json second attempt failed: {exc}")
            raise RuntimeError(
                f"local vllm json request failed: first={first_exc}; second={exc}"
            ) from exc

    def _chat_text(self, messages: List[Dict[str, str]], max_tokens: int) -> str:
        self._preflight_health()
        try:
            kwargs: Dict[str, Any] = {
                "model": self.cfg.model,
                "messages": messages,
                "temperature": self.cfg.temperature,
                "max_tokens": max_tokens,
                "timeout": self.cfg.timeout_s,
            }
            extra_body = self._chat_extra_body()
            if extra_body:
                kwargs["extra_body"] = extra_body
            resp = self.client.chat.completions.create(
                **kwargs,
            )
            content = self._strip_think_content(resp.choices[0].message.content or "")
            return content.strip()
        except Exception as exc:
            if self._is_server_side_failure(exc):
                self._mark_unhealthy(f"chat text failed: {exc}")
            raise

    def review_contract(self, markdown_text: str) -> Dict[str, Any]:
        source_text = (markdown_text or "").strip()
        compact_text = self._segment_for_small_context(source_text)
        compact_text = _truncate_for_prompt(compact_text, self.cfg.prompt_text_max_chars)
        taxonomy = self._compact_taxonomy()
        attempts = [
            (compact_text, taxonomy, False),
            (_truncate_for_prompt(compact_text, max(160, int(self.cfg.prompt_text_max_chars * 0.85))), "{}", True),
        ]
        last_exc: Exception | None = None
        desired_max_tokens = max(self.cfg.max_tokens, 48)

        for text_payload, taxonomy_payload, aggressive in attempts:
            messages = self._build_review_messages(
                compact_text=text_payload,
                taxonomy=taxonomy_payload,
                aggressive=aggressive,
            )
            messages, max_tokens = self._fit_messages_to_context(messages, desired_max_tokens)
            try:
                data = self._chat_json(messages, max_tokens)
                if not isinstance(data, dict):
                    raise ValueError("local vllm response is not json object")
                merged = self._merge_review_with_rule_hints(data, source_text)
                return _postprocess_local_review_json(merged)
            except Exception as exc:
                last_exc = exc
                if aggressive and self._is_json_parse_failure(exc):
                    fallback_data = self._review_via_text_template(
                        text_payload,
                        source_text_for_rule=source_text,
                    )
                    merged = self._merge_review_with_rule_hints(fallback_data, source_text)
                    return _postprocess_local_review_json(merged)
                if aggressive or not self._is_json_parse_failure(exc):
                    raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("local vllm review failed without response")

    def fix_ocr_text(self, raw_text: str) -> str:
        src = (raw_text or "").strip()
        if not src:
            return raw_text

        prompt_text = _truncate_for_prompt(src, self.cfg.ocr_fix_max_chars)
        messages = [
            {
                "role": "system",
                "content": (
                    "You only fix OCR noise. Keep original meaning and paragraph order. "
                    "Return plain text only, no markdown, no explanation."
                ),
            },
            {"role": "user", "content": prompt_text},
        ]
        messages, max_tokens = self._fit_messages_to_context(messages, self.cfg.max_tokens)
        fixed = self._chat_text(messages, max_tokens).strip()
        if not fixed:
            return raw_text

        raw_len = len(src)
        if raw_len > 0 and len(fixed) < max(120, int(raw_len * 0.45)):
            return raw_text
        return fixed


def _primary_provider_name() -> str:
    primary = (os.environ.get("LLM_PRIMARY_PROVIDER") or "").strip().lower()
    if primary:
        return primary
    return (os.environ.get("LLM_PROVIDER") or "remote").strip().lower()


def _fallback_provider_name(default: str = "remote") -> str:
    raw = (os.environ.get("LLM_FALLBACK_PROVIDER") or "").strip().lower()
    if not raw:
        return default
    alias = {
        "qwen_plus": "remote",
        "qwen-plus": "remote",
        "remote_qwen": "remote",
        "none": "none",
        "off": "none",
        "disabled": "none",
    }
    return alias.get(raw, raw)


def _build_client_chain() -> Tuple[BaseLLMClient, BaseLLMClient | None]:
    provider = _primary_provider_name()

    if provider in {"local_vllm", "local", "vllm"}:
        primary = LocalVLLMClient(LocalVLLMConfig.from_env())
        fallback_enabled = _env_flag("LLM_LOCAL_FALLBACK_REMOTE", True)
        fallback_provider = _fallback_provider_name("remote")
        if fallback_enabled and fallback_provider != "none":
            return primary, RemoteLLMClient()
        return primary, None

    if provider in {"remote", "qwen_plus", "qwen-plus"}:
        primary = RemoteLLMClient()
        fallback_provider = _fallback_provider_name("none")
        if fallback_provider in {"local_vllm", "local", "vllm"}:
            return primary, LocalVLLMClient(LocalVLLMConfig.from_env())
        return primary, None

    # Default keeps current behavior.
    return RemoteLLMClient(), None


def review_contract(markdown_text: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    primary, fallback = _build_client_chain()
    try:
        return primary.review_contract(markdown_text), {
            "provider": primary.name,
            "fallback_used": False,
        }
    except Exception as first_exc:
        if not fallback:
            raise
        print(f"[llm] primary provider failed: {primary.name} err={first_exc}", flush=True)
        try:
            data = fallback.review_contract(markdown_text)
            return data, {
                "provider": fallback.name,
                "fallback_used": True,
                "fallback_from": primary.name,
                "fallback_reason": str(first_exc)[:500],
            }
        except Exception as second_exc:
            raise RuntimeError(
                f"llm fallback failed: primary={primary.name} err={first_exc}; "
                f"fallback={fallback.name} err={second_exc}"
            ) from second_exc


def fix_ocr_text(raw_text: str) -> Tuple[str, Dict[str, Any]]:
    primary, fallback = _build_client_chain()
    try:
        return primary.fix_ocr_text(raw_text), {
            "provider": primary.name,
            "fallback_used": False,
        }
    except Exception as first_exc:
        if not fallback:
            raise
        print(f"[llm] primary ocr-fix failed: {primary.name} err={first_exc}", flush=True)
        try:
            data = fallback.fix_ocr_text(raw_text)
            return data, {
                "provider": fallback.name,
                "fallback_used": True,
                "fallback_from": primary.name,
                "fallback_reason": str(first_exc)[:500],
            }
        except Exception as second_exc:
            raise RuntimeError(
                f"ocr-fix fallback failed: primary={primary.name} err={first_exc}; "
                f"fallback={fallback.name} err={second_exc}"
            ) from second_exc
