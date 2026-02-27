# 智能合同审查系统

这是一个 Flutter 优先的本地合同审查系统，流程为：上传 PDF -> OCR + LLM 审查 -> 结构化结果 -> 导出 PDF。

## 架构

```text
Flutter 客户端（apps/mobile_client_flutter）
  -> 本地统一 API（apps/local_api，8003）
      -> Django API（contract_review，8000）
      -> Worker API（contract_review_worker，8001）
          -> Celery + Redis
          -> OCR / LLM
```

## 快速启动（PowerShell）

1. 安装依赖
```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. 启动后端（会同时拉起 8000/8001/8002/8003）
```bat
.\start_all.bat start
.\start_all.bat status
```

3. 启动前端
```bat
.\launch_flutter_oneclick.bat
```

Release 一键启动：
```bat
.\launch_flutter_release_oneclick.bat
```

4. 停止后端
```bat
.\stop_all.bat
```

## 目录

```text
apps/
  local_api/
  mobile_client_flutter/
contract_review/
contract_review_worker/
packages/
  core_engine/
  shared_contract_schema/
```

## Local API 端点

- `GET /contract/api/health/`
- `POST /contract/api/start/`
- `GET /contract/api/status/{job_id}/`
- `GET /contract/api/result/{job_id}/`
- `GET /contract/api/export_pdf/{job_id}/`

## 说明

- 优先使用仓库内 `tools/flutter`。
- PowerShell 执行本地脚本要带前缀：`./` 或 `.\`。
