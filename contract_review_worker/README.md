# Worker Service (`contract_review_worker`)

`contract_review_worker` 是合同审查的执行引擎，负责 OCR、盖章检测、LLM 审查与回调。

## 功能边界

- 接收 Django 提交的任务（`/analyze`）。
- 在 Celery Worker 中执行实际分析流程。
- 产出 `result_markdown` 与 `result_json` 并回调 Django。

## 核心流程

1. Django 调用 Worker API `POST /analyze`。
2. Worker 将任务投递到 Celery：`contract_review_worker.analyze_job`。
3. 执行 OCR（fast / auto / accurate）。
4. 执行盖章检测（stamp2vec/ultralytics + 红章 fallback）。
5. 执行 LLM：
   - 本地优先（vLLM / Qwen3）
   - 失败自动回退远程（qwen-plus）
6. 回调 Django：`/contract/api/job/update/`。

## 关键文件

- `api/main.py`：主编排与 FastAPI 接口。
- `api/llm_provider.py`：LLM 路由层（local-first + fallback）。
- `api/llm_client.py`：远程 Qwen 调用与审查后处理。
- `tasks.py`：Celery 任务入口。
- `celery_app.py`：Celery 配置。
- `app_config.py`：`.env` 加载与统一配置注入。

## LLM 路由策略

- `LLM_PROVIDER=local_vllm`：优先本地 vLLM。
- `LLM_LOCAL_FALLBACK_REMOTE=1`：本地失败自动回退远程。
- Worker 会将实际通道写入结果元数据：`result_json._llm_meta`。

示例：

```json
{
  "provider": "local_vllm",
  "fallback_used": false
}
```

或

```json
{
  "provider": "remote",
  "fallback_used": true,
  "fallback_from": "local_vllm"
}
```

## 常用配置（`.env`）

- `REVIEW_MODE=fast|accurate|auto`
- `QWEN_TIMEOUT`
- `QWEN_INPUT_MAX_CHARS`
- `LLM_PROVIDER`
- `LLM_PRIMARY_PROVIDER`
- `LLM_FALLBACK_PROVIDER`
- `LLM_LOCAL_FALLBACK_REMOTE`
- `LLM_REQUIRE_LOCAL_VLLM`
- `LOCAL_VLLM_BASE_URL`
- `LOCAL_VLLM_MODEL`
- `LOCAL_VLLM_SERVED_MODEL`
- `LOCAL_VLLM_EXTRA_ARGS`
- `LOCAL_VLLM_MAX_TOKENS`
- `LOCAL_VLLM_INPUT_CHAR_LIMIT`
- `DJANGO_CALLBACK_URL`

## 运行与检查

启动全套服务（推荐）：

```bat
start_all.bat start
```

仅检查 Django 配置：

```bat
.\.venv\Scripts\python.exe manage.py check
```

## 故障排查

- `llm timeout`: 调高 `QWEN_TIMEOUT`，并降低本地 `LOCAL_VLLM_MAX_TOKENS`。
- 本地模型显存不足：降低 `VLLM_MAX_MODEL_LEN` 与输入上限。
- 回调失败：检查 `DJANGO_CALLBACK_URL` 与 `WORKER_TOKEN`。
- 任务不消费：检查 Redis、Celery 是否已启动。
