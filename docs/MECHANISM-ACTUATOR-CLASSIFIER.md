# 执行器四类诊断

复用 weihu 已训练的三种子 ONNX 集成模型，识别正常、间隙、缺润滑、剥落。它与原有执行器健康残差分析分开，不需要健康参考记录，不输出 RUL。

## 资产与输入

- 源：`weihu/outputs/actuator-ensemble-study/runs/20261031T063124Z-32d1eb3b/bundle/record-ensemble.onnx`。
- SHA256：`6aa820a3b980a55f09dc6f2ba6c912c198ebabceba59f8ada9a16c32c0eeb89d`，574,705 字节。
- 输入：完整 80 秒记录，25 Hz，`[2000,3]`；列严格为指令位置 mm、跟随误差 mm、电流 A，不做客户端归一化。图内已包含训练归一化、五周期聚合、温度校准和候选集计算。
- 工况：20/40/neg40 三种负载 × sin/trap 两种轨迹。请求最多 8 条，不截断、不重采样。
- 代码源项目 MIT；数据 [Cranfield actuator](https://doi.org/10.17862/cranfield.rd.5097649) 为 CC BY 4.0，来源和历史协议保存在模型目录 `manifest.json`。许可记录来自源项目的固定实验清单。

原模型在同一已知语料的 156 条留出记录上 macro-F1 为 0.96736；12 条正常记录有 1 条误报。这是源项目历史结果，不是现场准确率。新设备、跨工况和寿命预测未通过验证。

## 接入

服务 `ActuatorClassifierService(root)` 按请求创建 CPU ONNX session，最多两个 CPU 线程，完成后释放。没有 GPU、轮询或常驻模型。遵循现有模型签名策略。

独立 router `actuator_classifier_router(store, service)` 使用现有不可变机理报告库：

- `GET /api/mechanism/actuator-classifier/capabilities`
- `POST /api/mechanism/actuator-classifier/analyses`
- 列表、详情、JSON 导出继续使用 `/api/mechanism/analyses`，报告种类 `actuator_classifier_shadow`。

候选集为空或有多个类别时 `label=null`、状态 `review`；同时保留最高概率类别和完整概率。不把低确定性输出变成正常。

## 安装与回放

运行目录需包含仓库中的 manifest，再从上述固定源复制模型：

```powershell
python sop-vision/tools/install_actuator_classifier.py --source D:/Documents/ChatGPT/weihu/outputs/actuator-ensemble-study/runs/20261031T063124Z-32d1eb3b/bundle/record-ensemble.onnx
python sop-vision/tools/replay_actuator_classifier.py --source-run D:/Documents/ChatGPT/weihu/outputs/actuator-ensemble-study/runs/20261031T063124Z-32d1eb3b --output .v0/mechanism-actuator-classifier/replay.json
```

本轮：8 项契约测试通过；按类别各取第一条 repeat=9 的 4 条既有留出记录，真实 CPU 推理并经报告创建、详情、列表、导出走通。概率相对源保存输出最大差 `1.1920928955078125e-7`，4 条总耗时 35.7 ms（含 session 初始化，本机单次）。证据 `.v0/mechanism-actuator-classifier/replay.json`。本轮没有训练、调阈值或重跑准确率评测。

完整应用验收：真实 `build_app` + 隔离 SQLite + Chromium 登录，经机理实验室入口导入上述四条信号、实际推理、重开报告与下载 JSON 通过；viewer 写入返回 403、读取报告 200，页面错误 0。证据 `.v0/output/playwright/actuator-classifier/`；`process-exit.json` 记录服务线程已退出。此验收不替换生产数据或现有账号。
