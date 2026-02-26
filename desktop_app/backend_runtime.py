from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
DJANGO_HEALTH_URL = "http://127.0.0.1:8000/contract/api/health/"
WORKER_HEALTH_URL = "http://127.0.0.1:8001/healthz"


class BackendRuntime:
    """
    Start/stop Django + Worker + Celery in background without showing console windows.
    """

    def __init__(self, project_root: Optional[Path] = None, log_fn: Optional[Callable[[str], None]] = None):
        self.project_root = (project_root or self._detect_project_root()).resolve()
        self.log_fn = log_fn
        self._session = requests.Session()
        self._lock = threading.Lock()

        self._procs: Dict[str, subprocess.Popen] = {}
        self._log_tail: Dict[str, List[str]] = {"django": [], "worker": [], "celery": []}
        self._ready_flags: Dict[str, bool] = {"django": False, "worker": False, "celery": False}

        self.last_error: str = ""
        self.last_start_message: str = ""

    def _emit(self, text: str) -> None:
        if self.log_fn:
            self.log_fn(text)

    def _detect_project_root(self) -> Path:
        candidates: List[Path] = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.extend([exe_dir, exe_dir.parent, Path.cwd().resolve()])
        candidates.extend([Path(__file__).resolve().parents[1], Path.cwd().resolve()])

        for cand in candidates:
            if (cand / "manage.py").exists():
                return cand
        return Path.cwd().resolve()

    def _resolve_python(self) -> Optional[Path]:
        candidates: List[Path] = []
        env_py = os.environ.get("CONTRACT_REVIEW_PYTHON", "").strip()
        if env_py:
            candidates.append(Path(env_py))

        if os.name == "nt":
            candidates.append(self.project_root / ".venv" / "Scripts" / "python.exe")
        else:
            candidates.append(self.project_root / ".venv" / "bin" / "python")

        if sys.executable:
            candidates.append(Path(sys.executable))
        which_py = shutil.which("python")
        if which_py:
            candidates.append(Path(which_py))

        for cand in candidates:
            try:
                resolved = cand.expanduser().resolve()
            except Exception:
                continue
            if resolved.exists() and "python" in resolved.name.lower():
                return resolved
        return None

    def _append_log(self, name: str, line: str) -> None:
        buf = self._log_tail.setdefault(name, [])
        buf.append(line)
        if len(buf) > 160:
            del buf[:-160]

        lower = line.lower()
        if name == "worker" and ("application startup complete" in lower or "uvicorn running on" in lower):
            self._ready_flags["worker"] = True
        if name == "celery" and "ready." in lower:
            self._ready_flags["celery"] = True

    def _reader_thread(self, name: str, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                self._append_log(name, line)
                self._emit(f"[{name}] {line}")
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _spawn(self, name: str, cmd: List[str], env: Dict[str, str]) -> subprocess.Popen:
        creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.project_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        threading.Thread(target=self._reader_thread, args=(name, proc), daemon=True).start()
        self._procs[name] = proc
        self._emit(f"[{name}] started (pid={proc.pid})")
        return proc

    def _check_http_ok(self, url: str, timeout: float = 1.2) -> bool:
        try:
            resp = self._session.get(url, timeout=timeout)
            return resp.status_code < 500
        except Exception:
            return False

    def _service_exit_error(self) -> Optional[str]:
        for name, proc in self._procs.items():
            code = proc.poll()
            if code is None:
                continue
            tail = "\n".join(self._log_tail.get(name, [])[-30:])
            msg = f"{name} exited unexpectedly (code={code})."
            if tail:
                msg += f"\n--- {name} logs ---\n{tail}"
            return msg
        return None

    def _summary_tail(self) -> str:
        parts: List[str] = []
        for name in ("django", "worker", "celery"):
            tail = self._log_tail.get(name, [])
            if not tail:
                continue
            parts.append(f"[{name}]")
            parts.extend(tail[-10:])
        return "\n".join(parts)

    def _redis_endpoint(self) -> Optional[Tuple[str, int]]:
        broker = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
        try:
            parsed = urlparse(broker)
        except Exception:
            return None
        if parsed.scheme not in {"redis", "rediss"}:
            return None
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 6379)
        return host, port

    def _check_local_redis(self) -> Tuple[bool, str]:
        endpoint = self._redis_endpoint()
        if endpoint is None:
            return True, ""
        host, port = endpoint
        if host not in {"127.0.0.1", "localhost"}:
            return True, ""
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True, ""
        except Exception:
            return False, f"Redis not reachable at {host}:{port}. Please start Redis first."

    def is_healthy(self) -> bool:
        django_ok = self._check_http_ok(DJANGO_HEALTH_URL)
        worker_ok = self._check_http_ok(WORKER_HEALTH_URL)
        return django_ok and worker_ok

    def status_snapshot(self) -> Dict[str, object]:
        processes: Dict[str, Dict[str, object]] = {}
        for name, proc in self._procs.items():
            code = proc.poll()
            processes[name] = {"pid": proc.pid, "alive": code is None, "exit_code": code}
        return {
            "healthy": self.is_healthy(),
            "processes": processes,
            "ready_flags": dict(self._ready_flags),
            "last_error": self.last_error,
            "last_start_message": self.last_start_message,
        }

    def start(self, timeout_s: int = 55) -> Tuple[bool, str]:
        with self._lock:
            self.last_error = ""
            self.last_start_message = ""

            if self.is_healthy():
                self.last_start_message = "Backend is already healthy."
                self._emit(self.last_start_message)
                return True, self.last_start_message

            redis_ok, redis_msg = self._check_local_redis()
            if not redis_ok:
                self.last_error = redis_msg
                return False, redis_msg

            self._stop_locked()
            self._log_tail = {"django": [], "worker": [], "celery": []}
            self._ready_flags = {"django": False, "worker": False, "celery": False}

            python_exe = self._resolve_python()
            if python_exe is None:
                self.last_error = "Python runtime not found. Please ensure .venv exists or set CONTRACT_REVIEW_PYTHON."
                return False, self.last_error

            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")

            worker_cmd = [
                str(python_exe),
                "-m",
                "uvicorn",
                "contract_review_worker.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8001",
            ]
            celery_cmd = [
                str(python_exe),
                "-m",
                "celery",
                "-A",
                "contract_review_worker.celery_app",
                "worker",
                "-l",
                "info",
                "-P",
                "solo",
            ]
            django_cmd = [
                str(python_exe),
                "manage.py",
                "runserver",
                "127.0.0.1:8000",
                "--noreload",
            ]

            try:
                self._spawn("worker", worker_cmd, env)
                self._spawn("celery", celery_cmd, env)
                self._spawn("django", django_cmd, env)
            except Exception as exc:
                self._stop_locked()
                self.last_error = f"Failed to spawn backend process: {exc}"
                return False, self.last_error

            start_ts = time.time()
            deadline = start_ts + max(15, timeout_s)
            while time.time() < deadline:
                exit_err = self._service_exit_error()
                if exit_err:
                    self._stop_locked()
                    self.last_error = exit_err
                    return False, exit_err

                if not self._ready_flags["django"]:
                    self._ready_flags["django"] = self._check_http_ok(DJANGO_HEALTH_URL)
                if not self._ready_flags["worker"]:
                    self._ready_flags["worker"] = self._check_http_ok(WORKER_HEALTH_URL)
                if not self._ready_flags["celery"]:
                    celery_proc = self._procs.get("celery")
                    if celery_proc and celery_proc.poll() is None and (time.time() - start_ts) > 8:
                        self._ready_flags["celery"] = True

                if all(self._ready_flags.values()):
                    self.last_start_message = "Local backend startup completed."
                    self._emit(self.last_start_message)
                    return True, self.last_start_message

                time.sleep(0.6)

            not_ready = [k for k, ok in self._ready_flags.items() if not ok]
            details = self._summary_tail()
            self._stop_locked()
            msg = f"Backend startup timeout. Services not ready: {', '.join(not_ready)}."
            if details:
                msg += f"\n--- Recent logs ---\n{details}"
            self.last_error = msg
            return False, msg

    def _stop_locked(self) -> None:
        if not self._procs:
            return
        procs = list(self._procs.items())
        for _, proc in procs:
            if proc.poll() is not None:
                continue
            try:
                proc.terminate()
            except Exception:
                pass
        for _, proc in procs:
            if proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        for _, proc in procs:
            if proc.poll() is not None:
                continue
            try:
                proc.kill()
            except Exception:
                pass
        self._procs.clear()
        self._ready_flags = {"django": False, "worker": False, "celery": False}

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
