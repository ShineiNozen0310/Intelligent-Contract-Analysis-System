import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from html import escape
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PySide6.QtCore import QLockFile, QSettings, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from .backend_runtime import BackendRuntime  # type: ignore
except Exception:
    from backend_runtime import BackendRuntime


DEFAULT_BASE_URL = "http://127.0.0.1:8000/contract"
POLL_INTERVAL_MS = 1000
ELAPSED_INTERVAL_MS = 1000
HEALTHCHECK_INTERVAL_MS = 3500
HISTORY_MAX = 50
LOG_MAX_LINES = 500
SETTINGS_ORG = "ContractReview"
SETTINGS_APP = "DesktopApp"
APP_VERSION = "2.2.1"
FILE_LOG_MAX_BYTES = 2 * 1024 * 1024
FILE_LOG_BACKUPS = 5

K_OVERVIEW = ["\u5ba1\u67e5\u6982\u8ff0", "overview", "summary"]
K_RISKS = ["\u98ce\u9669\u70b9", "risks", "risk_points", "\u98ce\u9669\u70b9\u53ca\u5efa\u8bae"]
K_IMPROVE = [
    "\u6539\u8fdb\u5efa\u8bae",
    "\u6539\u8fdb\u63aa\u65bd",
    "improvements",
    "improvement",
    "improvement_suggestions",
    "suggestions",
    "recommendations",
    "\u4f18\u5316\u5efa\u8bae",
    "\u5b8c\u5584\u5efa\u8bae",
    "\u4fee\u6539\u5efa\u8bae",
    "\u5ba1\u67e5\u5efa\u8bae",
]
K_TYPE = ["\u5408\u540c\u7c7b\u578b", "contract_type", "type", "type_l2"]
K_TYPE_DETAIL = ["\u5408\u540c\u7c7b\u578b\u660e\u7ec6", "contract_type_detail", "type_detail"]
K_KEY_FACTS = ["key_facts", "keyFacts", "\u5408\u540c\u5173\u952e\u8981\u7d20", "\u5173\u952e\u8981\u7d20", "\u5408\u540c\u8981\u7d20"]


def _app_data_dir() -> Path:
    qt_path = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    if qt_path:
        out = Path(qt_path)
    elif os.name == "nt":
        out = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "ContractReviewDesktop"
    else:
        out = Path.home() / ".contract_review" / "ContractReviewDesktop"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _build_file_logger() -> tuple[logging.Logger, Path]:
    log_dir = _app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "desktop.log"

    logger = logging.getLogger("ContractReviewDesktop")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=FILE_LOG_MAX_BYTES,
            backupCount=FILE_LOG_BACKUPS,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    return logger, log_file


class PdfDropLabel(QLabel):
    def __init__(self, on_file_picked):
        super().__init__("拖拽 PDF 到这里")
        self.on_file_picked = on_file_picked
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(84)
        self.setObjectName("dropZone")

    def dragEnterEvent(self, event):  # type: ignore[override]
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".pdf"):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):  # type: ignore[override]
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".pdf"):
                self.on_file_picked(Path(local))
                event.acceptProposedAction()
                return
        event.ignore()


class ContractReviewDesktopApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.setWindowTitle("合同智能审查")
        self.resize(1180, 800)

        self.session = requests.Session()
        self.session_lock = threading.Lock()
        self.ui_queue: queue.Queue = queue.Queue()

        self.selected_pdf: Optional[Path] = None
        self.current_job_id: Optional[int] = None
        self.current_job_filename: str = ""
        self.done_result: Optional[Dict[str, Any]] = None
        self.poll_started_at: Optional[float] = None
        self.elapsed_seconds = 0
        self.poll_inflight = False
        self.history: List[Dict[str, Any]] = []
        self.backend_runtime = BackendRuntime(log_fn=lambda msg: self.ui_queue.put(("runtime_log", msg)))
        self.logger, self.log_file = _build_file_logger()
        self._backend_online = False
        self._backend_booting = False
        self._backend_health_checking = False
        self._backend_auto_recovering = False

        self._build_ui()
        self._apply_theme()
        self._load_state()

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(120)
        self.ui_timer.timeout.connect(self._process_ui_queue)
        self.ui_timer.start()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self._poll_once)

        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(ELAPSED_INTERVAL_MS)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)

        self.health_timer = QTimer(self)
        self.health_timer.setInterval(HEALTHCHECK_INTERVAL_MS)
        self.health_timer.timeout.connect(self._check_backend_health_tick)
        self.health_timer.start()

        self._set_running(False)
        self._set_connection(False)
        self._log(f"App ready v{APP_VERSION}")
        self._log(f"desktop log: {self.log_file}")
        self._boot_backend_async(restart=False)

    def _build_ui(self):
        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        backend_box = QGroupBox("服务状态")
        backend_layout = QHBoxLayout(backend_box)
        base_url_label = QLabel("服务地址")
        backend_layout.addWidget(base_url_label)
        self.base_url_edit = QLineEdit(DEFAULT_BASE_URL)
        self.base_url_edit.setReadOnly(True)
        self.base_url_edit.setVisible(False)
        base_url_label.setVisible(False)
        backend_layout.addWidget(self.base_url_edit, 1)
        self.connect_btn = QPushButton("重启服务")
        self.connect_btn.clicked.connect(self.on_connect_clicked)
        backend_layout.addWidget(self.connect_btn)
        self.connection_badge = QLabel("离线")
        self.connection_badge.setObjectName("badgeOffline")
        self.connection_badge.setAlignment(Qt.AlignCenter)
        self.connection_badge.setMinimumWidth(90)
        backend_layout.addWidget(self.connection_badge)
        root_layout.addWidget(backend_box)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        input_box = QGroupBox("上传合同")
        input_layout = QVBoxLayout(input_box)
        self.drop_label = PdfDropLabel(self._set_selected_pdf)
        input_layout.addWidget(self.drop_label)
        self.file_label = QLabel("未选择文件")
        self.file_label.setWordWrap(True)
        input_layout.addWidget(self.file_label)

        input_btn_row = QHBoxLayout()
        self.pick_btn = QPushButton("选择 PDF")
        self.pick_btn.clicked.connect(self.on_pick_clicked)
        input_btn_row.addWidget(self.pick_btn)
        self.start_btn = QPushButton("开始审查")
        self.start_btn.clicked.connect(self.on_start_clicked)
        input_btn_row.addWidget(self.start_btn)
        input_layout.addLayout(input_btn_row)
        left_layout.addWidget(input_box)

        history_box = QGroupBox("任务历史")
        history_layout = QVBoxLayout(history_box)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(self.on_history_load_clicked)
        history_layout.addWidget(self.history_list)
        self.history_load_btn = QPushButton("加载选中任务")
        self.history_load_btn.clicked.connect(self.on_history_load_clicked)
        history_layout.addWidget(self.history_load_btn)
        self.clear_history_btn = QPushButton("清空任务历史")
        self.clear_history_btn.clicked.connect(self.on_clear_history_clicked)
        history_layout.addWidget(self.clear_history_btn)
        left_layout.addWidget(history_box, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        state_box = QGroupBox("任务状态")
        state_form = QFormLayout(state_box)
        self.job_id_label = QLabel("-")
        self.status_label = QLabel("空闲")
        self.stage_label = QLabel("-")
        self.progress_label = QLabel("0%")
        self.elapsed_label = QLabel("-")
        self.meta_label = QLabel("-")
        self.meta_label.setWordWrap(True)
        self.progressbar = QProgressBar()
        self.progressbar.setRange(0, 100)
        self.progressbar.setValue(0)
        state_form.addRow("文件名", self.job_id_label)
        state_form.addRow("状态", self.status_label)
        state_form.addRow("阶段", self.stage_label)
        state_form.addRow("进度", self.progress_label)
        state_form.addRow("耗时", self.elapsed_label)
        state_form.addRow("元信息", self.meta_label)
        state_form.addRow(self.progressbar)
        right_layout.addWidget(state_box)

        action_row = QHBoxLayout()
        self.export_btn = QPushButton("导出 PDF")
        self.export_btn.clicked.connect(self.on_export_clicked)
        action_row.addWidget(self.export_btn)
        self.copy_report_btn = QPushButton("复制报告")
        self.copy_report_btn.clicked.connect(self.on_copy_report_clicked)
        action_row.addWidget(self.copy_report_btn)
        self.copy_json_btn = QPushButton("复制 JSON")
        self.copy_json_btn.clicked.connect(self.on_copy_json_clicked)
        action_row.addWidget(self.copy_json_btn)
        self.back_btn = QPushButton("返回上传")
        self.back_btn.clicked.connect(self.on_clear_clicked)
        action_row.addWidget(self.back_btn)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.on_clear_clicked)
        action_row.addWidget(self.clear_btn)
        action_row.addStretch(1)
        right_layout.addLayout(action_row)

        self.tabs = QTabWidget()
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.json_text = QTextEdit()
        self.json_text.setReadOnly(True)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.tabs.addTab(self.summary_text, "报告")
        self.tabs.addTab(self.json_text, "JSON")
        self.tabs.addTab(self.log_text, "日志")
        right_layout.addWidget(self.tabs, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([350, 830])
        root_layout.addWidget(split, 1)

        self.export_btn.setEnabled(False)
        self.copy_report_btn.setEnabled(False)
        self.copy_json_btn.setEnabled(False)
        self.statusBar().showMessage("正在启动本地服务...")

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #f7f3ea,
                    stop:0.55 #eef5f3,
                    stop:1 #f9fbff
                );
            }
            QGroupBox {
                border: 1px solid #d7e3df;
                border-radius: 12px;
                margin-top: 16px;
                padding-top: 8px;
                background: rgba(255,255,255,0.94);
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                top: -1px;
                padding: 2px 6px;
                background: rgba(255,255,255,0.94);
            }
            QPushButton {
                border: none;
                border-radius: 9px;
                padding: 8px 13px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #4b6fae,
                    stop:1 #6f97bf
                );
                color: white;
                font-weight: 700;
            }
            QPushButton:hover { background: #5d83b8; }
            QPushButton:disabled { background: #a6b4c8; }
            QLineEdit, QListWidget, QTextEdit {
                border: 1px solid #d5dfec;
                border-radius: 9px;
                padding: 4px;
                background: #ffffff;
            }
            QLabel#dropZone {
                border: 2px dashed #8cb0c9;
                border-radius: 12px;
                background: #f7fbff;
                color: #27415f;
                font-weight: 600;
            }
            QLabel#badgeOnline {
                border: 1px solid #7fd89e;
                border-radius: 10px;
                background: #eafaf0;
                color: #0c7938;
                padding: 3px 6px;
            }
            QLabel#badgeOffline {
                border: 1px solid #f0b8b8;
                border-radius: 10px;
                background: #fff1f1;
                color: #a03d3d;
                padding: 3px 6px;
            }
            """
        )

    def _load_state(self):
        self.base_url_edit.setText(DEFAULT_BASE_URL)
        history_raw = self.settings.value("history_json", "[]")
        try:
            parsed = json.loads(history_raw if isinstance(history_raw, str) else "[]")
            if isinstance(parsed, list):
                self.history = parsed[:HISTORY_MAX]
        except Exception:
            self.history = []
        self._refresh_history()

    def _save_state(self):
        self.settings.setValue("base_url", self._normalized_base())
        self.settings.setValue("history_json", json.dumps(self.history[:HISTORY_MAX], ensure_ascii=False))

    def _log(self, text: str):
        now = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{now}] {text}")
        if self.log_text.document().blockCount() > LOG_MAX_LINES:
            kept = self.log_text.toPlainText().splitlines()[-LOG_MAX_LINES:]
            self.log_text.setPlainText("\n".join(kept))
            self.log_text.moveCursor(QTextCursor.End)
        try:
            self.logger.info(text)
        except Exception:
            pass

    def _set_connection(self, online: bool):
        self._backend_online = bool(online)
        if online:
            self.connection_badge.setObjectName("badgeOnline")
            self.connection_badge.setText("在线")
        else:
            self.connection_badge.setObjectName("badgeOffline")
            self.connection_badge.setText("离线")
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        if not self.poll_timer.isActive():
            self._set_running(False)

    def _set_running(self, running: bool):
        can_start = (not running) and self._backend_online and (not self._backend_booting)
        self.start_btn.setEnabled(can_start)
        self.connect_btn.setEnabled((not running) and (not self._backend_booting))
        self.pick_btn.setEnabled(not running)
        self.base_url_edit.setEnabled(False)

    def _normalized_base(self) -> str:
        return self.base_url_edit.text().strip().rstrip("/")

    def _api(self, path: str) -> str:
        return self._normalized_base() + path

    def _spawn(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _start_elapsed_counter(self):
        self.elapsed_seconds = 0
        self.elapsed_label.setText("0s")
        if not self.elapsed_timer.isActive():
            self.elapsed_timer.start()

    def _stop_elapsed_counter(self, clear: bool = False):
        if self.elapsed_timer.isActive():
            self.elapsed_timer.stop()
        if clear:
            self.elapsed_seconds = 0
            self.elapsed_label.setText("-")

    def _tick_elapsed(self):
        if self.poll_started_at is None:
            self._stop_elapsed_counter(clear=False)
            return
        self.elapsed_seconds += 1
        self.elapsed_label.setText(f"{self.elapsed_seconds}s")

    def _finalize_elapsed_counter(self, ensure_non_zero: bool = False):
        if self.poll_started_at is None:
            self._stop_elapsed_counter(clear=False)
            return
        elapsed_from_clock = max(0, int(time.time() - self.poll_started_at))
        if ensure_non_zero and elapsed_from_clock <= 0:
            elapsed_from_clock = 1
        self.elapsed_seconds = max(self.elapsed_seconds, elapsed_from_clock)
        self.elapsed_label.setText(f"{self.elapsed_seconds}s")
        self._stop_elapsed_counter(clear=False)

    def _check_backend_health_tick(self):
        if self._backend_booting or self._backend_health_checking:
            return
        self._backend_health_checking = True

        def worker():
            try:
                healthy = self.backend_runtime.is_healthy()
                self.ui_queue.put(("backend_health", healthy))
            except Exception:
                self.ui_queue.put(("backend_health", False))

        self._spawn(worker)

    def _boot_backend_async(self, restart: bool):
        if self._backend_booting:
            return
        self._backend_booting = True
        self._backend_health_checking = False
        self.connect_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self._set_connection(False)
        self.statusBar().showMessage("正在启动本地服务...")
        self.meta_label.setText("本地服务启动中...")
        if not self.summary_text.toPlainText().strip():
            self.summary_text.setPlainText("正在启动本地服务，请稍候...")

        def worker():
            try:
                if restart:
                    self.backend_runtime.stop()
                ok, msg = self.backend_runtime.start()
                self.ui_queue.put(("backend_started" if ok else "backend_failed", msg))
            except Exception as exc:
                self.ui_queue.put(("backend_failed", str(exc)))

        self._spawn(worker)

    def _ensure_csrf(self):
        with self.session_lock:
            resp = self.session.get(self._normalized_base() + "/api/health/", timeout=15)
        resp.raise_for_status()
        if "csrftoken" not in self.session.cookies:
            raise RuntimeError("No csrftoken cookie. Check backend URL.")

    def _set_selected_pdf(self, file_path: Path):
        if file_path.suffix.lower() != ".pdf":
            QMessageBox.warning(self, "文件错误", "请选择 PDF 文件。")
            return
        self.selected_pdf = file_path
        self.file_label.setText(str(file_path))
        if self.current_job_id is None:
            self.current_job_filename = file_path.name
            self.job_id_label.setText(file_path.name)
        self._log(f"selected file: {file_path}")

    def _task_display_name(self, filename: str, job_id: Optional[int]) -> str:
        name = (filename or "").strip()
        if name:
            return name
        if job_id:
            return f"任务#{job_id}"
        return "-"

    def _upsert_history(self, entry: Dict[str, Any]):
        job_id = entry.get("job_id")
        if not job_id:
            return
        for old in self.history:
            if old.get("job_id") == job_id:
                old.update(entry)
                break
        else:
            self.history.insert(0, entry)
        self.history = self.history[:HISTORY_MAX]
        self._refresh_history()

    def _refresh_history(self):
        self.history_list.clear()
        for entry in self.history:
            status = self._display_status(str(entry.get("status", "-")))
            stage = self._display_stage(str(entry.get("stage", "-")))
            filename = str(entry.get("filename", "") or "").strip()
            task_name = self._task_display_name(filename, entry.get("job_id"))
            text = f"{task_name} [{status}/{stage}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry)
            self.history_list.addItem(item)

    def _process_ui_queue(self):
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "runtime_log":
                self._log(f"[backend] {payload}")
                continue

            if kind == "backend_health":
                self._backend_health_checking = False
                healthy = bool(payload)
                if healthy:
                    if not self._backend_online:
                        self._set_connection(True)
                        self.statusBar().showMessage("本地服务已恢复")
                    continue
                if self._backend_booting:
                    continue
                if not self._backend_auto_recovering:
                    self._backend_auto_recovering = True
                    self._set_connection(False)
                    self._log("backend health check failed, auto-recovering")
                    self.statusBar().showMessage("检测到服务离线，正在自动恢复...")
                    self._boot_backend_async(restart=True)
                continue

            if kind == "backend_started":
                self._backend_booting = False
                self._backend_health_checking = False
                self._backend_auto_recovering = False
                self.connect_btn.setEnabled(True)
                self._set_connection(True)
                self._set_running(False)
                self._log(f"backend started: {payload}")
                self.statusBar().showMessage(str(payload))
                self.meta_label.setText("本地服务已就绪")
                if self.status_label.text() == "空闲":
                    self.summary_text.setPlainText("请选择 PDF 文件并开始审查。")
                continue

            if kind == "backend_failed":
                self._backend_booting = False
                self._backend_health_checking = False
                self._backend_auto_recovering = False
                self.connect_btn.setEnabled(True)
                self._set_connection(False)
                self._set_running(False)
                self.start_btn.setEnabled(False)
                self.poll_timer.stop()
                self._stop_elapsed_counter(clear=True)
                self.poll_inflight = False
                msg = str(payload)
                self.status_label.setText("服务异常")
                self.stage_label.setText("-")
                self.progressbar.setValue(0)
                self.progress_label.setText("0%")
                self.meta_label.setText("后端启动失败，点击“重启服务”重试")
                self.summary_text.setPlainText(
                    "后端启动失败：\n"
                    f"{msg}\n\n"
                    f"请检查 Redis、.venv 和依赖环境后，再点击“重启服务”。\n\n日志文件：{self.log_file}"
                )
                self._log(f"backend start failed: {msg}")
                self.statusBar().showMessage("后端启动失败")
                continue

            if kind == "error":
                self.poll_inflight = False
                self.poll_timer.stop()
                self._stop_elapsed_counter(clear=False)
                self._set_running(False)
                self._set_connection(False)
                self._log(f"error: {payload}")
                QMessageBox.critical(self, "错误", str(payload))
                continue

            if kind == "connected":
                self._set_connection(True)
                self._log(f"connected ({payload} ms)")
                self.statusBar().showMessage(f"已连接 ({payload} ms)")
                continue

            if kind == "started":
                if isinstance(payload, dict):
                    job_id = int(payload.get("job_id") or 0)
                    filename = str(payload.get("filename") or "").strip()
                else:
                    job_id = int(payload)
                    filename = ""
                if not filename and self.selected_pdf is not None:
                    filename = self.selected_pdf.name
                self.current_job_id = job_id
                self.current_job_filename = filename
                self.poll_started_at = time.time()
                self.job_id_label.setText(self._task_display_name(filename, job_id))
                self.status_label.setText(self._display_status("running"))
                self.stage_label.setText(self._display_stage("submitted"))
                self.progressbar.setValue(1)
                self.progress_label.setText("1%")
                self._start_elapsed_counter()
                self.meta_label.setText("stage=已提交   status=处理中")
                self.summary_text.setPlainText(f"任务已启动：{self._task_display_name(filename, job_id)}")
                self.json_text.clear()
                self.export_btn.setEnabled(False)
                self.copy_report_btn.setEnabled(False)
                self.copy_json_btn.setEnabled(False)
                self._upsert_history(
                    {
                        "job_id": job_id,
                        "status": "running",
                        "stage": "submitted",
                        "filename": filename,
                    }
                )
                self.poll_timer.start()
                self._poll_once()
                continue

            if kind == "poll":
                self.poll_inflight = False
                self._apply_poll_data(payload)
                continue

            if kind == "exported":
                self._log(f"pdf exported: {payload}")
                QMessageBox.information(self, "导出完成", f"已保存到:\n{payload}")
                continue

    def on_connect_clicked(self):
        self._log("manual backend restart")
        self._boot_backend_async(restart=True)

    def _dig(self, obj: Any, path: str) -> Any:
        cur = obj
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
        return cur

    def _first_non_empty(self, values: List[Any]) -> Any:
        for v in values:
            if isinstance(v, str):
                if v.strip():
                    return v.strip()
                continue
            if v is not None:
                return v
        return None

    def _as_list(self, value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return [x for x in value if x is not None]
        return [value]

    def _display_status(self, status: str) -> str:
        mapping = {
            "queued": "排队中",
            "running": "处理中",
            "done": "已完成",
            "error": "失败",
            "submitting": "提交中",
        }
        return mapping.get(status, status)

    def _display_stage(self, stage: str) -> str:
        mapping = {
            "submitted": "已提交",
            "start": "启动",
            "stamp_start": "盖章检测开始",
            "stamp_done": "盖章检测完成",
            "ocr_start": "OCR开始",
            "ocr_done": "OCR完成",
            "fallback_to_accurate": "切换高精度",
            "mineru_start": "高精度识别中",
            "mineru_retry_cpu": "高精度重试(CPU)",
            "mineru_done": "高精度完成",
            "mineru_no_md_fallback": "高精度无结果，转OCR",
            "mineru_empty_md_fallback": "高精度结果为空，转OCR",
            "accurate_fallback_done": "OCR兜底完成",
            "ocr_llm_fix_start": "OCR智能修正中",
            "ocr_llm_fix_done": "OCR智能修正完成",
            "ocr_zero_guard_failed": "OCR零误差校验未通过",
            "llm_start": "审查中",
            "done": "完成",
            "worker_error": "服务异常",
            "llm_error": "模型异常",
            "llm_timeout": "模型超时",
        }
        return mapping.get(stage, stage)

    def _guess_contract_type(self, markdown_text: str) -> str:
        text = markdown_text or ""
        if re.search(r"\u52b3\u52a8|\u8058\u7528", text):
            return "\u52b3\u52a8\u5408\u540c\uff08\u63a8\u65ad\uff09"
        if re.search(r"\u79df\u8d41|\u79df\u91d1|\u627f\u79df", text):
            return "\u79df\u8d41\u5408\u540c\uff08\u63a8\u65ad\uff09"
        if re.search(r"\u670d\u52a1|\u6280\u672f\u670d\u52a1|\u54a8\u8be2", text):
            return "\u670d\u52a1\u5408\u540c\uff08\u63a8\u65ad\uff09"
        if re.search(r"\u91c7\u8d2d|\u4f9b\u8d27|\u4e70\u5356", text):
            return "\u4e70\u5356/\u91c7\u8d2d\u5408\u540c\uff08\u63a8\u65ad\uff09"
        return "\u672a\u8bc6\u522b\uff08\u63a8\u65ad\uff09"

    def _extract_type_info(self, result_json: Dict[str, Any], markdown_text: str) -> tuple[str, str, Dict[str, Any]]:
        type_detail = {}
        for key in K_TYPE_DETAIL:
            if isinstance(result_json.get(key), dict):
                type_detail = result_json.get(key) or {}
                break
        if not type_detail:
            type_detail = (
                self._dig(result_json, "result.contract_type_detail")
                or self._dig(result_json, "review.contract_type_detail")
                or self._dig(result_json, "data.contract_type_detail")
                or {}
            )
            if not isinstance(type_detail, dict):
                type_detail = {}

        type_l2 = self._first_non_empty(
            [
                type_detail.get("type_l2"),
                result_json.get("\u5408\u540c\u7c7b\u578b"),
                result_json.get("contract_type"),
                result_json.get("type"),
            ]
        )
        if not type_l2:
            type_l2 = self._guess_contract_type(markdown_text)
            return str(type_l2), "\u6765\u6e90\uff1amarkdown \u63a8\u65ad", type_detail
        return str(type_l2), "\u6765\u6e90\uff1aresult_json", type_detail

    def _extract_stamp_text(self, result_json: Dict[str, Any]) -> tuple[str, str]:
        stamp = result_json.get("\u662f\u5426\u76d6\u7ae0")
        if stamp is None:
            stamp_status = result_json.get("stamp_status")
            if stamp_status == "YES":
                stamp = "\u662f"
            elif stamp_status == "NO":
                stamp = "\u5426"
            elif stamp_status == "UNCERTAIN":
                stamp = "\u4e0d\u786e\u5b9a"
        if stamp in (True, "\u662f", "YES"):
            return "\u662f", "#0c7b48"
        if stamp in (False, "\u5426", "NO"):
            return "\u5426", "#b42318"
        if stamp is None:
            return "\u672a\u63d0\u53ca", "#6b7280"
        return str(stamp), "#6b7280"

    def _extract_key_facts(self, result_json: Dict[str, Any], markdown_text: str) -> Dict[str, str]:
        bucket: Dict[str, Any] = {}
        for key in K_KEY_FACTS:
            if isinstance(result_json.get(key), dict):
                bucket = result_json.get(key) or {}
                break
        if not bucket:
            bucket = (
                self._dig(result_json, "result.key_facts")
                or self._dig(result_json, "review.key_facts")
                or self._dig(result_json, "data.key_facts")
                or {}
            )
            if not isinstance(bucket, dict):
                bucket = {}

        def pick(d: Dict[str, Any], keys: List[str]) -> Optional[str]:
            for k in keys:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
                if isinstance(v, (int, float)):
                    return str(v)
            return None

        name = pick(bucket, ["\u5408\u540c\u540d\u79f0", "\u534f\u8bae\u540d\u79f0", "contract_name", "name"])
        party_a = pick(bucket, ["\u7532\u65b9", "\u7532\u65b9\u540d\u79f0", "partyA", "party_a"])
        party_b = pick(bucket, ["\u4e59\u65b9", "\u4e59\u65b9\u540d\u79f0", "partyB", "party_b"])
        amount = pick(bucket, ["\u91d1\u989d", "\u5408\u540c\u91d1\u989d", "\u603b\u91d1\u989d", "amount", "contract_amount"])
        term = pick(bucket, ["\u671f\u9650", "\u5408\u540c\u671f\u9650", "\u6709\u6548\u671f", "term", "contract_term"])

        text = markdown_text or ""
        if not name:
            m = re.search(r"(\u5408\u540c\u540d\u79f0|\u534f\u8bae\u540d\u79f0)[:\uff1a]\s*([^\n\uff0c\u3002\uff1b;]+)", text)
            if m:
                name = m.group(2).strip()
        if not party_a:
            m = re.search(r"\u7532\u65b9[:\uff1a]\s*([^\n\uff0c\u3002\uff1b;]+)", text)
            if m:
                party_a = m.group(1).strip()
        if not party_b:
            m = re.search(r"\u4e59\u65b9[:\uff1a]\s*([^\n\uff0c\u3002\uff1b;]+)", text)
            if m:
                party_b = m.group(1).strip()

        return {
            "\u5408\u540c\u540d\u79f0": name or "\u672a\u63d0\u53ca",
            "\u7532\u65b9": party_a or "\u672a\u63d0\u53ca",
            "\u4e59\u65b9": party_b or "\u672a\u63d0\u53ca",
            "\u91d1\u989d": amount or "\u672a\u63d0\u53ca",
            "\u671f\u9650": term or "\u672a\u63d0\u53ca",
        }

    def _extract_items(self, result_json: Dict[str, Any], keys: List[str]) -> List[Any]:
        cands: List[Any] = []
        for k in keys:
            cands.append(result_json.get(k))
        for prefix in ("result", "review", "data"):
            node = result_json.get(prefix)
            if isinstance(node, dict):
                for k in keys:
                    cands.append(node.get(k))
        picked = self._first_non_empty(cands)
        return self._as_list(picked)

    def _extract_improvement_suggestions(self, markdown_text: str) -> List[Dict[str, str]]:
        if not markdown_text:
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for raw_line in markdown_text.splitlines():
            s = raw_line.strip().lstrip("-*•").strip()
            if not s:
                continue
            m = re.search(r"(?:改进建议|优化建议|完善建议|修改建议|建议)[:：]\s*(.+)", s)
            if not m:
                continue
            suggestion = (m.group(1) or "").strip("；;。 ")
            if len(suggestion) < 6:
                continue
            if suggestion in seen:
                continue
            seen.add(suggestion)
            out.append({"title": "改进建议", "problem": "", "suggestion": suggestion})
            if len(out) >= 8:
                break
        return out

    def _fallback_improvements_from_risks(self, risks: List[Any]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()
        for item in risks:
            if not isinstance(item, dict):
                continue
            suggestion = self._first_non_empty(
                [item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("建议"), item.get("修改建议")]
            )
            if not suggestion:
                continue
            suggestion = str(suggestion).strip()
            if len(suggestion) < 6 or suggestion in seen:
                continue
            seen.add(suggestion)
            title = self._first_non_empty([item.get("title"), item.get("name"), item.get("item"), "改进建议"]) or "改进建议"
            problem = self._first_non_empty([item.get("problem"), item.get("issue"), item.get("description"), item.get("问题")]) or ""
            out.append({"title": str(title), "problem": str(problem), "suggestion": suggestion})
            if len(out) >= 8:
                break
        return out

    def _has_meaningful_items(self, items: List[Any]) -> bool:
        for item in items:
            if isinstance(item, str) and item.strip():
                return True
            if isinstance(item, dict):
                title = self._first_non_empty([item.get("title"), item.get("name"), item.get("item"), item.get("风险点"), item.get("问题点")])
                problem = self._first_non_empty([item.get("problem"), item.get("issue"), item.get("desc"), item.get("description"), item.get("问题")])
                suggestion = self._first_non_empty(
                    [item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("建议"), item.get("修改建议")]
                )
                if title or problem or suggestion:
                    return True
        return False

    def _render_items_html(self, title: str, items: List[Any]) -> str:
        if not items:
            return (
                f"<h3>{escape(title)}</h3>"
                "<div class='empty'>\u672a\u8bc6\u522b\u5230\u76f8\u5173\u5185\u5bb9</div>"
            )
        lines = [f"<h3>{escape(title)}</h3><ol>"]
        for item in items:
            if isinstance(item, str):
                lines.append(f"<li>{escape(item)}</li>")
                continue
            if isinstance(item, dict):
                t = self._first_non_empty(
                    [item.get("title"), item.get("name"), item.get("item"), item.get("\u98ce\u9669\u70b9"), item.get("\u95ee\u9898\u70b9")]
                ) or title
                level = self._first_non_empty([item.get("level"), item.get("severity"), item.get("risk_level"), item.get("\u98ce\u9669\u7b49\u7ea7")])
                problem = self._first_non_empty(
                    [item.get("problem"), item.get("issue"), item.get("desc"), item.get("description"), item.get("\u95ee\u9898")]
                )
                suggestion = self._first_non_empty(
                    [item.get("suggestion"), item.get("advice"), item.get("fix"), item.get("solution"), item.get("\u5efa\u8bae"), item.get("\u4fee\u6539\u5efa\u8bae")]
                )
                head = escape(str(t))
                if level:
                    head += f" <span class='muted'>({escape(str(level))})</span>"
                lines.append("<li>")
                lines.append(f"<div><b>{head}</b></div>")
                if problem:
                    lines.append(f"<div><b>\u95ee\u9898\uff1a</b>{escape(str(problem))}</div>")
                if suggestion:
                    lines.append(f"<div>{escape(str(suggestion))}</div>")
                lines.append("</li>")
                continue
            lines.append(f"<li>{escape(str(item))}</li>")
        lines.append("</ol>")
        return "".join(lines)

    def _build_report_html(self, result_json: Dict[str, Any], markdown_text: str) -> str:
        contract_type, type_note, type_detail = self._extract_type_info(result_json, markdown_text)
        stamp_text, stamp_color = self._extract_stamp_text(result_json)
        key_facts = self._extract_key_facts(result_json, markdown_text)
        overview = self._first_non_empty(
            [
                result_json.get(K_OVERVIEW[0]),
                result_json.get(K_OVERVIEW[1]),
                result_json.get(K_OVERVIEW[2]),
                self._dig(result_json, "result.overview"),
                self._dig(result_json, "review.overview"),
                self._dig(result_json, "data.overview"),
            ]
        ) or "\u6682\u672a\u751f\u6210"
        risks = self._extract_items(result_json, K_RISKS)
        # 风险点只展示风险本身，不展示建议字段。
        for item in risks:
            if isinstance(item, dict):
                item["suggestion"] = ""
        improvements = self._extract_items(result_json, K_IMPROVE)

        conf_text = "-"
        if isinstance(type_detail, dict):
            conf = type_detail.get("confidence")
            if isinstance(conf, (int, float)):
                conf_text = f"{round(float(conf) * 100)}%"
            elif conf is not None:
                conf_text = str(conf)

        facts_rows = "".join(
            f"<tr><td class='k'>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in key_facts.items()
        )

        risks_html = self._render_items_html("\u98ce\u9669\u70b9", risks)
        improve_html = self._render_items_html("\u6539\u8fdb\u5efa\u8bae", improvements)

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
<div class='title'>\u5408\u540c\u5ba1\u67e5\u62a5\u544a</div>
<div class='meta'><b>\u5408\u540c\u7c7b\u578b\uff1a</b>{escape(contract_type)} | <b>\u76d6\u7ae0\uff1a</b><span class='stamp'>{escape(stamp_text)}</span></div>
<div class='meta'><b>\u7c7b\u578b\u7f6e\u4fe1\u5ea6\uff1a</b>{escape(conf_text)} | <b>\u7c7b\u578b\u6765\u6e90\uff1a</b>{escape(type_note)}</div>
<div class='panel'><h3>\u5408\u540c\u5173\u952e\u8981\u7d20</h3><table>{facts_rows}</table></div>
<div class='panel'><h3>\u5ba1\u67e5\u6982\u8ff0</h3><div>{escape(str(overview))}</div></div>
<div class='panel'>{risks_html}</div>
<div class='panel'>{improve_html}</div>
"""

    def on_pick_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF Files (*.pdf);;All Files (*)")
        if path:
            self._set_selected_pdf(Path(path))

    def on_start_clicked(self):
        if self._backend_booting:
            QMessageBox.information(self, "服务启动中", "本地服务正在启动，请稍候后再试。")
            return
        if not self.backend_runtime.is_healthy():
            QMessageBox.warning(self, "服务未就绪", "本地服务未就绪，正在尝试自动重启。")
            self._boot_backend_async(restart=True)
            return
        if self.selected_pdf is None:
            QMessageBox.warning(self, "未选择文件", "请先选择或拖拽 PDF。")
            return
        self._set_running(True)
        self.poll_started_at = None
        self._stop_elapsed_counter(clear=True)
        self.status_label.setText("提交中")
        self.stage_label.setText("上传中")
        self.progressbar.setValue(0)
        self.progress_label.setText("0%")
        self.elapsed_label.setText("-")
        self.meta_label.setText("任务提交中...")
        self.export_btn.setEnabled(False)
        self.copy_report_btn.setEnabled(False)
        self.copy_json_btn.setEnabled(False)
        self._log("start analyzing")

        def worker():
            try:
                self._ensure_csrf()
                csrf = self.session.cookies.get("csrftoken", "")
                with self.selected_pdf.open("rb") as fp:
                    files = {"file": (self.selected_pdf.name, fp, "application/pdf")}
                    with self.session_lock:
                        resp = self.session.post(
                            self._api("/api/start/"),
                            files=files,
                            headers={"X-CSRFToken": csrf},
                            timeout=120,
                        )
                if resp.status_code != 200:
                    raise RuntimeError(f"Start failed: {resp.status_code} {resp.text[:300]}")
                body = resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"Start failed: {body}")
                self.ui_queue.put(
                    (
                        "started",
                        {
                            "job_id": int(body["job_id"]),
                            "filename": self.selected_pdf.name if self.selected_pdf else "",
                        },
                    )
                )
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        self._spawn(worker)

    def _poll_once(self):
        if self.current_job_id is None or self.poll_inflight:
            return
        self.poll_inflight = True
        job_id = self.current_job_id

        def worker():
            try:
                with self.session_lock:
                    resp = self.session.get(self._api(f"/api/status/{job_id}/"), timeout=20)
                if resp.status_code != 200:
                    raise RuntimeError(f"Status failed: {resp.status_code} {resp.text[:300]}")
                self.ui_queue.put(("poll", resp.json()))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        self._spawn(worker)

    def _apply_poll_data(self, data: Dict[str, Any]):
        status = str(data.get("status", "running"))
        stage = str(data.get("stage", "-"))
        progress = max(0, min(100, int(data.get("progress", 0) or 0)))
        status_ui = self._display_status(status)
        stage_ui = self._display_stage(stage)

        self.current_job_id = int(data.get("job_id", self.current_job_id or 0) or 0) or self.current_job_id
        latest_filename = str(data.get("filename", "") or "").strip()
        if latest_filename:
            self.current_job_filename = latest_filename
        display_name = self._task_display_name(self.current_job_filename, self.current_job_id)
        self.job_id_label.setText(display_name)
        self.status_label.setText(status_ui)
        self.stage_label.setText(stage_ui)
        self.progressbar.setValue(progress)
        self.progress_label.setText(f"{progress}%")
        self.meta_label.setText(f"stage={stage_ui}   status={status_ui}")
        self.statusBar().showMessage(f"{display_name} {status_ui} ({progress}%)")

        self._upsert_history(
            {
                "job_id": self.current_job_id,
                "status": status,
                "stage": stage,
                "filename": self.current_job_filename,
            }
        )

        if status == "done":
            self.poll_timer.stop()
            self._finalize_elapsed_counter(ensure_non_zero=True)
            self._set_running(False)
            self.done_result = data
            self._render_done(data)
            self.export_btn.setEnabled(True)
            self.copy_report_btn.setEnabled(True)
            self.copy_json_btn.setEnabled(isinstance(data.get("result_json"), dict))
            self._log(f"job {self.current_job_id} done")
            return
        if status == "error":
            self.poll_timer.stop()
            self._finalize_elapsed_counter(ensure_non_zero=True)
            self._set_running(False)
            self.export_btn.setEnabled(False)
            err = data.get("error") or "未知错误"
            self._log(f"job {self.current_job_id} error: {err}")
            QMessageBox.critical(self, "任务失败", str(err))
            return

    def _render_done(self, data: Dict[str, Any]):
        result_json = data.get("result_json")
        result_markdown = data.get("result_markdown") or ""
        if isinstance(result_json, dict):
            self.json_text.setPlainText(json.dumps(result_json, ensure_ascii=False, indent=2))
            self.summary_text.setHtml(self._build_report_html(result_json, result_markdown))
        else:
            self.json_text.clear()
            md = result_markdown if result_markdown else "No report content."
            self.summary_text.setPlainText(md)
        self.tabs.setCurrentWidget(self.summary_text)

    def on_export_clicked(self):
        if self.current_job_id is None:
            QMessageBox.warning(self, "无任务", "请先执行审查任务。")
            return
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存 PDF",
            f"{Path(self.current_job_filename).stem}_审查报告.pdf" if self.current_job_filename else f"contract_review_job_{self.current_job_id}.pdf",
            "PDF Files (*.pdf);;All Files (*)",
        )
        if not out_path:
            return
        target = Path(out_path)

        def worker():
            try:
                with self.session_lock:
                    resp = self.session.get(self._api(f"/api/export_pdf/{self.current_job_id}/"), timeout=120)
                if resp.status_code != 200:
                    raise RuntimeError(f"Export failed: {resp.status_code} {resp.text[:300]}")
                target.write_bytes(resp.content)
                self.ui_queue.put(("exported", str(target)))
            except Exception as exc:
                self.ui_queue.put(("error", exc))

        self._spawn(worker)

    def on_copy_report_clicked(self):
        report_text = self.summary_text.toPlainText().strip()
        if not report_text:
            QMessageBox.warning(self, "无报告", "当前没有可复制的报告内容。")
            return
        QApplication.clipboard().setText(report_text)
        self.statusBar().showMessage("报告已复制")
        self._log("report copied")

    def on_copy_json_clicked(self):
        if not (self.done_result and isinstance(self.done_result.get("result_json"), dict)):
            QMessageBox.warning(self, "无JSON", "当前没有 result_json。")
            return
        text = json.dumps(self.done_result["result_json"], ensure_ascii=False, indent=2)
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("JSON已复制")
        self._log("json copied")

    def on_history_load_clicked(self):
        item = self.history_list.currentItem()
        if item is None:
            QMessageBox.information(self, "历史任务", "请先选择一个历史任务。")
            return
        row = item.data(Qt.UserRole)
        if not isinstance(row, dict) or not row.get("job_id"):
            return
        self.current_job_id = int(row["job_id"])
        self.current_job_filename = str(row.get("filename") or "").strip()
        self.job_id_label.setText(self._task_display_name(self.current_job_filename, self.current_job_id))
        self.poll_started_at = time.time()
        self._start_elapsed_counter()
        self._log(f"load history job {self.current_job_id}")
        self._poll_once()

    def on_clear_history_clicked(self):
        if not self.history:
            self.statusBar().showMessage("任务历史已为空")
            return
        self.history = []
        self._refresh_history()
        self._save_state()
        self._log("history cleared")
        self.statusBar().showMessage("任务历史已清空")

    def on_clear_clicked(self):
        self.poll_timer.stop()
        self._stop_elapsed_counter(clear=True)
        self.poll_inflight = False
        self._set_running(False)
        self.current_job_id = None
        self.done_result = None
        self.poll_started_at = None
        self.summary_text.clear()
        self.json_text.clear()
        self.status_label.setText("空闲")
        self.stage_label.setText("-")
        self.progressbar.setValue(0)
        self.progress_label.setText("0%")
        self.meta_label.setText("-")
        self.job_id_label.setText("-")
        self.current_job_filename = ""
        self.export_btn.setEnabled(False)
        self.copy_report_btn.setEnabled(False)
        self.copy_json_btn.setEnabled(False)
        self.statusBar().showMessage("已清空")
        self._log("cleared")

    def _stop_legacy_services(self):
        root = Path(__file__).resolve().parents[1]
        start_bat = root / "start_all.bat"
        if not start_bat.exists():
            return
        try:
            subprocess.run(
                ["cmd", "/c", str(start_bat), "stop"],
                cwd=str(root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    def closeEvent(self, event):  # type: ignore[override]
        self.health_timer.stop()
        try:
            self.backend_runtime.stop()
        except Exception as exc:
            self._log(f"backend stop failed: {exc}")
        self._stop_legacy_services()
        self._save_state()
        super().closeEvent(event)


def main():
    app = QApplication([])
    app_data = _app_data_dir()
    lock_file = app_data / "ContractReviewDesktop.lock"
    lock = QLockFile(str(lock_file))
    lock.setStaleLockTime(30 * 1000)
    if not lock.tryLock(100):
        QMessageBox.information(None, "提示", "应用已在运行，请勿重复启动。")
        return
    app._single_instance_lock = lock  # type: ignore[attr-defined]

    win = ContractReviewDesktopApp()
    win.show()
    app.exec()


if __name__ == "__main__":
    main()
