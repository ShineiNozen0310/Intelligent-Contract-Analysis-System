# 智能合同审查系统

> 面向法务、采购、合同管理等业务场景的桌面审查产品。上传合同 PDF 后，系统自动完成 OCR、要素提取、风险识别与建议生成，并支持报告导出。

## 产品定位

智能合同审查系统提供“可本地部署、可离线运行、可对接本地模型”的合同审查能力，目标是让业务人员用更短时间完成更稳定的一致化审查。

- 降低人工通读成本，快速定位风险条款。
- 以结构化结果呈现审查结论，提升可读性与可执行性。
- 支持本地模型优先策略，保障数据可控。

## 核心能力

- 合同 PDF 上传与任务调度。
- OCR 文本抽取与预处理。
- 盖章识别（可配置开关）。
- LLM 审查（本地 vLLM 优先，远端回退）。
- 产品化结果页（审查摘要、风险建议、审查时间）。
- 审查结果导出 PDF。

## 使用流程（业务视角）

1. 打开桌面应用。
2. 选择合同 PDF。
3. 点击“开始审查”。
4. 系统自动执行：盖章识别 -> OCR -> LLM 分析。
5. 查看审查报告并按需导出 PDF。

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

- `8000`：Django API（任务与状态）
- `8001`：Worker API（审查执行）
- `8002`：本地 vLLM（可选）
- `8003`：Local API（前端统一入口）

## 快速启动

### 方式 A：桌面快捷方式（推荐）

双击桌面 `合同审查桌面版.lnk`。

该入口会自动完成：

- 后端服务拉起（隐藏窗口）。
- Flutter Release 客户端启动。
- 客户端关闭后自动回收后端进程。

### 方式 B：命令行一键启动

```bat
.\launch_flutter_release_oneclick.bat
```

## 开发者启动

### 1) 安装依赖

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

## 打包发布（Windows）

### 构建 Release

```bat
.\build_flutter_release.bat
```

生成文件：

```text
apps/mobile_client_flutter/build/windows/x64/runner/Release/contract_review_flutter.exe
```

### 创建/刷新桌面快捷方式

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\create_release_shortcut.ps1
```

## Local API（前端对接）

- `GET /contract/api/health/`
- `POST /contract/api/start/`
- `GET /contract/api/status/{job_id}/`
- `GET /contract/api/result/{job_id}/`
- `GET /contract/api/export_pdf/{job_id}/`

## 关键配置（.env）

### LLM 路由

- `LLM_PRIMARY_PROVIDER`
- `LLM_FALLBACK_PROVIDER`
- `LLM_REQUIRE_LOCAL_VLLM`

### 本地 vLLM

- `LOCAL_VLLM_BASE_URL`
- `LOCAL_VLLM_START_CMD`
- `LOCAL_VLLM_MODEL`

### OCR / 盖章

- `OCR_BACKEND`
- `OCR_DPI`
- `STAMP_ENABLED`

## 常见问题

### 前端健康检查失败

```bat
.\start_all.bat status
```

确认 `8003` 端口服务可用。

### 本地 vLLM 连接失败（127.0.0.1:8002）

若 vLLM 运行在 WSL NAT 模式，可将 `.env` 中地址改为 WSL IP：

```text
LOCAL_VLLM_BASE_URL=http://<WSL_IP>:8002/v1
```

### 上传提示文件类型错误

请确认文件是有效 PDF，而非仅修改扩展名。

### PowerShell 运行 bat 失败

需带前缀 `./` 或 `\.\`，例如：

```powershell
.\start_all.bat start
```

## 目录结构

```text
apps/
  local_api/                 # 前端统一 API
  mobile_client_flutter/     # Flutter 客户端
contract_review/             # Django 业务接口
contract_review_worker/      # 审查执行服务
packages/                    # 共享模块
parsers/                     # 解析器与相关依赖
```

## 发布前检查

- `flutter analyze`
- `python manage.py check`
- `./build_flutter_release.bat`
- 真实 PDF 的端到端审查与导出验证
