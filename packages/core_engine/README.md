# Core Engine（迁移目标）

该包用于沉淀可复用的核心处理能力：

- OCR 与解析编排
- 规则检查
- LLM 审查编排
- 报告对象组装

## 当前状态

- 主要运行逻辑仍在 `contract_review_worker/api/main.py`
- 共享报告协议已抽离到 `packages/shared_contract_schema`
- `result_contract.py` 负责错误结果构建与盖章状态归一化
