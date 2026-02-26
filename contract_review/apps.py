from django.apps import AppConfig
from pathlib import Path


class ContractReviewConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "contract_review"

    def ready(self) -> None:
        """Django 启动时统一初始化 Stamp / MinerU / OCR 配置。

        这样无论是 Django 端直接调用解析/盖章能力，还是仅作为前端服务，
        环境变量（HF_HOME / PADDLEOCR_HOME / MINERU_DEVICE_MODE 等）都能保持一致。
        """
        try:
            from contract_review_worker.app_config import bootstrap
        except Exception:
            # worker 依赖未安装 / 不需要 worker 时，不阻塞 Django 启动
            return

        project_root = Path(__file__).resolve().parents[1]
        try:
            bootstrap(project_root)
        except Exception:
            # 生产环境建议打日志；这里避免因配置缺失导致 Django 起不来
            return
