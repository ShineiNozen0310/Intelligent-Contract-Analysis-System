# Worker 服务（contract_review_worker）

`contract_review_worker` 是审查执行引擎，负责 OCR、盖章检测、LLM 审查与回调。

## 流程

1. 接收 `POST /analyze`
2. 投递 Celery 任务
3. 执行 OCR（fast/auto/accurate）
4. 执行盖章检测
5. 执行 LLM（本地优先，失败回退远程）
6. 回调 Django：`/contract/api/job/update/`

## 关键文件

- `api/main.py`
- `api/llm_provider.py`
- `api/llm_client.py`
- `tasks.py`
- `celery_app.py`
- `app_config.py`

## 常用配置

- `REVIEW_MODE`
- `LLM_PROVIDER`
- `LLM_LOCAL_FALLBACK_REMOTE`
- `LOCAL_VLLM_BASE_URL`
- `QWEN_TIMEOUT`
- `DJANGO_CALLBACK_URL`

## 启动

```bat
.\start_all.bat start
```
