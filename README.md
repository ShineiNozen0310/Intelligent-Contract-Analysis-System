# 智能合同审查系统

> 面向法务、采购、合同管理等业务场景的桌面审查产品。上传合同 PDF 后，系统自动完成 OCR、要素提取、风险识别与建议生成，并支持报告导出。

## 当前目标

- 桌面端：稳定可交付、可一键启动、可回收后台进程。
- 移动端：保留 Flutter 跨平台能力，支持 Android/iOS 后续落地。

## 核心能力

- 合同 PDF 上传与任务调度。
- OCR 文本抽取与预处理。
- 盖章识别（可配置开关）。
- LLM 审查（本地 vLLM 优先，远端回退）。
- 产品化结果页（审查摘要、风险建议、审查时间）。
- 审查结果导出 PDF。

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

## 启动脚本矩阵（已整理）

核心脚本已按职责分层到：

- `scripts/dev`：开发调试启动（Flutter 调试、一键联调）
- `scripts/release`：发布与快捷方式（Release 构建、桌面入口）
- `scripts/ops`：后端运维与清理（start/stop/status、网关、清理）

根目录同名脚本保留为兼容入口，历史命令可继续使用：

- `launch_flutter_release_oneclick.bat`：桌面版一键启动（推荐给业务用户）。
- `launch_flutter_oneclick.bat`：开发一键启动（后端 + Flutter 调试）。
- `run_flutter_client.bat [device] [extra args]`：手动启动 Flutter。

示例：

```bat
run_flutter_client.bat windows
run_flutter_client.bat android --debug
run_flutter_client.bat edge --web-port 8090
```

## 快速启动（业务用户）

### 方式 A：桌面快捷方式（推荐）

双击 `合同审查桌面版.lnk`。

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

### 3) 启动 Flutter（桌面或移动）

```bat
.\run_flutter_client.bat windows
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

## 结构清理与维护

新增一键清理脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\clean_workspace.ps1
```

会清理：

- Flutter 构建残留（`build`、`.dart_tool`、`ephemeral`）
- Python 缓存（`__pycache__`、`*.pyc`、`*.pyo`）
- `parsers/mineru/mineru.egg-info` 生成残留

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

确认 `8003` 服务可用。

### 本地 vLLM 连接失败（127.0.0.1:8002）

若 vLLM 在 WSL NAT 模式，Windows 可能无法直连 `127.0.0.1`。可改为 WSL IP：

```text
LOCAL_VLLM_BASE_URL=http://<WSL_IP>:8002/v1
```

### 上传提示文件类型错误

请确认文件是有效 PDF，而非仅改扩展名。

## 目录结构

```text
apps/
  local_api/                 # 前端统一 API
  mobile_client_flutter/     # Flutter 客户端（桌面 + 移动）
contract_review/             # Django 业务接口
contract_review_worker/      # 审查执行服务
packages/                    # 共享模块
parsers/                     # 解析器与模型相关依赖
scripts/
  dev/                       # 开发调试脚本
  release/                   # 发布相关脚本
  ops/                       # 运维与清理脚本
```

## 发布前检查

- `flutter analyze`
- `python manage.py check`
- `build_flutter_release.bat`
- 真实 PDF 的端到端审查与导出验证
