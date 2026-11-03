# 焊接、焊点与针脚专用模型

本批取得一个能实际输出焊缝框的 YOLO ONNX，以及一个焊料不足 ROI 分类候选。未切换生产模型，资产不入 git；机器可读登记见 `sop-vision/models/public/joint-candidates.json`。

## 焊缝检测：已真实推理

发布者：[avinashhm/welding-defect-yolov8-full-training](https://huggingface.co/avinashhm/welding-defect-yolov8-full-training/tree/2cbc6dc08b74b6bab1ebafc59a3804e4bc43e517)。固定 revision `2cbc6dc08b74b6bab1ebafc59a3804e4bc43e517`，原文件 `weights/best.onnx`。

- 实际文件 `.v0/joint-models/avinashhm-best.onnx`，44,734,882 bytes；SHA256 `dbddf8b39a4425b53aad2de279e0fb18b2189f97afdf9ad1c5a939cf59b29745`，与发布端 LFS 一致。
- 输入 `[1,3,640,640]`，输出 `[1,7,8400]`；类别依次 **Bad Weld、Good Weld、Defect**，取自实际 ONNX metadata。
- 现有 `YoloOnnxBackend` 的 `yolo_raw` 已兼容，无需添加模型代码或加载 PT。
- 作者同版本 `val_batch0_labels.jpg` 固定图在 CPU 上，conf=0.25 返回15框，三个类别均有输出。冷加载+检测550.56ms，只记录这一张图，不换算实时FPS。

图 SHA256：`36834bd1c523b856b1aa0eba432676783b60317a6f28fb4a4b21f091f4437f25`。输入是带作者标签的验证拼图，不是独立测试图；本次只能证明推理/解码可运行，不能从15框推断准确率或合格率。原始框、类别和分数在 `.v0/joint-models/weld-real-trial.json`。

**许可分别记录**：Ultralytics代码为 AGPL-3.0；实际权重 metadata 同样写 AGPL-3.0，模型卡未另写权重许可。卡片仅称数据为 The Welding Defect Dataset - v2，没有明确数据来源/许可。保持评估候选，未批准生产发布。可见光焊缝类别不扩展为焊点微缺陷或 X-ray 内部缺陷。

## 焊料不足：已下载，需 ROI 分类适配

发布者：[JcProg/PCBInspect-LeadDefect](https://huggingface.co/JcProg/PCBInspect-LeadDefect/tree/fe29664de102e5e2ecce3bdc52a7f014bd0e9929)，固定 revision `fe29664de102e5e2ecce3bdc52a7f014bd0e9929`。

`model.onnx` 已保存 `.v0/joint-models/JcProg-model.onnx`，21,793,705 bytes，SHA256 `851a12519c713e676907a5f6e65248a09b033c1c7b4f01c1c87472d6f2bd2f58`，匹配 LFS。ORT 已实际加载，输入float32 `[1,3,640,640]`，输出 `[1,2]`；Golden / SolderInsufficient。这是已定位元件 ROI 的二分类，不能返回缺陷框，也不是完整连接器针脚模型。未用无关图片冒充准确率测试。

模型卡声明 MIT，但实际 ONNX metadata 写 **AGPL-3.0**，两处冲突；训练数据是未公开的私有 AOI 元件裁片。不能据模型卡的 MIT 单独宣称商用无约束。详情 `.v0/joint-models/lead-static.json`。后续若适配，须明确 ROI 裁片入口及输出激活函数，不能塞进 YOLO 框解码器。

## 未采用的路径与下一步

- [ParsaVictor 焊点 YOLO26](https://huggingface.co/ParsaVictor/pcb-solder-defect-detection-yolo26)：固定 `3b7ec12d89b69d6346a1134c02a161374022eaee`，只有44MB PT，声明 research-use-mixed-sources。未下载执行 pickle。关联[数据仓库](https://huggingface.co/datasets/ParsaVictor/pcb-solder-defect-detection/tree/219ff5062b63bd7de745a1669a3d336243695440)确有 data.yaml / images_and_labels.zip / LICENSE.md；本次许可文件 TLS 获取失败，未下载整包或启动训练。
- [PO-YOLOv5连接器论文](https://doi.org/10.1371/journal.pone.0297059) Data Availability 明确代码和自建数据归第三方、需申请；未取得公开权重。论文开放许可不等于数据许可，组件 Pins 检测也不能改名为倾斜针脚检测。

优先把已取得的焊缝模型放入**评估试图**入口，使用新的无标注覆盖层原始焊缝照片建立独立样本；类别输出不直接生成生产 PASS。针脚缺陷还缺可核授权权重或带框原始数据。焊点训练的可执行下一步是先完整取得并核对上述 dataset 的 LICENSE/ZIP，检查按原板/元件来源拆分和框质量，再复用现有隔离 train-venv 做小规模 YOLO训练/独立验证/ONNX导出；当前不能报告已完成训练或数据许可已通过。

## 实际环境

未安装主环境依赖、未加载 torch/pickle、未运行第三方脚本、未训练。ONNX 推理使用现有 CPU Runtime；下载与推理进程均已结束。作者模型卡及 metadata、下载SHA、原始推理结果保存在 `.v0/joint-models/`。
