# contract_review/services/stamp_detect.py
from __future__ import annotations
from typing import Dict, List, Tuple

import fitz  # PyMuPDF
import numpy as np
import cv2


def _find_red_regions(img_bgr: np.ndarray) -> List[Tuple[int, int, int, int, float]]:
    """
    返回可能的红章区域 bbox 列表: (x, y, w, h, score)
    score 这里用区域内红色像素占比做一个简单置信度
    """
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    # 红色在 HSV 里通常跨 0 度，所以要两段
    lower1 = np.array([0, 70, 50])
    upper1 = np.array([10, 255, 255])
    lower2 = np.array([160, 70, 50])
    upper2 = np.array([180, 255, 255])

    mask1 = cv2.inRange(hsv, lower1, upper1)
    mask2 = cv2.inRange(hsv, lower2, upper2)
    mask = cv2.bitwise_or(mask1, mask2)

    # 形态学去噪/连通
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    # 连通域
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    candidates = []
    H, W = mask.shape[:2]

    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]

        # 过滤太小的红块（噪声）
        if area < 800:
            continue

        # 过滤超大的红块（比如整页水印）
        if area > 0.25 * H * W:
            continue

        # 印章大多接近方形区域，宽高比别太离谱
        ar = w / float(h + 1e-6)
        if ar < 0.35 or ar > 2.8:
            continue

        # 计算红色占比作为 score
        region = mask[y : y + h, x : x + w]
        red_ratio = float(np.count_nonzero(region)) / float(region.size + 1e-6)

        # 水印通常覆盖大面积但红色密度很低、且形态松散；
        # 印章通常红色密度相对更高
        if red_ratio < 0.08:
            continue

        candidates.append((x, y, w, h, red_ratio))

    # 按 score 排序，便于展示 evidence
    candidates.sort(key=lambda t: t[4], reverse=True)
    return candidates


def _select_page_indices(total: int, max_pages: int, head_pages: int, tail_pages: int) -> List[int]:
    if total <= 0:
        return []
    if max_pages <= 0 or total <= max_pages:
        return list(range(total))

    head = min(head_pages, total)
    tail = min(tail_pages, max(0, total - head))

    keep = list(range(0, head))
    if tail > 0:
        keep += list(range(total - tail, total))

    remaining = max_pages - len(keep)
    middle_start = head
    middle_end = total - tail
    if remaining > 0 and middle_end > middle_start:
        step = (middle_end - middle_start) / float(remaining + 1)
        for i in range(remaining):
            idx = int(middle_start + step * (i + 1))
            keep.append(idx)

    return sorted(set(keep))


def detect_stamp_status_from_pdf(pdf_path: str, max_pages: int = 8, tail_pages: int = 4) -> Dict:
    """
    图像法检测 PDF 是否盖章（扫描件友好）
    返回:
      {
        "stamp_status": "YES"|"NO"|"UNCERTAIN",
        "evidence": [{"page": 1, "bbox": [x,y,w,h], "score":0.xx}, ...]
      }
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        return {"stamp_status": "UNCERTAIN", "evidence": [{"error": f"open_pdf_failed: {e}"}]}

    evidence = []
    try:
        total = len(doc)
        tail_pages = max(tail_pages, 0)
        if max_pages <= 0:
            page_indices = list(range(total))
        else:
            head_pages = max(max_pages - tail_pages, 0)
            page_indices = _select_page_indices(total, max_pages, head_pages, tail_pages)

        for pno in page_indices:
            page = doc[pno]
            pix = page.get_pixmap(dpi=200, alpha=False)  # dpi 可调 180~240
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            # pix 是 RGB，转 BGR 供 opencv
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

            cands = _find_red_regions(img_bgr)
            # 取前几个证据就够了
            for (x, y, w, h, score) in cands[:3]:
                evidence.append(
                    {"page": pno + 1, "bbox": [int(x), int(y), int(w), int(h)], "score": float(score)}
                )

            if cands:
                return {"stamp_status": "YES", "evidence": evidence}

        return {"stamp_status": "NO", "evidence": evidence}
    except Exception as e:
        return {"stamp_status": "UNCERTAIN", "evidence": [{"error": f"detect_failed: {e}"}]}
    finally:
        try:
            doc.close()
        except Exception:
            pass


def detect_stamp_status(text: str) -> Dict:
    """
    ✅ 兼容旧接口（views.py 里 import 的就是它）
    - 如果传进来是文本：做一个轻量关键词兜底
    - 如果你以后想彻底切到图像法：请在 views.py 改成调用 detect_stamp_status_from_pdf(pdf_path)
    """
    text = text or ""
    kws = ["盖章", "印章", "公章", "签章"]
    hit = any(k in text for k in kws)
    return {
        "stamp_status": "YES" if hit else "NO",
        "evidence": [k for k in kws if k in text],
    }
