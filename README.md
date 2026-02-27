# 合同智能审查系统

一个面向业务落地的合同审查产品：  
**上传 PDF -> 自动识别 -> 风险审查 -> 改进建议 -> 一键导出报告**。

---

## 产品定位

- 用户只需要打开桌面应用
- 系统自动拉起本地后端服务
- 支持本地模型优先，远程模型兜底
- 审查结果可视化展示，并支持复制/导出

---

## 核心价值

### 1. 快
- 桌面端一键启动，分钟级可用
- 支持任务状态实时更新

### 2. 稳
- 后端服务健康检查与自动复用
- 本地模型异常时可自动回退远程模型

### 3. 可落地
- 输出结构化风险点和可执行改进建议
- 支持 PDF 审查报告导出，便于留痕和流转

---

## 使用流程

1. 打开桌面端  
2. 拖拽或选择合同 PDF  
3. 点击“开始审查”  
4. 查看合同关键信息、审查概述、风险点、改进建议  
5. 导出 PDF 或复制报告内容

---

## 三分钟上手

### 1. 安装依赖

```bat
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

GPU 环境可选：

```bat
pip install -r requirements-gpu.txt
```

### 2. 启动产品

```bat
launch_desktop_oneclick.bat
```

### 3. 停止服务

```bat
stop_all.bat
```

---

## 模型策略（本地优先 + 兜底）

配置文件：项目根目录 `.env`

推荐配置（本地 vLLM 优先，失败回退 Qwen Plus）：

```env
LLM_PRIMARY_PROVIDER=local_vllm
LLM_FALLBACK_PROVIDER=qwen_plus
LLM_LOCAL_FALLBACK_REMOTE=1
LLM_REQUIRE_LOCAL_VLLM=0

LOCAL_VLLM_BASE_URL=http://127.0.0.1:8002/v1
LOCAL_VLLM_API_KEY=dummy
LOCAL_VLLM_MODEL=./hf_models/Qwen3-8B-AWQ
LOCAL_VLLM_SERVED_MODEL=./hf_models/Qwen3-8B-AWQ

# 小上下文窗口建议
LOCAL_VLLM_MAX_TOKENS=48
LOCAL_VLLM_INPUT_CHAR_LIMIT=320
LOCAL_VLLM_PROMPT_TEXT_MAX_CHARS=240
LOCAL_VLLM_OCR_FIX_MAX_CHARS=280
LOCAL_VLLM_CONTEXT_WINDOW=256
LOCAL_VLLM_CONTEXT_SAFETY_MARGIN=16
```

---

## 架构概览

```text
Desktop App (PySide6)
  -> Django API      http://127.0.0.1:8000
  -> Worker API      http://127.0.0.1:8001
  -> Celery + Redis  异步任务
  -> vLLM            http://127.0.0.1:8002/v1 (可选)
```

说明：所有服务默认在本机 `127.0.0.1` 通信，不对公网开放。

---

## 常用运维命令

```bat
start_all.bat start
start_all.bat stop
start_all.bat restart
start_all.bat status
desktop_app\build_exe.bat
```

---

## 常见问题

### 1) `attempting to bind ... 8001`
原因：重复启动导致端口冲突。  
处理：

```bat
stop_all.bat
start_all.bat start
```

### 2) `local vllm health probe failed` / `127.0.0.1:8002 refused`
原因：本地 vLLM 未成功启动。请检查：

- `LOCAL_VLLM_BASE_URL` 是否为 `http://127.0.0.1:8002/v1`
- 当前 Python 环境是否安装 `vllm`
- `start_all.bat status` 中 vLLM 是否 running

### 3) `You passed xxx input tokens ...`
原因：超过本地模型上下文窗口。可继续调小：

- `LOCAL_VLLM_INPUT_CHAR_LIMIT`
- `LOCAL_VLLM_PROMPT_TEXT_MAX_CHARS`
- `LOCAL_VLLM_MAX_TOKENS`

---

## 日志位置

```text
C:\Users\<你的用户名>\AppData\Local\ContractReviewDesktop\logs\desktop.log
```

---

## 项目结构（核心）

```text
DjangoProject1/                Django 配置层
contract_review/               业务 API 层
contract_review_worker/        OCR/LLM Worker 层
desktop_app/                   桌面端与打包
hf_models/                     本地模型与缓存
launch_desktop_oneclick.bat    产品一键启动入口
start_all.bat                  服务管理脚本
```

---

## 子模块文档

- [desktop_app/README.md](desktop_app/README.md)
- [contract_review/README.md](contract_review/README.md)
- [contract_review_worker/README.md](contract_review_worker/README.md)
- [DjangoProject1/README.md](DjangoProject1/README.md)
