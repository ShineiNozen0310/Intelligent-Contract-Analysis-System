from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore


# =========================
# env helpers
# =========================
def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v is None or str(v).strip() == "":
        return default
    return v


def _as_bool(key: str, default: bool = False) -> bool:
    v = _env(key)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(key: str, default: float) -> float:
    v = _env(key)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _as_path(v: str | None) -> Path | None:
    if not v:
        return None
    return Path(v).expanduser().resolve()


def _setenv_if(value: Path | str | None, key: str) -> None:
    if value is None:
        return
    os.environ[key] = str(value)


# =========================
# Stamp (盖章) Config
# =========================
@dataclass(frozen=True)
class StampConfig:
    """
    stamp = 可选模块
    - 默认 enabled=False
    - 即使 enabled=True 但模型缺失，也会自动降级为 disabled
    """

    yolo_model_path: Path
    yolo_conf: float
    enabled: bool

    @staticmethod
    def from_env(project_root: Path) -> "StampConfig":
        # ✅ 默认 False：不阻塞服务启动
        enabled = _as_bool("STAMP_ENABLED", False)

        model = _env("STAMP_YOLO_MODEL_PATH")
        if not model:
            model = str(project_root / "yolov8n.pt")

        model_path = _as_path(model)

        # ✅ 不再 raise：找不到模型就自动关闭 stamp
        if enabled:
            if model_path is None or not model_path.exists():
                enabled = False

        return StampConfig(
            yolo_model_path=model_path or (project_root / "yolov8n.pt"),
            yolo_conf=_as_float("STAMP_YOLO_CONF", 0.25),
            enabled=enabled,
        )


# =========================
# OCR Config
# =========================
@dataclass(frozen=True)
class OcrConfig:
    backend: str = "paddle"
    paddleocr_home: Path | None = None

    @staticmethod
    def from_env(project_root: Path) -> "OcrConfig":
        backend = (_env("OCR_BACKEND", "paddle") or "paddle").strip().lower()
        if backend not in {"paddle"}:
            backend = "paddle"

        paddle_home = _as_path(_env("PADDLEOCR_HOME", str(project_root / "hf_models" / "models--paddlepaddle--PaddleOCR" / "cache")))
        if paddle_home is not None:
            paddle_home.mkdir(parents=True, exist_ok=True)

        return OcrConfig(
            backend=backend,
            paddleocr_home=paddle_home,
        )

    def apply_to_env(self) -> None:
        os.environ["OCR_BACKEND"] = self.backend
        _setenv_if(self.paddleocr_home, "PADDLEOCR_HOME")


# =========================
# MinerU Config
# =========================
@dataclass(frozen=True)
class MinerUConfig:
    hf_home: Path | None
    hf_hub_cache: Path | None
    transformers_cache: Path | None
    mineru_device_mode: str | None

    @staticmethod
    def from_env(project_root: Path) -> "MinerUConfig":
        default_cache = project_root / "hf_models"

        hf_home = _as_path(_env("HF_HOME", str(default_cache)))
        hf_hub_cache = _as_path(_env("HUGGINGFACE_HUB_CACHE", str(default_cache)))
        transformers_cache = _as_path(_env("TRANSFORMERS_CACHE", str(default_cache)))

        for p in (hf_home, hf_hub_cache, transformers_cache):
            if p is not None:
                p.mkdir(parents=True, exist_ok=True)

        return MinerUConfig(
            hf_home=hf_home,
            hf_hub_cache=hf_hub_cache,
            transformers_cache=transformers_cache,
            mineru_device_mode=_env("MINERU_DEVICE_MODE"),
        )

    def apply_to_env(self) -> None:
        _setenv_if(self.hf_home, "HF_HOME")
        _setenv_if(self.hf_hub_cache, "HUGGINGFACE_HUB_CACHE")
        _setenv_if(self.transformers_cache, "TRANSFORMERS_CACHE")

        if self.mineru_device_mode:
            os.environ["MINERU_DEVICE_MODE"] = self.mineru_device_mode


# =========================
# AppConfig (统一入口)
# =========================
@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    stamp: StampConfig
    ocr: OcrConfig
    mineru: MinerUConfig

    @staticmethod
    def load(project_root: Path) -> "AppConfig":
        return AppConfig(
            project_root=project_root,
            stamp=StampConfig.from_env(project_root),
            ocr=OcrConfig.from_env(project_root),
            mineru=MinerUConfig.from_env(project_root),
        )

    def apply_to_env(self) -> None:
        self.ocr.apply_to_env()
        self.mineru.apply_to_env()
        # stamp 不需要写 env，推理代码直接读 config


# =========================
# bootstrap / singleton
# =========================
@lru_cache(maxsize=1)
def get_config(project_root: Path) -> AppConfig:
    return AppConfig.load(project_root)


def bootstrap(project_root: Path) -> AppConfig:
    """
    统一启动入口：
    1. 加载 .env
    2. 生成 AppConfig
    3. 写回 OCR / MinerU 相关环境变量
    """
    env_path = project_root / ".env"
    if load_dotenv and env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)

    cfg = get_config(project_root)
    cfg.apply_to_env()
    return cfg

