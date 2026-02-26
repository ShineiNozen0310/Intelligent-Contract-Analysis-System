# Worker 服务说明

`contract_review_worker` 负责合同审查核心流程：OCR、盖章检测、LLM 结构化输出。

## 1. 主要模块

- `api/main.py`：FastAPI 入口和主流程
- `api/llm_client.py`：Qwen API 调用
- `tasks.py`：Celery 任务入口
- `celery_app.py`：Celery 配置

## 2. 流程简述

1. Django 调用 `/analyze`
2. Worker 提交 Celery 任务
3. 执行 OCR 和盖章检测
4. 调用 LLM 生成 `result_json`
5. 回调 Django 更新任务状态

## 3. 关键配置（`.env`）

- `REVIEW_MODE`
- `OCR_DPI`、`OCR_MAX_PAGES`
- `QWEN_TIMEOUT`、`QWEN_INPUT_MAX_CHARS`
- `STAMP_ENABLED`、`STAMP_MAX_PAGES`
- `CELERY_BROKER_URL`、`CELERY_RESULT_BACKEND`

## 4. 性能优化要点

- 减少子进程日志 I/O 开销
- OCR 临时文件自动清理
- 大文档盖章检测支持抽样 / 跳过
- LLM 输入长度裁剪与超时控制
