# 固定模型验证

对视觉应用导出的已确认标注版本运行一次检测评测，保存逐图预测、误检和漏检，供专用模型候选比较。

在 `sop-vision` 目录执行：

```powershell
python tools/evaluate_detection_holdout.py --dataset dataset.zip --dataset-sha256 <数据包SHA256> --model model.onnx --model-sha256 <模型SHA256> --adapter yolo_raw --confidence 0.25 --iou 0.5 --output report.json
```

输入使用 LabelStore 导出的 ZIP，不接受临时拼接的普通 YOLO 目录。端到端输出模型选择 `yolo_e2e`；模型 metadata 中的类别及顺序须与标注版本一致。

- 只运行 `val` 中已人工确认的图片；明确复核为空背景的图片也参与误报统计。
- 排除合成来源组和未确认数据，检查导出划分之间的来源组与图片指纹重叠。
- 固定数据包、模型、版本及图片指纹；同一会话、单一输入尺度，不重复跑分。
- 报告包含阈值、来源、框、逐图 FP/FN 和汇总 precision/recall/F1；不覆盖已有文件、不发布模型。

每次最多500张合格验证图片，超过时拒绝，不悄悄抽样。指标是固定置信度与 IoU 下的单工作点结果，不是 COCO AP。随机验证划分不能证明现场独立性；模型是否见过这些数据仍需结合训练记录确认。

软件验证使用真实 ONNX Runtime、小型协议模型和 LabelStore 人工确认版本，覆盖导出、推理、报告及 CLI。它验证评测链路，不代表任何专用模型的识别准确率。
