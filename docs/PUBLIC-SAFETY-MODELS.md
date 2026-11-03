# 公开安全与仓储模型：实际下载与探针

2026-11-03。本批复用既有模型，另取得2个作者公开ONNX；未下载或执行PT/pickle，未安装torch，未运行作者仓库脚本。权重保留在本机，Git只记录来源、指纹与探针结果。

## 已取得的模型

| 模型 | 固定来源 | 输入/输出 | 实际状态 |
|---|---|---|---|
| 仓储物流20类 YOLOv8n | [MTerryJack/Element-Detect-WarehouseLogistics](https://huggingface.co/MTerryJack/Element-Detect-WarehouseLogistics/tree/4b37c4f133cc0b5e13007d272fcfd209700d6615) | float32 `[1,3,640,640]` → `[1,24,8400]`，opset16 | ONNX静态检查、现有640适配器与实际单图通过 |
| 烟火 CCTV YOLOv8n | [fiacecson20/cctv-ai-fire-smoke](https://huggingface.co/fiacecson20/cctv-ai-fire-smoke/tree/343990e42d99a5d27e9f35fc7c80880dc5f43f45) | float32 `[1,3,320,320]` → `[1,6,2100]`，opset20 | 原生320探针与生产ModelTrials子进程均通过；适配器读取静态原生尺寸 |

机器清单：`sop-vision/models/public/safety-models.json`。每条含本机相对路径、SHA256、大小、类别顺序、源提交、固定下载URL、许可审核状态和实际探针结果。权重分别是 `warehouse-logistics.onnx`（12,253,107字节）和 `fire-smoke-cctv.onnx`（12,139,911字节）；SHA256见清单。

仓储模型类别：barcode、car、cardboard box、fire、forklift、freight container、gloves、helmet、ladder、license plate、person、qr code、road sign、safety vest、smoke、traffic cone、traffic light、truck、van、wood pallet。类别存在不代表每类已验证；barcode/qr code类别只是定位框，不是解码器；gloves/helmet也不能直接等同佩戴合规判定。

## 实际单图探针

固定每个模型的原生输入尺寸，没有分辨率扫描。CPUExecutionProvider，复用 StepEye 的 letterbox、YOLO解码与按类NMS。

- 仓储作者 `example.png`：叉车1框（0.669）、人员1框（0.578）、木托盘6框（0.251–0.460）；已目视检查原图为仓储叉车场景。717ms 为首次原生进程往返，含启动及模型加载，不是稳态帧耗时。
- 既有 `test-helmet.png`：安全帽5框、人员6框、反光衣1框；最高安全帽置信0.688。该图只用于功能冒烟，未建立人工框级真值，人数重复/误报未量化。
- 新烟火模型对既有3张测试图：`test-fire.jpg` 返回烟雾0.285（可见火焰未返回fire）；`test-fire2.jpg` 返回烟雾0.797；`test-fire3.jpg` 返回火焰0.652。阈值均0.25，无调阈值追结果。3次冷进程往返约674–696ms，不是独立集召回率。
- 旧 `community/fire-smoke.onnx` 不覆盖、不启用；其既有不合格结论保留。旧安全帽权重保持原样。

探针原件位于 `.v0/public-models/{warehouse,fire}/`，模型指纹、图片指纹和结果摘要写入机器清单。作者样图可能与其训练/验证数据有关，本轮没有拿它计算泛化指标。

## 许可与候选边界

- 仓储仓库未声明明确权重许可，来源指向 Roboflow logistics 数据；先作为本机评估资产，不标“商用已审核”。
- 烟火作者声明权重MIT、源数据CC-BY4.0，并明确提示Ultralytics基座AGPL要求；本项目尚未核完许可链，清单同样标 `evaluation_only` 与 `commercial_review_required`。
- [Qualcomm PPE-Detection](https://huggingface.co/qualcomm/PPE-Detection) 有原生ONNX候选，但不是本批YOLO适配目标，未下载和移植，不能算已接功能。
- `constructelligence/construction-site-safety-hazards` 的API读取出现TLS错误，本轮不反复重试、不下载未经核对的权重。
- `rabahdev/fire-smoke-yolov8n` 固定提交 `13017fe8af477c25f5298d168e2dfede4b000753` 仅见PT检查点与AGPL模型卡；优先ONNX策略下未加载它。

## 复现

在仓库根目录运行；`--out` 必须是新文件，避免覆盖证据：

```powershell
python sop-vision/tools/public_model_probe.py --model sop-vision/models/public/warehouse-logistics.onnx --image .v0/public-models/warehouse/example.png --out .v0/warehouse-new-probe.json
```

工具先检查ONNX（拒绝外部张量与自定义算子），再用短命原生CPU子进程按模型原生尺寸推理，默认30秒超时。结果中的 `adapter_compatible` 表示当前静态方形单输入路径的兼容性，不由“探针成功”自动变成True。320生产接线另以既有fire3测试得到fire置信0.652、648ms冷启动往返；现场评测仍未完成。
