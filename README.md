# 智能合同审查系统（Flutter 桌面版）

一个面向业务用户的本地合同审查产品：上传 PDF 后，系统自动执行 OCR、要素提取、风险识别与建议生成，并可导出审查报告 PDF。

## 产品定位

- 面向对象：法务、采购、合同管理、项目管理等业务角色。
- 核心价值：
  - 降低人工通读成本，快速定位风险点。
  - 提供结构化结论（合同类型、关键要素、风险、建议）。
  - 本地部署，可控、可离线、可与本地模型结合。

## 主要能力

- 合同 PDF 上传与任务调度。
- 合同文本抽取（OCR + 纠错流程）。
- 盖章检测（可配置）。
- LLM 审查（支持本地 vLLM 与远程回退路由）。
- 结构化报告展示（风险/建议双栏、可读化摘要）。
- 审查结果导出为 PDF。

## 端到端流程

1. 选择合同 PDF。
2. 点击“开始审查”。
3. 系统依次执行：盖章识别 -> OCR -> LLM 审查。
4. 前端展示审查进度、审查用时、结构化报告。
5. 导出 PDF 报告用于归档与流转。

## 系统架构

```text
Flutter 客户端（apps/mobile_client_flutter）
  -> 本地统一 API（apps/local_api，8003）
      -> Django API（contract_review，8000）
      -> Worker API（contract_review_worker，8001）
          -> Celery + Redis
          -> OCR / Stamp / LLM
```

### 默认端口

- `8000`：Django（合同任务管理）
- `8001`：Worker API（审查执行）
- `8002`：本地 vLLM（可选）
- `8003`：Local API（前端统一入口）

## 快速使用（业务用户）

### 方式 A：桌面快捷方式（推荐）

双击桌面 `合同审查桌面版.lnk`。

该入口会：
- 自动拉起后端依赖（隐藏窗口方式）。
- 启动 Flutter release 客户端。
- 前端关闭后自动回收后端进程。

### 方式 B：命令行启动

```bat
.\launch_flutter_release_oneclick.bat
```

## 开发环境启动（开发者）

### 1) 准备 Python 环境

```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) 启动后端

```bat
.\start_all.bat start
.\start_all.bat status
```

### 3) 启动 Flutter 调试端

```bat
.\run_flutter_client.bat
```

### 4) 停止后端

```bat
.\stop_all.bat
```

## 打包与桌面发布

### 构建 Windows Release

```bat
.\build_flutter_release.bat
```

生成物：

```text
apps/mobile_client_flutter/build/windows/x64/runner/Release/contract_review_flutter.exe
```

### 生成/更新桌面快捷方式

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_release_shortcut.ps1
```

## Local API（前端访问）

- `GET /contract/api/health/`
- `POST /contract/api/start/`
- `GET /contract/api/status/{job_id}/`
- `GET /contract/api/result/{job_id}/`
- `GET /contract/api/export_pdf/{job_id}/`

## 关键配置（.env）

常用项：

- LLM 路由
  - `LLM_PRIMARY_PROVIDER`（如 `local_vllm`）
  - `LLM_FALLBACK_PROVIDER`（如 `qwen_plus`）
  - `LLM_REQUIRE_LOCAL_VLLM`
- 本地 vLLM
  - `LOCAL_VLLM_BASE_URL`
  - `LOCAL_VLLM_START_CMD`
  - `LOCAL_VLLM_MODEL`
- OCR / Stamp
  - `OCR_BACKEND`、`OCR_DPI`、`STAMP_ENABLED` 等

## 常见问题

### 1) 前端“健康检查失败”

先确认后端已启动：

```bat
.\start_all.bat status
```

重点检查 `8003`（Local API）是否正常。

### 2) 本地 vLLM 连接失败（127.0.0.1:8002 拒绝）

若 vLLM 跑在 WSL，且当前是 NAT 网络模式，Windows 可能无法通过 `127.0.0.1` 访问 WSL 端口。可将 `.env` 中 `LOCAL_VLLM_BASE_URL` 改为 WSL IP：

```text
LOCAL_VLLM_BASE_URL=http://<WSL_IP>:8002/v1
```

### 3) 上传后提示文件类型错误

请确保上传的是真实 PDF（不是改后缀的非 PDF 文件）。

### 4) PowerShell 无法直接执行 bat

在 PowerShell 中执行本地脚本需带前缀：

```powershell
.\start_all.bat start
```

## 目录说明

```text
apps/
  local_api/                 # 前端统一 API 入口
  mobile_client_flutter/     # Flutter 客户端
contract_review/             # Django 业务接口与任务状态
contract_review_worker/      # 审查执行服务（OCR/LLM/Stamp）
packages/                    # 共享模块
parsers/                     # 解析与相关依赖
```

## 版本与发布建议

- 建议每次发布前执行：
  1. `flutter analyze`
  2. `python manage.py check`
  3. `build_flutter_release.bat`
- 发布时推荐只交付：
  - 版本说明
  - release exe 及快捷方式入口
  - 必要的模型与运行依赖说明

---
如需，我可以继续补一份 `README-部署手册.md`（按“单机部署 / 内网部署 / GPU部署”拆分）。
