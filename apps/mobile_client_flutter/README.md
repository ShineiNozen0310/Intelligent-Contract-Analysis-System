# Flutter 客户端

`apps/mobile_client_flutter` 是智能合同审查系统的 Flutter 前端。

## 功能

- 上传 PDF
- 发起审查
- 轮询状态
- 查看结构化报告与原始 JSON
- 导出 PDF 报告

## 默认 API 地址

- 桌面：`http://127.0.0.1:8003/contract`
- Android 模拟器：`http://10.0.2.2:8003/contract`

## 启动

```bat
.\start_all.bat start
cd apps\mobile_client_flutter
..\..\tools\flutter\bin\flutter.bat pub get
..\..\tools\flutter\bin\flutter.bat run
```

一键脚本：

```bat
.\launch_flutter_oneclick.bat
.\launch_flutter_release_oneclick.bat
```
