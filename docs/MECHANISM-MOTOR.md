# 电机故障分类研究候选

机理实验室新增「电机分类」，复用 weihu 已训练的 MCC5-THU RF ONNX。输入是振动与三相电流的固定 114 维工程特征，输出完整记录的 24 类候选，不是任意振动波形通用诊断。

- 复用原训练结果，没有重新训练或占用 GPU。
- CPU 按请求加载、计算、释放；同实例与机理报告库均单并发，忙返回 429，无后台线程。
- 源记录没有有效窗口时返回 `unsupported`，不补健康标签。
- 报告、历史、导出复用现有机理实验室；模型 SHA/清单 SHA 固定，并遵循可选全局模型签名策略。

## 使用

原版特征缓存为每条完整记录一个 NPZ，包含 `features[N,114]`；先转换，再在「机理实验室 → 新建分析 → 电机分类」上传：

```powershell
python sop-vision/tools/motor_feature_request.py record-1.npz record-2.npz --output motor-request.json
```

单次最多 8 条记录、每条最多 64 个有效窗口；JSON ≤2MB。不得把任意 114 个统计量重命名为原特征。打包器只核对结构，原始传感器和特征来源仍需调用方确认。

原特征提取实现原样保留于 `models/mechanism/motor-rf/motor_features.py`，原文件 SHA `b46eb63529518484679440967045e66a176ad77ba59bdba6ad6801e75a9ab406`，MIT 许可副本同目录。它需要 NumPy/SciPy，仅供离线提取，平台推理不导入它。原实现为 12.8kHz、2秒窗口、9列原电压数据（时间、键相、扭矩、三轴振动、三相电流）；键相每转一脉冲与传感器0.1V/g、0.1V/A是原数据假设，不能套到其他传感器。

接口：`GET /api/mechanism/motor/capabilities`；`POST /api/mechanism/motor/analyses`。报告 kind 为 `motor_rf_shadow`。公开记录回放请求位于 `.v0/motor-rf/real-request.json`，不随源码分发。

## 模型与证据

来源：weihu 运行 `20261031T205846Z-a699e055/held-out-torque-profile-42`。模型20,440,546字节，SHA `54e8ef689293f3c1cb17c3cf578765e9757e622e9b071a79864fb763ebf2cee4`。本机 `models/mechanism/motor-rf/model.onnx` 已完整复制；权重不进Git。

类别覆盖滚动体/内外圈缺陷、弯曲、断条、偏心、绕组、不平衡及组合类，顺序以 manifest 为准。图内包含特征列选择与RF，不包含原波形预处理；对同一完整记录的有效窗口概率取均值后分类，不将单窗直接用作告警。

原始报告的留出扭矩工况为144条记录、macro-F1 0.95099，正常记录仅6条；这是原历史结果，本轮未重跑整套。**3000rpm 跨转速迁移未通过，未获工业准入，也不估计 RUL。**

本轮读取已核 SHA 的原留出记录228（health）及144（bearing_ball_H），各45个有效窗口；CPU实际输出两类分别正确，与原独立推理输出概率最大绝对差0。90窗含会话加载一次耗时1326ms，不是延迟分位数或新增准确率结论。记录见 `.v0/motor-rf/real-result.json`。

数据来自 [Mendeley MCC5-THU v1](https://data.mendeley.com/datasets/6s3dggj9mw/1)，发布方声明CC BY4.0；作者Shijin Chen、Zeyi Liu、Chenyang Li、Dongliang Zou、Xiao He、Donghua Zhou。代码MIT、原数据和模型用途分开登记，模型保持 `evaluation_only`。

10项专项检查覆盖完整记录均值、空窗、维度/有限值/特征版本、重复记录、缺资产、单并发与失败释放、共享报告API；JS语法通过。页面集成由主线浏览器验收。
