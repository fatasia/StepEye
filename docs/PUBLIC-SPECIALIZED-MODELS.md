# 地面积水与胶路：公开候选实测

2026-11-03。取得一个湿地二分类 ONNX 和一个作者胶路 Darknet 检测器，完成真实固定图推理。**两者均未注册或接入生产**，不替换已有模型。

| 范围 | 实际资产 | 本轮结果 | 尚缺 |
|---|---|---|---|
| S11 地面积水／污渍 | ConvNeXt Tiny q8 ONNX，33.8MB，dry/wet | ONNX 检查与 CPU 真图通过；湿滑站台公开照片返回 dry 0.9982 | 不输出位置，不识别通用污渍；无工厂独立真值 |
| Q7 胶路 | 作者 YOLOv4-Tiny，23.5MB，discontinuity/excess | 原 cfg 错配80类；保留原件并派生两类 cfg 后，固定图返回4个断胶框 | 权重／数据许可未声明；非ONNX，现有后端不支持 |

固定版本、完整 SHA256、下载地址和阻塞项见 [`specialized-candidates.json`](../sop-vision/models/public/specialized-candidates.json)。这是资产审计清单，不是启动时自动加载清单。下载、脚本与原始报告留在 `.v0/specialized-models/`。

## S11：可运行的湿地二分类，不是积水定位模型

发布者 [builtbyd3v/spot-wetfloor-convnext-tiny](https://huggingface.co/builtbyd3v/spot-wetfloor-convnext-tiny/tree/942bd9c786bcb4b0e3b1521c6763a88a66134e6e)，固定 revision `942bd9c786bcb4b0e3b1521c6763a88a66134e6e`。权重声明 **CC BY 4.0**；基座 `facebook/convnext-tiny-224` 为 Apache-2.0。模型卡列训练来源为 Mendeley CC BY 4.0 和 Kaggle CC0。没有加载 PT、pickle 或作者脚本。

已下载 `onnx/model_quantized.onnx`，33,821,316 字节，SHA256：

```text
87caee35e68b0de6846ff169978321e4e8aeb6f715d700962d1f998534e315a0
```

与 HF LFS OID 一致；`onnx.checker` 通过，无外部张量或自定义算子。float32 `pixel_values [batch,3,224,224]` → `logits [batch,2]`，opset17，类别顺序 **0=dry，1=wet**。预处理按发布者配置：RGB、PIL bicubic 短边256、中心裁224、除255、ImageNet mean/std；对输出做 softmax。不能按 YOLO 框解码。

独立来源照片为 Phil Champion 的 [A slippy floor, Halifax station](https://commons.wikimedia.org/wiki/File:A_slippy_floor,_Halifax_station_-_geograph.org.uk_-_2587394.jpg)，CC BY-SA 2.0，800×600，SHA256 `c951d7c6c5c1c31d5a8d387ae77c052a9117ba2e9cf4c12f2062b6e695c3db3c`。CPU 单次推理约48ms（不含加载），返回 dry 0.998219、wet 0.001781。照片已目视检查；没有独立人工标签，也未审计与训练集的像素重复，不能据此计算准确率。图片来源称湿滑，不代表能从 RGB 测得摩擦系数。

作者明确训练图以户外道路为主，未验证工厂地面，并记录工业特写误报。`dry` 不等于安全；薄液膜、油、冰、反光和通用污渍不在可靠覆盖内。当前只保留二分类研究候选，不新增空场景或接 VLM。

**可训练数据入口**：[Dataset of Stagnant Water and Wet Surface with Annotations V4](https://data.mendeley.com/datasets/y6zyrnxbfm/4)，DOI `10.17632/y6zyrnxbfm.4`。作者 Kailas Patil、Sonali Bhutad；原站确认 CC BY 4.0、1,976张256×256 RGB、water/wet surface 两类及 YOLO 标注。完整包未下载，类别ID、配对、场景分组和负样本仍须审计；无标图片不能直接当干地面。

## Q7：取得作者权重，发现并隔离发布配置错误

来源为[作者 GAN_Synth_Adhesive 仓库](https://github.com/RicardoSPeres/GAN_Synth_Adhesive/tree/b309653ec5ef27013f5dc0d6ce1fc7bf4763c148)，对应[结构胶缺陷研究](https://doi.org/10.3390/app11073086)。固定提交 `b309653ec5ef27013f5dc0d6ce1fc7bf4763c148`。取得 `Weights/yolov4-tiny-obj_best_aug.weights`，23,530,556字节，SHA256：

```text
c51a715e886f585a1046a6b09f62270d212a8715a0e5428490c7897a88ced3a3
```

`obj.names` 明确 **discontinuity（断胶）、excess（过量胶）**，不是宽度、体积测量或三防漆检测。仓库／发布包未找到专门的权重或数据许可证；论文 CC BY 不能代替这些授权，当前不列商用已审核。

原始 `Cfg/yolov4-tiny-obj.cfg` 却保留 `classes=80`、两个输出卷积 `filters=255`。直接真实推理得到85列输出且含非有限数，已拒绝。原件保留，没有覆盖成“官方正确配置”。

本地派生 `glue-yolov4-tiny-2class-derived.cfg`：两个 `classes` 改2，对应两个卷积 `filters` 改21，即 `3×(2+5)`。逐层计数核验：原cfg需6,062,814个float，文件实际5,882,634个；派生cfg恰好需5,882,634个，权重全部有限。派生cfg SHA256 `44dbebf92ad2dba2ebcc3da31d2058cd5a94b37ad9901c3e00973b1866cca3bb`。

主机 OpenCV5 已移除 Darknet 导入器。只在 `.v0/specialized-models/probe-venv` 安装官方 PyPI `opencv-python-headless==4.11.0.86`，主环境不改。复用其 Darknet 解析，CPU、RGB/255、直接缩放416、conf0.25、按类NMS IoU0.45。

实际固定图来自作者 v1.0 `holdout.zip/1.png`，1984×1264，SHA256 `87590ddf9360e3e75e27a33fc3ff630ebfa0b667d29ab49881c3f3635ddba93b`；原标签含6处断胶。模型约134ms返回4个断胶框，置信0.2797–0.9772；预测叠图已目视检查。仅为作者holdout功能冒烟，不是独立产线准确率；本轮没有调阈值追结果。

### 数据与后续路径

- [v1.0 发布包](https://github.com/RicardoSPeres/GAN_Synth_Adhesive/releases/tag/v1.0)：`obj.zip` 464.5MB（594张真实+合成训练图）、`test.zip` 13.5MB（作者称58张验证图）、`holdout.zip` 5.9MB。只下载holdout，SHA256 `5fd644fbaf110aa5b5d5666f476613bd683da327385f57ba8461d0211280159f`；CRC、全图解码、标签边界检查通过。发布说明称18张，包内实际19对，40个断胶框／17个过量胶框，缺标0；差异已记录。
- [UniMatch GlueDataset_300](https://github.com/He001123/UniMatch/tree/7dc782c873817fb61713b9f31e29293c7a59744d/GlueDataset_300)：固定树含298张JPEG、300张分割PNG，未取得训练好的权重。仓库MIT署名为上游UniMatch作者Lihe Yang，不能据此确认新增工厂图片授权；类别、配对及原帧分组尚未审计，不列已备好训练集。
- [simulation-synth-adhesive 作者结果页](https://ricardosperes.github.io/simulation-synth-adhesive/)仍可访问，但论文指向的GitHub仓库本轮404，未从该仓库取得权重。结果图不是模型下载。

下一步先解决胶路权重／数据许可与正确配置来源，再考虑固定版本 ONNX 转换及数值对齐；不为这一个旧格式降级主环境。S11优先补独立工厂地面真值与定位任务数据。两者都需要按真实批次／采集场景隔离验证，不随机拆相邻帧。

## 本轮检查

模型／配置／图片 SHA核对、ONNX checker、Darknet参数长度、19对holdout全量静态检查、两条CPU真实推理已执行。首次 OpenCV5 不支持、原作者cfg错配的失败均保留。未训练、未改 registry/server/runtime、未接生产；公开模型下载不等于 S11/Q7 已验收。
