# 合同智能审查系统（Desktop）

这是一个本地运行的合同审查桌面应用。  
你只需要打开桌面端，系统会在后台自动启动 Django / Worker / Celery，完成 OCR、审查和报告导出。

## 一句话说明

- 产品入口：桌面端（PySide6）
- 后端地址：`127.0.0.1:8000`（Django）+ `127.0.0.1:8001`（Worker）
- 本地模型（可选）：`127.0.0.1:8002`（vLLM）

## 1. 快速启动（推荐）

### 1.1 安装依赖

```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

如果你是 GPU 环境，可额外安装：

```bat
pip install -r requirements-gpu.txt
```

### 1.2 启动桌面端

```bat
launch_desktop_oneclick.bat
```

默认是源码模式启动（改完代码马上生效）。

如果你要强制使用打包好的 EXE：

```bat
set USE_PACKAGED_EXE=1
launch_desktop_oneclick.bat
```

## 2. 模型路由配置（.env）

配置文件是项目根目录 `.env`。

### 2.1 本地 vLLM 优先，失败回退远程（推荐）

```env
LLM_PRIMARY_PROVIDER=local_vllm
LLM_FALLBACK_PROVIDER=qwen_plus
LLM_LOCAL_FALLBACK_REMOTE=1
LLM_REQUIRE_LOCAL_VLLM=0

LOCAL_VLLM_BASE_URL=http://127.0.0.1:8002/v1
LOCAL_VLLM_API_KEY=dummy
LOCAL_VLLM_MODEL=./hf_models/Qwen3-8B-AWQ
LOCAL_VLLM_SERVED_MODEL=./hf_models/Qwen3-8B-AWQ

# 小上下文模型建议
LOCAL_VLLM_MAX_TOKENS=48
LOCAL_VLLM_INPUT_CHAR_LIMIT=320
LOCAL_VLLM_PROMPT_TEXT_MAX_CHARS=240
LOCAL_VLLM_OCR_FIX_MAX_CHARS=280
LOCAL_VLLM_CONTEXT_WINDOW=256
LOCAL_VLLM_CONTEXT_SAFETY_MARGIN=16
```

### 2.2 只用本地 vLLM（严格）

```env
LLM_PRIMARY_PROVIDER=local_vllm
LLM_REQUIRE_LOCAL_VLLM=1
LLM_LOCAL_FALLBACK_REMOTE=0
```

### 2.3 只用远程

```env
LLM_PROVIDER=remote
LLM_PRIMARY_PROVIDER=remote
```

## 3. 服务脚本

```bat
start_all.bat start
start_all.bat stop
start_all.bat restart
start_all.bat status
```

不建议同时用两种入口重复启动（例如：桌面端 + `start_all.bat start` 并行）。

## 4. 本地 vLLM 启动说明

项目已支持自动拉起 vLLM（默认 `8002`）。触发条件之一：

- `LLM_PROVIDER=local_vllm`
- `LLM_PRIMARY_PROVIDER=local_vllm`
- `VLLM_ENABLED=1`

默认等价命令：

```bat
python -m vllm serve "./hf_models/Qwen3-8B-AWQ" --host 127.0.0.1 --port 8002 --served-model-name "./hf_models/Qwen3-8B-AWQ" --api-key dummy --quantization awq_marlin --dtype half --gpu-memory-utilization 0.86 --max-model-len 256 --max-num-seqs 1 --enforce-eager
```

如果 vLLM 不在项目 `.venv`，可在 `.env` 指定：

```env
LOCAL_VLLM_PYTHON=你的python路径
```

或者直接指定完整命令：

```env
LOCAL_VLLM_START_CMD=你的vllm启动命令
```

## 5. 常见问题

### 5.1 `attempting to bind ... 8001`

端口冲突（重复启动）导致。执行：

```bat
stop_all.bat
start_all.bat start
```

### 5.2 `local vllm health probe failed` / `127.0.0.1:8002 refused`

说明本地 vLLM 没有真正启动，检查：

- `LOCAL_VLLM_BASE_URL` 是否是 `http://127.0.0.1:8002/v1`
- `LOCAL_VLLM_PYTHON` 对应 Python 是否安装了 `vllm`
- `start_all.bat status` 中 vLLM 是否 running

### 5.3 `You passed xxx input tokens ...`

说明超过了本地模型上下文窗口。继续减小输入：

- `LOCAL_VLLM_INPUT_CHAR_LIMIT`
- `LOCAL_VLLM_PROMPT_TEXT_MAX_CHARS`
- `LOCAL_VLLM_MAX_TOKENS`（建议 32~64）

## 6. 打包 EXE

```bat
desktop_app\build_exe.bat
```

输出文件：

```text
desktop_app/dist/ContractReviewDesktop.exe
```

## 7. 日志位置

桌面端日志：

```text
C:\Users\<你的用户名>\AppData\Local\ContractReviewDesktop\logs\desktop.log
```

## 8. 项目结构（核心）

```text
DjangoProject1/                Django 配置
contract_review/               Django 业务接口
contract_review_worker/        Worker（OCR / LLM / 回调）
desktop_app/                   桌面端与打包
hf_models/                     本地模型和缓存
start_all.bat                  服务管理脚本
launch_desktop_oneclick.bat    一键启动入口
```

## 9. 子模块文档

- `desktop_app/README.md`
- `contract_review/README.md`
- `contract_review_worker/README.md`
- `DjangoProject1/README.md`
