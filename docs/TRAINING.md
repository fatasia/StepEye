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

## 三、微调与导出(在 ≥24GB GPU 上执行;4060 8GB 只够 QLoRA 2B 级 VLM,检测器微调建议租 4090)

```bash
# RF-DETR 微调(Apache-2.0,微调 API 最简)
pip install rfdetr
python - <<'EOF'
from rfdetr import RFDETRNano
m = RFDETRNano()
m.train(dataset_dir="datasets/ppe_v1", epochs=50, batch_size=4, grad_accum_steps=4)
m.export()
EOF
# D-FINE 同理(官方仓库 config 驱动);两者任选,许可均 Apache-2.0
```

导出 ONNX 后二选一接入:
1. **B0 检测器适配器**(规划中):ONNX→`visual/perception` 通用推理,输出类别×bbox 进既有 `OBJECT_*` 事件链;
2. **MediaPipe ObjectDetector 格式**:模型转 .tflite 放 `models/`,`DetectorCfg.model` 指向、`classes` 对齐标签——**零代码改动**。

## 四、上岗闸门(不可跳过)

1. 固定回归集跑分:`python -m visual regress <manifest> --data <dir>` —— 一致率不低于现役引擎;
2. 影子模式 ≥2 周:`serve --shadow` → `python -m visual shadow-report` —— 漏检/误杀双指标达标;
3. 许可台账登记(数据集 + 基座模型,ADR-03)。
