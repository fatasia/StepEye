# 微调管线:从样本到自有检测器(训练腿操作手册)

> 目标:把「拍照即检攒的样本 + 影子模式真值」变成**本地自有的专用检测器**(烟火/PPE/手机等),
> 走 ONNX/MediaPipe .tflite 接口进 `DetectorCfg`——训练在云端 GPU,推理回本地。

## 一、三条路径与前置条件

| 路径 | 何时用 | 前置 |
|---|---|---|
| ① 免训练规则(Pose/区域/计数) | 跌倒/无人值守/聚集/翻越(人员类) | ✅ 已落地,零数据 |
| ② 开放词汇低频 | 吸烟/垃圾等低频巡检 | Grounded-SAM2/OWLv2 接入 |
| ③ 检测器微调(D-FINE/RF-DETR) | 高频+专用类别(烟火/PPE/手机) | **≥500 张已标注样本/类** |

**数据从哪来(闭环已建)**:`tools/cam_uploader.py` 落图 → 拍照即检判定 → 影子库(`--shadow`)
→ 人工真值标注(`ShadowStore.set_truth`)→ `samplegen` 增强扩充 → 训练集。

## 二、数据集规范(YOLO 格式,两个生态通吃)

```
datasets/ppe_v1/
  images/{train,val}/          # jpg
  labels/{train,val}/          # 同名 txt: class cx cy w h(归一化)
  classes.txt                  # 每行一类,顺序=class id;val 占比 ≥15%
```

- 标注工具:X-AnyLabeling(SAM2 预标注,人只确认;GPL 独立工具,不链平台代码)
- **必须含多光照子集**(昼/夜/开灯/关灯各 ≥15%,光照治理红线)
- 样本不够先用 `python -m visual gen-samples --engine augment` 扩充(增强样本占比 ≤30%)

## 三、微调与导出（接口示例，尚未完成实际训练）

先运行训练准入检查。本机 8GB 显存是否能训练所选小模型取决于分辨率、batch 和具体实现，不按显存大小直接判定；本轮未租用 GPU。
RF-DETR 使用其支持的 COCO 目录，不可直接把上节 YOLO 目录传入。选定具体型号后核查许可，不将整个 RF-DETR 家族视作同一许可。

```bash
# RF-DETR Nano 接口示例；先准备 COCO 数据与独立验证集
pip install rfdetr
python - <<'EOF'
from rfdetr import RFDETRNano
m = RFDETRNano()
m.train(dataset_dir="datasets/ppe_coco_v1", epochs=50, batch_size=4, grad_accum_steps=4)
m.export()
EOF
# D-FINE 使用其官方配置与导出契约，具体实现按所选版本核查
```

当前 ONNX 适配器仅支持 YOLO v8/v5 类布局。D-FINE/RF-DETR 必须补齐专用预处理、多输入/多输出解码并用真实导出回归，不能仅改文件名。MediaPipe 也要求兼容的 ObjectDetector 模型与元数据，不保证任意 ONNX 自动转换可用。

## 四、上岗闸门(不可跳过)

1. 固定独立检测器评测集跑框级召回/mAP及场景漏检/误报；现有 `visual regress` 主要用于 VLM 图片判定，不代替检测器评测;
2. 影子模式 ≥2 周:`serve --shadow` → `python -m visual shadow-report` —— 漏检/误杀双指标达标;
3. 许可台账登记(数据集 + 基座模型,ADR-03)。
