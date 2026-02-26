from __future__ import annotations

import os
from pathlib import Path

from celery import Celery

from contract_review_worker.app_config import bootstrap

BASE_DIR = Path(__file__).resolve().parents[1]
bootstrap(BASE_DIR)

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


app = Celery("contract_review_worker", broker=BROKER_URL, backend=RESULT_BACKEND)

app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.environ.get("CELERY_TIMEZONE", "Asia/Shanghai"),
    enable_utc=True,
    task_ignore_result=_env_flag("CELERY_TASK_IGNORE_RESULT", True),
    result_expires=_env_int("CELERY_RESULT_EXPIRES", 3600),
    worker_prefetch_multiplier=_env_int("CELERY_PREFETCH_MULTIPLIER", 1),
    broker_pool_limit=_env_int("CELERY_BROKER_POOL_LIMIT", 10),
    broker_connection_retry_on_startup=True,
)

app.autodiscover_tasks(["contract_review_worker"])
