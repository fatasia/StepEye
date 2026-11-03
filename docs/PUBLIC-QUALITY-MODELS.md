# 质量检测公开资产

2026-11-03。本次找到并下载了极片涂层数据和 PCB ONNX 候选；均未接入生产、未训练。此前资产不重复下载，基线见[既有模型审计](MODEL-ASSET-AUDIT.md)。

| 方向 | 实际取得 | 当前判断 |
|---|---|---|
| 锂电／极片涂层 | CoatingVision v1，94.3MB 完整 ZIP | 最值得继续的数据；检测标注仅针孔／未分类两类，不是四类检测器 |
| 汽车电子／PCB | 社区 PCB YOLO26s，38.2MB ONNX | 文件合格；训练数据许可未明，且当前解码器不支持其端到端输出，隔离保留 |
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

**下一步可实际做的事**：将有框的 581 图按原视频／物理卷分组导入两类项目，核实标注和许可归属，保留独立批次，再执行[训练预检](TRAINING-READINESS.md)。未分类类仅 78 张，达不到现有每类 500 张默认门槛；当前也没有独立现场验证集。裂纹／分层需另外审核多通道分割标注后决定做分割还是人工框，不能直接改类名复用针孔检测标签。

## 2. PCB ONNX：文件可核验，不能直接上岗

来源为训练发布者 [betty0/pcb-defect-detection](https://huggingface.co/betty0/pcb-defect-detection/tree/08f7ff096054f6ad3e1f37dfdf5cfc63d04dabaa)，固定 revision `08f7ff096054f6ad3e1f37dfdf5cfc63d04dabaa`。模型卡关联作者训练代码，并明确 HRIPCB 镜像的数据许可为 Unknown；权重声明 AGPL-3.0，不列为无条件可商用资产。

实际文件 `.v0/quality-models/pcb-yolo26-best.onnx`：38,175,508 字节，SHA256 `057c61dbd2745f59bd246effebf47a31686e95b0e2263610b0600664e5210fc5`，与 HF LFS OID 相符。

静态 `onnx.checker` 通过，无 external-data 引用，无自定义算子域：float32 输入 `[1,3,640,640]`，输出 `[1,300,6]`，顺序为 `[x1,y1,x2,y2,confidence,class_id]`，opset 20。类别表与文件元数据一致：missing_hole、mouse_bite、open_circuit、short、spur、spurious_copper。

当前 `visual/onnx_detector.py` 明确不支持端到端 NMS 输出，不能按 v8 原始输出解码。此模型只完成下载、哈希和静态结构校验，未执行推理或独立评测；没有安装或加载 PT/pickle。作者报告的 board-grouped 成绩不是本项目实测，也不代替目标汽车电子产线数据。

## 3. 不应误列为可用的其他候选

- **DeepPCB**：固定 [作者提交](https://github.com/tangsanli5201/DeepPCB/tree/08e98c4db5922613fb97176eb3d6497d48260cb1)。README 末尾明确数据仅限研究；MIT LICENSE 与该限制不能混为一谈。实际下载 640×640 测试／模板图和 10 个框，六类 ID 1–6 全部合法；完整 trainval/test 清单是 1000/500，但共享 7 个源组。即使取得另行授权，也需按源板重拆分，并将像素 xyxy+1基类别转换为 YOLO，而非直接上传文本。
- **GC10-DET**：固定 [作者提交](https://github.com/lvxiaoming2019/GC10-DET-Metallic-Surface-Defect-Datasets/tree/c89df4f2bf0417f842461407a11e9371ad6d035c)。只有 README 与示意图，提供百度网盘入口，未附数据 LICENSE 或 ONNX。第三方镜像标注的 CC BY 不能替代作者授权；本轮未下载完整数据。
- **Merge-YOLO 包装**：[论文的数据链接](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0340205)指向[作者仓库](https://github.com/luoyirong123/Merge-YOLO/tree/2799011d06c3ba56b57a005af119e0f1f23a3b2c)。实际树只有 13 个文件，“Defective Dataset”是 1 字节 blob，无图／标签／权重／LICENSE；论文开放获取不等于数据已经发布。
- **SecureCoating-Vision 社区极片模型**：固定作者提交 `c557b956760577d9d709304f3effe7002927838d`；[模型配置](https://github.com/thtcsec/securecoating-vision/blob/c557b956760577d9d709304f3effe7002927838d/configs/model.yaml)声明两类裂纹／分层及 ONNX 指纹，但本次递归树未包含 PT/ONNX 文件，未核实可下载发布包。记录为权重未取得，不据 README 的“ships”字样登记已安装模型；[NOTICE](https://github.com/thtcsec/securecoating-vision/blob/c557b956760577d9d709304f3effe7002927838d/NOTICE.md)列代码／权重 AGPL-3.0、数据 CC BY 4.0。

## 本轮结论

可继续准备的是 **CoatingVision 两类极片检测数据**；PCB ONNX 仅留作输出适配和评测候选。没有新增已合格的包装、表面或汽车电子生产模型，没有训练、替换 YOLO 或修改现有模型配置。
