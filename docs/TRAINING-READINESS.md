# T25 训练准备检查（2026-11-03）

训练前用本机脚本检查数据结构、框标注、重复泄漏和许可台账；检查通过只表示可以进入训练，不表示模型可发布。

## 本轮资源盘点

- 项目内排除虚拟环境、依赖、构建目录后，没有发现 `classes.txt`、`dataset.yaml` 或 `data.yaml`。已定位的 zcode 项目记忆也没有已就绪的 PPE／烟火标注数据目录，记录仍为待数据。
- `sop-vision/data/` 中的 6 张参考图和 `.v0/evalset/` 中的 18 张小评测图不是满足 T25 的框标注训练集；本轮未把演示图充当训练成果。
- 本机实查为 RTX 4060 Laptop、8188MiB 显存。本轮未训练、未下载大数据集、未租 GPU；数据、实际训练与现场影子验收仍未完成。
- YOLO 和现有权重保持不变。`helmet.onnx` 的既有实测结论保留；烟火社区权重的既有不合格结论保留。

## 最短检查路径

在 `sop-vision/` 下运行：

```powershell
python tools/training_preflight.py datasets/ppe_v1 --out data/ppe-preflight.json
```

目录要求：`classes.txt`、`images/train/`、`images/val/`、`labels/train/`、`labels/val/`、`metadata.json`。标注路径与图片相对路径对应。

- 默认每类至少 500 张**内容不同**的已标注图片、每类验证集占比至少 15%。计数同时输出框数和去重图片数。
- 缺标、非法类别／框坐标、损坏图片、训练／验证 SHA256 重复、缺少许可审核字段会返回 `blocked`，退出码为 2。
- 空标注只可作为经审核的负样本，需在 `metadata.json` 的 `reviewed_negatives` 中列出相对标签路径；否则阻止进入训练。
- `--min-images-per-class`、`--min-val-ratio` 可调整检查策略，实际阈值写入报告，不能拿降低阈值后的结果冒充默认门禁。

`metadata.json` 最小例子（内容需由实际审核人填写）：

```json
{
  "licenses": [
    {"component": "dataset", "license": "待核实", "source": "授权材料位置", "checked_by": "审核人", "checked_at": "2026-11-03", "commercial_use": false},
    {"component": "base_model", "license": "待核实", "source": "官方模型来源", "checked_by": "审核人", "checked_at": "2026-11-03", "commercial_use": false}
  ],
  "reviewed_negatives": []
}
```

这是阻塞模板，不是已获得授权的声明。脚本只确认台账完整并记录了商用审核结论，不自动判断许可是否有效；报告不回显许可来源 URL、查询凭据或原始文件内容。

## 当前导出接入的真实缺口

`visual/onnx_detector.py` 当前固定使用单输入 640 像素 letterbox、RGB/255，并按 YOLO v8/v5 风格解码首个输出。它不是任意 ONNX 模型的通用适配器。

| 模型路径 | 官方导出契约 | StepEye 尚需完成 |
|---|---|---|
| D-FINE | `images`、`orig_target_sizes` 两输入；`labels`、`boxes`、`scores` 三输出 | 多输入／多输出适配、类别映射、原图坐标与真实导出回归。[官方导出代码](https://github.com/Peterande/D-FINE/blob/master/tools/deployment/export_onnx.py) |
| RF-DETR | `dets` 为归一化框，`labels` 为原始 logits；按模型元数据归一化与映射背景类别 | 按输出名解码、预处理、类别映射、真实导出回归。[官方 ONNX 文档](https://github.com/roboflow/rf-detr/blob/develop/docs/exports/onnx.md) |

不能仅把 `.onnx` 文件放入模型目录就声称完成替换。RF-DETR 也不能笼统写成全型号 Apache：当前官方文档区分 Nano–Large 检测型号与 Plus／PML 路径，选定具体模型后重新登记许可。[官方型号与许可说明](https://github.com/roboflow/rf-detr/blob/develop/docs/index.md)

## 发布前仍需什么

数据通过结构检查 → 人工检查标注与光照／场景覆盖 → 选定具体基座并审核许可 → 实际训练 → 核对 ONNX 输出与平台推理一致性 → 独立检测器回归 → 真实影子验证。

现有 `visual regress` 主要是 VLM 图片判定回归，不等于检测器框级 mAP／召回率回归。T25 仍为 **blocked：缺合格框标注数据、真实训练结果与上线验收**。
