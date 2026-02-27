from __future__ import annotations

import os
import shlex
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
DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8002/v1"


class BackendRuntime:
    """
    Start/stop Django + Worker + Celery in background without showing console windows.
    """

    def __init__(self, project_root: Optional[Path] = None, log_fn: Optional[Callable[[str], None]] = None):
        self.project_root = (project_root or self._detect_project_root()).resolve()
        self._load_project_env()
        self.log_fn = log_fn
        self._session = requests.Session()
        self._lock = threading.Lock()

        self._procs: Dict[str, subprocess.Popen] = {}
        self._want_vllm = self._should_start_vllm()
        self._log_tail: Dict[str, List[str]] = {}
        self._ready_flags: Dict[str, bool] = {}
        self._reset_runtime_state()

        self.last_error: str = ""
        self.last_start_message: str = ""

    def _load_project_env(self) -> None:
        env_path = self.project_root / ".env"
        if not env_path.exists():
            return
        try:
            lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                # Keep startup behavior deterministic: project .env wins over inherited shell env.
                os.environ[key] = value

    def _reset_runtime_state(self) -> None:
        self._log_tail = {"django": [], "worker": [], "celery": [], "vllm": []}
        self._ready_flags = {
            "django": False,
            "worker": False,
            "celery": False,
            "vllm": (not self._want_vllm),
        }

    def _emit(self, text: str) -> None:
        if self.log_fn:
            self.log_fn(text)

    def _env_flag(self, key: str, default: bool = False) -> bool:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            return default
        return raw.lower() in {"1", "true", "yes", "y", "on"}

    def _env_int(self, key: str, default: int) -> int:
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except Exception:
            return default

    def _should_start_vllm(self) -> bool:
        local_primary = self._is_local_primary_provider()
        explicit = os.environ.get("VLLM_ENABLED")
        if explicit is not None and str(explicit).strip() != "":
            enabled = self._env_flag("VLLM_ENABLED", False)
            if local_primary and not enabled:
                return True
            return enabled
        return local_primary

    def _provider_name(self) -> str:
        primary = (os.environ.get("LLM_PRIMARY_PROVIDER") or "").strip().lower()
        if primary:
            return primary
        return (os.environ.get("LLM_PROVIDER") or "remote").strip().lower()

    def _is_local_provider_name(self, name: str) -> bool:
        return (name or "").strip().lower() in {"local_vllm", "local", "vllm"}

    def _is_local_primary_provider(self) -> bool:
        return self._is_local_provider_name(self._provider_name())

    def _require_local_vllm(self) -> bool:
        default = self._is_local_primary_provider()
        return self._env_flag("LLM_REQUIRE_LOCAL_VLLM", default)

    def _route_debug_line(self) -> str:
        return (
            "[vllm] route config: "
            f"provider={self._provider_name()} "
            f"llm_provider={os.environ.get('LLM_PROVIDER', '')} "
            f"llm_primary={os.environ.get('LLM_PRIMARY_PROVIDER', '')} "
            f"vllm_enabled={os.environ.get('VLLM_ENABLED', '')} "
            f"require_local={int(self._require_local_vllm())} "
            f"fallback_remote={int(self._llm_fallback_remote_enabled())}"
        )

    def _llm_fallback_remote_enabled(self) -> bool:
        return self._env_flag("LLM_LOCAL_FALLBACK_REMOTE", True)

    def _vllm_base_url(self) -> str:
        base = (os.environ.get("LOCAL_VLLM_BASE_URL") or DEFAULT_VLLM_BASE_URL).strip().rstrip("/")
        if not base:
            base = DEFAULT_VLLM_BASE_URL
        return base

    def _vllm_health_url(self) -> str:
        base = self._vllm_base_url()
        if base.endswith("/v1"):
            return base + "/models"
        return base + "/v1/models"

    def _vllm_host_port(self) -> Tuple[str, int]:
        host_raw = (os.environ.get("LOCAL_VLLM_HOST") or "").strip()
        port_raw = (os.environ.get("LOCAL_VLLM_PORT") or "").strip()
        if host_raw and port_raw:
            return host_raw, self._env_int("LOCAL_VLLM_PORT", 8002)

        parsed = urlparse(self._vllm_base_url())
        host = host_raw or (parsed.hostname or "127.0.0.1")
        port = self._env_int("LOCAL_VLLM_PORT", int(parsed.port or 8002))
        return host, port

    def _vllm_model(self) -> str:
        raw = (os.environ.get("LOCAL_VLLM_MODEL") or "./hf_models/Qwen3-8B-AWQ").strip()
        p = Path(raw)
        if p.is_absolute():
            return str(p)
        return str((self.project_root / raw).resolve())

    def _vllm_served_model_name(self) -> str:
        raw = (os.environ.get("LOCAL_VLLM_SERVED_MODEL") or "").strip()
        if raw:
            return raw
        return (os.environ.get("LOCAL_VLLM_MODEL") or "./hf_models/Qwen3-8B-AWQ").strip()

    def _resolve_vllm_python(self, default_python: Path) -> Path:
        raw = (os.environ.get("LOCAL_VLLM_PYTHON") or "").strip().strip('"').strip("'")
        if not raw:
            return default_python
        p = Path(raw).expanduser()
        if p.exists():
            return p.resolve()
        return default_python

    def _vllm_start_cmd_override(self) -> List[str]:
        raw = (os.environ.get("LOCAL_VLLM_START_CMD") or "").strip()
        if not raw:
            return []
        return shlex.split(raw, posix=False)

    def _build_vllm_cmd(self, python_exe: Path) -> List[str]:
        override = self._vllm_start_cmd_override()
        if override:
            return override

        host, port = self._vllm_host_port()
        api_key = (os.environ.get("LOCAL_VLLM_API_KEY") or "dummy").strip()
        cmd: List[str] = [
            str(python_exe),
            "-m",
            "vllm",
            "serve",
            self._vllm_model(),
            "--host",
            host,
            "--port",
            str(port),
        ]
        served_model = self._vllm_served_model_name()
        if served_model:
            cmd += ["--served-model-name", served_model]
        if api_key:
            cmd += ["--api-key", api_key]

        max_model_len = self._env_int("VLLM_MAX_MODEL_LEN", 4096)
        if max_model_len > 0:
            cmd += ["--max-model-len", str(max_model_len)]

        dtype = (os.environ.get("VLLM_DTYPE") or "").strip()
        if dtype:
            cmd += ["--dtype", dtype]

        gpu_mem = (os.environ.get("VLLM_GPU_MEMORY_UTILIZATION") or "").strip()
        if gpu_mem:
            cmd += ["--gpu-memory-utilization", gpu_mem]

        extra = (os.environ.get("LOCAL_VLLM_EXTRA_ARGS") or "").strip()
        if extra:
            cmd += shlex.split(extra, posix=False)
        return cmd

    def _vllm_headers(self) -> Dict[str, str]:
        key = (os.environ.get("LOCAL_VLLM_API_KEY") or "").strip()
        if not key:
            return {}
        return {"Authorization": f"Bearer {key}"}

    def _check_vllm_ok(self) -> bool:
        return self._check_http_ok(self._vllm_health_url(), timeout=1.5, headers=self._vllm_headers())

    def _vllm_start_timeout(self) -> int:
        return self._env_int("VLLM_START_TIMEOUT", 180)

    def _python_can_launch_vllm(self, python_exe: Path) -> bool:
        try:
            proc = subprocess.run(
                [str(python_exe), "-c", "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('vllm') else 1)"],
                cwd=str(self.project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            return proc.returncode == 0
        except Exception:
            return False

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
        if name == "vllm" and ("application startup complete" in lower or "uvicorn running on" in lower):
            self._ready_flags["vllm"] = True

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

    def _check_http_ok(self, url: str, timeout: float = 1.2, headers: Optional[Dict[str, str]] = None) -> bool:
        try:
            resp = self._session.get(url, timeout=timeout, headers=headers)
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
        for name in ("django", "worker", "celery", "vllm"):
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
        if not self._should_start_vllm():
            return django_ok and worker_ok
        vllm_ok = self._check_vllm_ok()
        if vllm_ok:
            return django_ok and worker_ok
        if self._require_local_vllm():
            return False
        if self._llm_fallback_remote_enabled():
            return django_ok and worker_ok
        return False

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
            self._want_vllm = self._should_start_vllm()
            self._reset_runtime_state()
            self._emit(self._route_debug_line())

            if self.is_healthy():
                self.last_start_message = "Backend is already healthy."
                self._emit(self.last_start_message)
                return True, self.last_start_message

            redis_ok, redis_msg = self._check_local_redis()
            if not redis_ok:
                self.last_error = redis_msg
                return False, redis_msg

            self._stop_locked()

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
                if self._want_vllm and not self._check_vllm_ok():
                    vllm_python = self._resolve_vllm_python(python_exe)
                    vllm_cmd = self._build_vllm_cmd(vllm_python)
                    if self._vllm_start_cmd_override():
                        self._spawn("vllm", vllm_cmd, env)
                    elif self._python_can_launch_vllm(vllm_python):
                        self._spawn("vllm", vllm_cmd, env)
                    elif self._require_local_vllm():
                        self.last_error = (
                            "Local vLLM is required but cannot be started: package 'vllm' is not available "
                            "in selected runtime. Set LOCAL_VLLM_PYTHON to a python with vllm installed, "
                            "or set LOCAL_VLLM_START_CMD to a valid startup command."
                        )
                        return False, self.last_error
                    elif self._llm_fallback_remote_enabled():
                        self._ready_flags["vllm"] = True
                        self._emit("[vllm] package not installed in selected python, skip local launch and allow remote fallback.")
                    else:
                        self.last_error = "vLLM is enabled but package 'vllm' is not installed in selected runtime."
                        return False, self.last_error
                elif self._want_vllm:
                    self._ready_flags["vllm"] = True
                    self._emit("[vllm] detected running service, reuse existing endpoint.")
                self._spawn("worker", worker_cmd, env)
                self._spawn("celery", celery_cmd, env)
                self._spawn("django", django_cmd, env)
            except Exception as exc:
                self._stop_locked()
                self.last_error = f"Failed to spawn backend process: {exc}"
                return False, self.last_error

            start_ts = time.time()
            vllm_timeout = self._vllm_start_timeout() if self._want_vllm else timeout_s
            deadline = start_ts + max(15, timeout_s, vllm_timeout)
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
                if self._want_vllm and not self._ready_flags["vllm"]:
                    self._ready_flags["vllm"] = self._check_vllm_ok()

                if all(self._ready_flags.values()):
                    if self._want_vllm:
                        self.last_start_message = "Local backend startup completed (with vLLM)."
                    else:
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
        self._ready_flags = {
            "django": False,
            "worker": False,
            "celery": False,
            "vllm": (not self._want_vllm),
        }

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
