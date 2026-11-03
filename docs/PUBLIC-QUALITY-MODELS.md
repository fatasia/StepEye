# 质量检测公开资产

2026-11-03。已下载极片数据和 PCB ONNX 候选，并完成受限的单针孔真实训练；均未接入生产。此前资产不重复下载，基线见[既有模型审计](MODEL-ASSET-AUDIT.md)。

| 方向 | 实际取得 | 当前判断 |
|---|---|---|
| 锂电／极片涂层 | CoatingVision v1，94.3MB 完整 ZIP | R1→R7两类缺验证类别；已完成单针孔3轮候选，不是完整两类／四类模型 |
| 汽车电子／PCB | 社区 PCB YOLO26s，38.2MB ONNX | 独立端到端适配与CPU固定图实推已接；数据许可未明，仅供评估 |
| PCB 数据 | 作者 DeepPCB 一对图及框标注 | 数据明确仅限研究；不能拿仓库 MIT 当数据商用授权 |
| 金属表面 | 作者 GC10-DET 仓库及下载入口 | 未找到作者数据许可证；暂不选作默认交付 |
| 包装 | 作者 Merge-YOLO 仓库 | “Defective Dataset”实际是 1 字节文件，未提供可训练数据或权重 |

完整固定版本、来源和校验结果在 `sop-vision/models/public/quality-candidates.json`。下载与原始核验报告保留于 `.v0/quality-models/`，不自动注册为视觉应用。

## 1. CoatingVision：已下载的极片候选

[作者数据 v1](https://doi.org/10.6084/m9.figshare.29260121.v1)由 Vignesh Sampath 发布，Figshare 数据许可证为 **CC BY 4.0**；[论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC13594281/)说明检测 ID 0 为针孔、ID 1 为未分类缺陷。作者[代码仓库](https://github.com/vigsam-coder/CoatingVision/tree/19d857658207491c5711625017c0db9a14233965)未附 LICENSE，本轮不复制或运行其训练代码；数据许可证与代码许可分别记录。

实际下载文件：`.v0/quality-models/CoatingVision-v1.zip`，94,296,797 字节。

```text
SHA256 04dd29cac2124ce08410225fbec93ef2e1a91af790cf24a769e29cf316e76655
MD5    7cbbf14725f9762a64a81a12b858a2b4
```

MD5 与作者 Figshare file 55182476 发布值一致，ZIP 全量 CRC 通过，无越界路径。直接读取压缩包得到：

- 检测目录 2,227 张 JPEG，全部 640×480、解码验证通过，精确 SHA256 重复组 0。
- 只有 **581 张**有检测标签，共 649 框：`0/pinhole` 564 框、`1/unclassified` 85 框，归一化边界及有限数校验全部通过；另 1,646 张缺标，不能自动补空或认作背景。
- 分类 CSV 含 2,227 行，四类阳性图片数分别为 surface_crack 1,947、delamination 203、pinhole 519、unclassified 78；这是多标签分类，不是四类框标注。
- 没有预置 train/val/test。CSV 保留原视频／切片文件名：8 个 `_frame_` 前缀，但仅 R1/R7 两种卷／批次前缀（2,095/132 张）。分组语义还需核实；随机拆相邻帧和同帧 patch 会泄漏。

`coating-validation.json` 是全包检查结果；`coatingvision-sample/` 保留一组原始检测图／标签及 CSV。作者 GitHub 的 8 对 train/val 示例另存在 `coatingvision-source/`，不是完整训练拆分，也不代替全包数据。

**实际继续结果**：转换脚本已生成按 R1/R7 隔离的 560/21 图两类数据，但 R7 未分类正样本为 0，两类训练仍 blocked。另明确限定单针孔任务，R1 498 图训练、R7 21 图验证，官方YOLOv8n三轮真实 train/val/export完成；mAP50 0.47407、mAP50–95 0.25225，模型仍是实验候选。完整环境、指纹、命令和CPU核验见[训练记录](YOLO-TRAINING.md#2026-11-03实际训练记录)。未分类仅78图仍不满足默认500图/类门槛，裂纹／分层不能改名复用针孔框。

## 2. PCB ONNX：文件可核验，不能直接上岗

来源为训练发布者 [betty0/pcb-defect-detection](https://huggingface.co/betty0/pcb-defect-detection/tree/08f7ff096054f6ad3e1f37dfdf5cfc63d04dabaa)，固定 revision `08f7ff096054f6ad3e1f37dfdf5cfc63d04dabaa`。模型卡关联作者训练代码，并明确 HRIPCB 镜像的数据许可为 Unknown；权重声明 AGPL-3.0，不列为无条件可商用资产。

实际文件 `.v0/quality-models/pcb-yolo26-best.onnx`：38,175,508 字节，SHA256 `057c61dbd2745f59bd246effebf47a31686e95b0e2263610b0600664e5210fc5`，与 HF LFS OID 相符。

静态 `onnx.checker` 通过，无 external-data 引用，无自定义算子域：float32 输入 `[1,3,640,640]`，输出 `[1,300,6]`，顺序为 `[x1,y1,x2,y2,confidence,class_id]`，opset 20。类别表与文件元数据一致：missing_hole、mouse_bite、open_circuit、short、spur、spurious_copper。

已增加 `visual/yolo_e2e.py`，经 `detector_registry.create_backend(adapter="yolo_e2e")` 显式选择，不把六列输出猜成 v8 原始布局，也不再执行 NMS。原始布局适配保持原路；遇 `end2end=True` 元数据会要求明确适配器，防止两类别原始输出与六列布局混淆。

`models/public/quality-models.json` 固定指纹、6类及许可状态；本机权重复制到 `sop-vision/models/public/pcb-yolo26.onnx` 后登记 `public-pcb-yolo26`。只登记 `evaluation_only`，不下载 PT、不替换任何生产模型、不自动切到相机。现有视觉应用试图及机器建议均使用原来的按需子进程，通过登记的 adapter 选择布局。

实际测试使用 DeepPCB 固定图 `00041000_test.jpg`，SHA256 `624b448f0737aaa8bdc53963a752800147bffb2591365b9810b3f2a83b956c4a`：完整 `ModelTrials` CPU子进程链路约948ms，conf 0.25 返回 **0框**。该二值图与模型训练来源不同，未比较真值、未计算准确率；0框不是“PCB合格”。报告在 `.v0/quality-models/pcb-e2e-real-trial.json`。作者报告的 board-grouped 成绩仍不是本项目实测，不代替真实汽车电子产线评测；训练数据许可未明的状态没有因适配完成而改变。

## 3. 不应误列为可用的其他候选

- **DeepPCB**：固定 [作者提交](https://github.com/tangsanli5201/DeepPCB/tree/08e98c4db5922613fb97176eb3d6497d48260cb1)。README 末尾明确数据仅限研究；MIT LICENSE 与该限制不能混为一谈。实际下载 640×640 测试／模板图和 10 个框，六类 ID 1–6 全部合法；完整 trainval/test 清单是 1000/500，但共享 7 个源组。即使取得另行授权，也需按源板重拆分，并将像素 xyxy+1基类别转换为 YOLO，而非直接上传文本。
- **GC10-DET**：固定 [作者提交](https://github.com/lvxiaoming2019/GC10-DET-Metallic-Surface-Defect-Datasets/tree/c89df4f2bf0417f842461407a11e9371ad6d035c)。只有 README 与示意图，提供百度网盘入口，未附数据 LICENSE 或 ONNX。第三方镜像标注的 CC BY 不能替代作者授权；本轮未下载完整数据。
- **Merge-YOLO 包装**：[论文的数据链接](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0340205)指向[作者仓库](https://github.com/luoyirong123/Merge-YOLO/tree/2799011d06c3ba56b57a005af119e0f1f23a3b2c)。实际树只有 13 个文件，“Defective Dataset”是 1 字节 blob，无图／标签／权重／LICENSE；论文开放获取不等于数据已经发布。
- **SecureCoating-Vision 社区极片模型**：固定作者提交 `c557b956760577d9d709304f3effe7002927838d`；[模型配置](https://github.com/thtcsec/securecoating-vision/blob/c557b956760577d9d709304f3effe7002927838d/configs/model.yaml)声明两类裂纹／分层及 ONNX 指纹，但本次递归树未包含 PT/ONNX 文件，未核实可下载发布包。记录为权重未取得，不据 README 的“ships”字样登记已安装模型；[NOTICE](https://github.com/thtcsec/securecoating-vision/blob/c557b956760577d9d709304f3effe7002927838d/NOTICE.md)列代码／权重 AGPL-3.0、数据 CC BY 4.0。

## 本轮结论

**CoatingVision 单针孔已产生真实训练候选，两类仍缺独立验证类别**；PCB ONNX仅留作输出适配和评测候选。没有新增生产合格模型，没有替换现有YOLO或修改生产配置。
