# 社区权重(models/community/)——来源、验证结论与许可溯源

> 权重文件(.pt/.onnx)不入库(*.pt/*.onnx 在 .gitignore);本目录只保留此说明。
> 许可注意:以下权重均为 Ultralytics 训练(AGPL 溯源)——**评估/演示可用;商用交付请按
> TRAINING.md 用同源数据自训 D-FINE/RF-DETR(Apache-2.0)替换**,或购买 Ultralytics 授权。

| 文件 | 来源 | 类别 | 验证结论(2026-11-03) | 状态 |
|---|---|---|---|---|
| helmet.pt/.onnx | HF `iam-tsr/yolov8n-helmet-detection` (best.pt) | 0=With Helmet, 1=Without Helmet | ✅ **可用**:产品图实测检出 2 顶 With Helmet;ONNX 通路+适配器工作正常 | 可用 |
| fire-smoke.pt/.onnx | HF `raidavid/yolov8_fire_and_smoke` (weights/best.pt) | 0=fireorsmoke | ❌ **不合格**:对 4 张典型火图(航拍/近景)ultralytics 原生推理全部未检出——权重弱或训练域极窄,**非适配器问题**(合成模型全链路单测证明适配器正确) | 占位,待替换 |
| ~~fire-smoke.pt(第一版)~~ | HF `Notacodinggeek/yolov8n-fire-smoke` | 类别表=俄语酒瓶品牌(假烟火模型) | 已删除——教训:**社区权重必须先读类别表再导出** | 淘汰 |

## 验证方法(可复现)

```python
# 1) ultralytics 原生交叉验证(隔离 venv: models/community/.venv-export)
from ultralytics import YOLO
r = YOLO("xxx.pt").predict("test.jpg", conf=0.2, verbose=False)

# 2) 平台适配器验证(ONNX 通路)
# DetectorCfg(model="models/community/xxx.onnx", classes=[...], class_names=[...])
# → visual/onnx_detector.OnnxObjectDetector(合成模型单测背书)
```

## 替换路径(烟火)

1. 找合格数据集(D-FIRE / Roboflow 烟火集,许可核台账)→ `visual dataset-import`;
2. 租 GPU 自训 D-FINE(见 TRAINING.md)→ 导出 ONNX → 放本目录 → `DetectorCfg` 指向;
3. 上岗闸门:回归集 + 影子 2 周(与所有检测器同一纪律)。
