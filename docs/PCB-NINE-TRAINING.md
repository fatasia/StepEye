# PCB 九类训练数据

固定来源为 DsPCBSD+ v1，CC BY 4.0；复用完整下载包，不重复下载。来源与整包审计见 [公开数据](PUBLIC-MODEL-NEXT.md)。

工具 `sop-vision/tools/prepare_pcb_dataset.py` 从 COCO 真值生成平台已有训练 worker 可读的数据。类别顺序固定：SH、SP、SC、OP、MB、HB、CS、CFO、BMFO，不猜缩写的工艺定义。

## 冻结版本

本机 `.v0/dspcbsd/dataset-v1/dataset.yaml`，版本 `DSPCBSD-V1-SHA10`：

| 集合 | 图片 | 来源 |
|---|---:|---|
| train | 7,370 | 原作者 train |
| val | 838 | 原作者 train 图像 SHA256 前32位模10为0 |
| test | 2,051 | 原作者 val 全量保留，不进入训练 YAML |

所有集合均覆盖九类。原始文件名如 Y_011791.jpg、S_11068415.jpg，没有已证实的板号或会话字段；date_captured不能支持可靠批次隔离。因此拆分不是同板隔离，近重复/同板裁剪仍可能造成乐观指标。test是作者留出数据，不是工厂验收数据。

原审计270框指超过0.01px的轻微越界；转换记录所有幅度越界，共371框，最大越界不超过0.02px。仅裁到图像边缘，不丢框，原始框和归一化修正值逐条保存在source.json；超出这个容差直接拒绝。

生成目录保存原图、YOLO标签、来源文件SHA、原作者集合、最终集合、九类框数和修正记录。原始ZIP不改。dataset.yaml只有train/val；metadata.json记录数据版本及来源许可。未声明这些公开作者标签为现场人工复核。

```powershell
python sop-vision/tools/prepare_pcb_dataset.py --archive .v0/dspcbsd/DsPCBSD.zip --output .v0/dspcbsd/dataset-v1
```

输出已存在时拒绝覆盖。数据已冻结，不修改正在训练的数据版本。

## GPU 训练与后续评测

2026-11-04 16:32 已启动现有 `visual/training_worker.py`：YOLOv8n、30轮、batch8、固定640、device0。RTX4060 Laptop 8GB，启动前空闲约6GB；第2轮训练占用约2.4GB，每轮约154秒。权重仍在训练，暂无九类ONNX与留出评测结果。

- 规格：`.v0/dspcbsd/train-spec-v1.json`；输出：`.v0/dspcbsd/train-gpu-v1/`。
- 数据指纹：`47eb32b60fab01a543d26214b4dc1d0b5e8b716b73e1bfa2bbb090a8a66fc9e5`。
- 基座SHA：`f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`。
- 训练日志：`.v0/dspcbsd/train-gpu-v1.log`，完成时 worker 写 `result.json` 并导出固定640 ONNX。

已启动一次性串行评测进程：等待本次训练退出，只有成功生成候选结果才运行 `tools/evaluate_pcb_onnx.py`。不启动第二个训练，不自动发布；结束即退出。日志 `.v0/dspcbsd/evaluate-after-training.log`，报告 `.v0/dspcbsd/test-eval-v1.json`。若电脑或进程中断，可在训练完成后执行：

```powershell
python sop-vision/tools/evaluate_pcb_onnx.py --model .v0/dspcbsd/train-gpu-v1/train/weights/best.onnx --archive .v0/dspcbsd/DsPCBSD.zip --output .v0/dspcbsd/test-eval-v1.json
```

评测使用全部2,051张原作者val，置信度0.25、IoU0.5；记录每类TP/FP/FN、精确率/召回率、逐图预测和CPU延迟，不将截断预测计算为完整AP。结果文件存在时拒绝覆盖。取得实际指标后再决定候选登记，原PCB六类模型保留。

数据CC BY 4.0不改变YOLOv8基座/训练框架的AGPL许可条件；本候选不标成Apache模型。
