# Desktop App（`desktop_app`）

`desktop_app` 是桌面端入口层，负责：

- 启动和管理本地后端（Django / Worker / Celery / 可选 vLLM）
- 展示任务状态和审查报告
- 导出 PDF、复制报告和 JSON

## 1. 关键文件

- `app_pyside6.py`：主界面（当前默认 UI，PySide6/Qt）
- `backend_runtime.py`：后台服务生命周期管理
- `run_app.bat`：源码模式启动入口
- `build_exe.bat`：打包 EXE
- `dist/ContractReviewDesktop.exe`：打包产物

兼容入口：

- `app.py`：旧 Tk 版本，仅在 PySide6 启动失败时作为回退

## 2. 启动方式

### 2.1 推荐（从项目根目录）

```bat
launch_desktop_oneclick.bat
```

### 2.2 在子目录直接调试

```bat
desktop_app\run_app.bat
```

### 2.3 强制使用已打包 EXE

```bat
set USE_PACKAGED_EXE=1
launch_desktop_oneclick.bat
```

## 3. 后台服务管理逻辑

`backend_runtime.py` 会按健康检查决定“复用”还是“拉起”：

- Django: `http://127.0.0.1:8000/contract/api/health/`
- Worker: `http://127.0.0.1:8001/healthz`
- vLLM: `http://127.0.0.1:8002/v1/models`（启用本地模型时）

如果服务已健康，桌面端会直接复用，不重复启动。

## 4. 本地 vLLM 相关（桌面端侧）

桌面端会根据 `.env` 决定是否启动 vLLM，常用键：

- `LLM_PROVIDER` / `LLM_PRIMARY_PROVIDER`
- `VLLM_ENABLED`
- `LLM_REQUIRE_LOCAL_VLLM`
- `LLM_LOCAL_FALLBACK_REMOTE`
- `LOCAL_VLLM_BASE_URL`
- `LOCAL_VLLM_MODEL`
- `LOCAL_VLLM_API_KEY`
- `LOCAL_VLLM_PYTHON`
- `LOCAL_VLLM_START_CMD`

说明：

- `LOCAL_VLLM_PYTHON`：指定一个已安装 `vllm` 的 Python 解释器
- `LOCAL_VLLM_START_CMD`：直接给定完整启动命令（优先级更高）

## 5. 打包

在项目根目录执行：

```bat
desktop_app\build_exe.bat
```

输出文件：

```text
desktop_app\dist\ContractReviewDesktop.exe
```

## 6. 常见问题

### 6.1 `Failed to spawn backend process: [WinError 2]`

通常是路径或依赖问题：

- 项目根目录路径变化后，快捷方式仍指向旧目录
- `.venv` 丢失或依赖不完整
- 脚本被杀软拦截

建议先在项目根目录手动执行：

```bat
desktop_app\run_app.bat
```

### 6.2 Worker 端口冲突（`bind 127.0.0.1:8001`）

先清理旧进程再启动：

```bat
stop_all.bat
start_all.bat start
```

避免“桌面端 + 手动脚本”并行重复启动。

### 6.3 本地模型连接失败（8002）

确认：

- `LOCAL_VLLM_BASE_URL` 是否为 `http://127.0.0.1:8002/v1`
- vLLM 是否已启动并监听 8002
- 使用的 Python 是否安装 `vllm`

## 7. 日志

桌面端日志默认在：

```text
C:\Users\<你的用户名>\AppData\Local\ContractReviewDesktop\logs\desktop.log
```
