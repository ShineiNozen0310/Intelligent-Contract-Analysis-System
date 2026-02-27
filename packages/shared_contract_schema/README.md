# 共享结果协议（shared_contract_schema）

`packages/shared_contract_schema` 定义多端共用的报告协议。

## 对外函数

- `normalize_result_json(raw)`
- `build_report_payload(result_json, result_markdown)`
- `build_report_html(report_payload)`
- `build_report_markdown(report_payload)`

## 目标

1. 统一 API 与 Flutter 的报告结构
2. 避免前端重复拼装字段
3. 保持渲染结果一致
