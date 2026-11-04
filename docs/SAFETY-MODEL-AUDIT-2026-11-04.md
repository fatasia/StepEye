# 安全专用模型追加审计（2026-11-04）

本轮取得近景腕带数据并完成一次训练与ONNX评测；模型未达可用水平。本文记录数据交付时的状态。

## 腕带：141 张真实图片，3 类标注

来源：[Justin Lutz / Jetson-recomputer-PPE](https://studio.edgeimpulse.com/public/129799/latest)，公开项目 129799，版本 2，快照项目 141976。项目声明 Apache-2.0；这是数据项目声明，不改变下游训练框架或生成权重的许可要求。

原项目 196 张（训练 158、测试 38）。本轮匿名下载成功 141 张：原训练 110、原测试 31，共 278 个框。3 个训练分页与 19 个图片请求发生 TLS EOF；分页定向重试一次仍失败后停止，未将缺失图片计入数据。类别完全沿用作者 `ring / watch / wrist-strap`，没有从缺框推导未佩戴类别。

| 拆分 | 图片 | ring 正样本图 | watch 正样本图 | wrist-strap 正样本图 |
|---|---:|---:|---:|---:|
| train | 87 | 61 | 33 | 74 |
| val | 23 | 14 | 23 | 15 |
| test | 31 | 20 | 18 | 20 |

- 原作者 test 保留为 test，不进入训练或选模。它是同拍摄会话留出集，不是独立工厂/人员/机位验证。
- 验证取原训练中完整的前两组采集分钟：`2022-08-15T20:42`、`2022-08-15T20:50`。其余原训练组进入 train，不随机切相邻帧。
- 每张下载图已解码，核验尺寸、框边界、文件 SHA256 和像素 SHA256。跨原始拆分精确像素重复为 0。
- 64 位 dHash（9×8 灰度、Lanczos，汉明距离 ≤4）检出 49 对 train/test 相似候选；train/val、val/test 均为 0。相似候选并非精确重复，但再次说明作者测试不能当作跨场景泛化证据。
- 内容是工作台近景手部及可见腕带。不能外推到接地、电阻、防静电服、远景 ESD 合规全覆盖。

数据入口：`.v0/safety-model-audit/edge-wrist/dataset-v1/data.yaml`。逐图来源与 SHA 在同目录 `source.json`；下载审计在上级 `audit.json`，相似图审计在 `near-duplicate-audit.json`。下载脚本 `.v0/safety-model-audit/download_edge_wrist.py` 使用公开页面及匿名原图接口，不需要用户凭据。

可复用转换器：`sop-vision/tools/prepare_wrist_dataset.py`。它校验原始图 SHA、按采集组划分并排除训练图与留出集的精确像素重复，保留原测试。3 项测试覆盖分组/测试隔离、源文件变更拒绝、训练重复排除。

```powershell
python sop-vision/tools/prepare_wrist_dataset.py --audit .v0/safety-model-audit/edge-wrist/audit.json --rows .v0/safety-model-audit/edge-wrist/rows.json --output .v0/safety-model-audit/edge-wrist/dataset-v2
```

## 滴漏：排除错误标卡的权重

[Thalos 发布仓库](https://huggingface.co/thalostech2025/thalos-oil-gas-safety-v1/tree/32648810b170a7401d6330732822818713e8dd5b)的模型卡声称含 leak；实际文件 `oil_gas_weights.pt` 的序列化 names 只有：

`no-suit, no_glove, no_goggles, no_helmet, no_mask, no_shoes`

因此不登记为泄漏模型。只下载前 1 MiB，以 `pickletools` 静态解析 ZIP 内 `best/data.pkl`，校验条目 CRC/长度；没有执行 pickle 或 torch.load，没有进行推理。完整文件 87,688,762 字节未下载，远端 LFS SHA `276df90baf7c4f19934cdc2288bea804a17f0650ef2ebac8b6501c76f4df1d23` 未经本地全文件复核。证据 `.v0/safety-model-audit/thalos-audit.json`。

[leak-detection-thesis](https://github.com/Kether180/leak-detection-thesis)公开的是方法/代码，作者明确未发布数据与权重，不能补成本地模型。积水模型也不替代滴漏时序模型。

## 本轮剩余缺口

| 项目 | 检索与实际结果 | 尚缺 |
|---|---|---|
| 可见腕带 | 已交付 141 张、3 类可训练子集 | 完成训练、导出和真实图评测；当前新增权重数 0 |
| 完整 ESD | 未获取完整同域本地权重；不重复请求需要 key 的 Roboflow 导出 | 服装/接地等独立能力与数据 |
| 洁净穿戴/行为 | [SH17](https://github.com/ahmadmughees/SH17dataset)数据为 CC BY-NC-SA 4.0，不能作为无商业限制的数据补齐；原 Roboflow 导出仍需 key | 可直接取得且许可适合的洁净服/帽/鞋套数据及行为序列 |
| 滴漏 | Thalos 实际类别不符；上述论文仓库未发布数据/权重 | 真实滴漏公开标注序列或可验证的本地权重 |
| 远景睡岗 | [驾驶困倦 ONNX](https://huggingface.co/notgoodkeeper/cnn-based-drowsiness-detection)依赖近景人脸预处理，属于不同域；未下载、未推理 | 厂区远景睡岗时序标注及专用动作头 |

以上是本次限定检索的结果，不代表互联网上不存在其他资源。没有启动常驻服务或占用 GPU，没有改动共享模型清单和服务接口。

## 训练后的一次固定评测

`tools/evaluate_wrist_onnx.py` 复用平台 `create_backend` 和 `evaluate_boxes`，按 `source.json` 中的作者 test 清单读取全部图片并对照原始作者框，核对 SHA 和 ONNX 类别顺序。固定输入 640、置信度 0.25、匹配 IoU 0.5，不搜索阈值、不改变拆分。输出逐图预测、TP/FP/FN、各类精确率/召回率和 CPU 时延；不将截断置信度后的预测宣称为完整 AP。

```powershell
python sop-vision/tools/evaluate_wrist_onnx.py --model PATH_TO_EXPORTED_ONNX --dataset .v0/safety-model-audit/edge-wrist/dataset-v1 --audit .v0/safety-model-audit/edge-wrist/audit.json --output .v0/safety-model-audit/edge-wrist/test-evaluation.json
```

评测脚本已实际执行全部31张作者test图；数据目录保持原拆分。

## 实际训练结果

复用已有YOLOv8n基座与平台隔离training_worker，CPU固定640、batch4、20轮，耗时363.37秒。87训练/23验证，31作者test不参与训练；仅一次固定工作点评测，无阈值扫描。

ONNX SHA256 `24795596ca01216245ecbb94fc60151252999683b215b1a4af0a5a2fc8c45f44`，三类ring/watch/wrist-strap。test TP12/FP1/FN46，P0.9231/R0.2069；腕带本身TP8/FP1/FN12、R0.4，戒指R0。当前**未达可用水平，不登记自动加载、不启用告警**。候选与失败指标保留models/public/safety-training-candidates.json；本机权重wrist-visible-v1.onnx不入Git。数据声明Apache-2.0不改变YOLOv8基座/框架AGPL许可。

平台复现用转换器生成的dataset.yaml（仅train/val，path为本目录）；data.yaml额外保留作者test供离线评测。训练规格/结果/test逐图证据在.v0/safety-model-audit/edge-wrist/。

分组依据是公开项目记录的created分钟，不是已核验的相机采集批次。不同创建时间不保证不同拍摄会话，因此val也不作为独立工厂泛化证据。
