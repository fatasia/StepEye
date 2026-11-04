# 工业质量模型：资源与缺口

2026-11-04 核查密封钉、电芯六面、电池包、胶路、针脚、表盘。下表的“未取得”只针对列明的本轮来源，不代表没有其他公开资源。

| 场景 | 本项目已有 | 本轮新找到／核实 | 当前可接范围 |
|---|---|---|---|
| 密封钉 | 无专用权重；焊缝OK/KO不等同密封钉 | 作者LGASS与OPT-SND数据入口，3D点云；数据需同意研究限制，原checkpoint未随代码提供 | 本轮未取得可直接运行的专用权重；不把3D研究改名为普通相机检测 |
| 电芯六面 | 六面设备结果/SN汇总；电池焊缝分类器 | DAEF入口与既有版本相同；新核M-BSD需非商用研究协议，LIBAD数据有访问门槛 | 缺陷模型并未覆盖六面全部工艺；现有焊缝不能代替凹陷/漏液/蓝膜等类别 |
| 电池包 | 设备NG图/原判/复判；局部焊缝候选 | 作者X-ray-PBD有数据与PTH入口，但任务是电芯内部极片端点，不是Pack装配外观 | 本轮未取得可直接接入的Pack通用外观/装配缺陷权重 |
| 胶路 | 已下载并实推YOLOv4-Tiny两类Darknet权重 | 复核既有作者权重；Roboflow另有52图Glue分割集，页面Models=0 | 断胶/溢胶权重可作离线评估；许可未声明、尚非ONNX，不能直接登记生产 |
| 针脚 | 固定夹具单排轮廓；新增 PAINE U-Net 分割 ONNX 独立试跑 | 固定权重已下载、受限提取并导出，公开图 PT/ORT 掩膜一致 | 只输出针脚区域；尚未接统一分割页面，无专用缺陷分类/3D针高 |
| 表盘 | 原有VLM模板 | **新取得12.6MB四关键点ONNX，真实照片CPU推理通过** | 已新增独立GaugeService；显式量程/单位/旋向才能输出读数，不进普通框检测器 |

## 已可接：四关键点表盘模型

发布者 [Jason-Shell/gauge-ai-reader](https://github.com/Jason-Shell/gauge-ai-reader/tree/139e43a446c1cab718fb6c69b061bba9d0967ec3)，固定revision
`139e43a446c1cab718fb6c69b061bba9d0967ec3`，文件 `models/gauge_pose_e120.onnx`。

- 实际12,598,927字节；SHA256 `c5fd23e05397d75730dbef2d561ccb99fcdec8bc89a3148104559e633f139441`；Git blob SHA1也与固定树一致。
- float32 `images[1,3,640,640]` → `[1,17,8400]`：4框坐标、1个gauge置信度、4组(x,y,置信度)。关键点顺序 **min/max/tip/center**；ONNX checker通过，无外部张量或自定义算子。
- RGB、bilinear letterbox640、居中114填边、除255；独立Pose解码，不把17通道当普通类别头。CPU一次性子进程，2线程，空闲无模型/线程。
- metadata明确AGPL-3.0；作者README称训练数据Gauge v1为CC-BY4。固定树没有根源码LICENSE，未复制作者实现；照片未单列许可，留本机评估。完整数据未下载。登记仍为 `evaluation_only`。

真实照片为作者 `input/meter.jpg`，3119×3012，SHA256
`5d6ea5e13823ceac69cc25b8c29c0d2dd3560caaf1654b71c943eaed23991e94`。
新服务返回1个表盘、置信0.99048、4个关键点；一次CPU推理44.53ms，含校验/解码/子进程约1301ms，不据此计算持续FPS。
照片单位为kg/cm²与psi，不能套作者示例0–250bar。未配置时实际返回 `uncalibrated`、reading/ratio/unit均null；无独立真值准确率。

接线契约：`visual.gauge.GaugeService(ROOT).trial(raw_bytes, calibration)`；
`GaugeCalibration={min_value,max_value,unit,direction}`，旋向仅clockwise/counterclockwise，无auto。
返回归一框与四点、角度、显式旋向下的比例/读数。多表不自动选一个；量程外/端点退化为unknown，不限幅成“合格读数”。
关闭调用 `close()`；忙429、超时终止504、模型缺失503。模型与图许可状态不因接口接通而改变。

资产登记在 `sop-vision/models/public/gauge-models.json`，ONNX已复制到固定目录但不入Git。复现：

```powershell
cd sop-vision
python tools/probe_public_gauge.py --image ../.v0/quality-gaps/gauge-input-meter.jpg --output ../.v0/quality-gaps/new-probe.json
python -m pytest tests/test_gauge.py -q
```

本轮22项测试通过，包括几何跨360°、非方图角度还原、显式标定、无检测/多表/量程外、异常关键点、真实子进程超时与关闭、真图Pose链路。未改默认生产模型或共享检测后端。

## 密封钉：真实作者入口，但模态和访问条件不同

[LGASS作者代码](https://github.com/WillPANSUTD/LGASS/tree/a25b4ed06cc5b924d43d1b21cdd5c45679b16134)为Apache-2.0。
作者README明确原checkpoint已不在手、重训中，未随代码发布；HF模型入口本轮返回401，未取得权重。
[OPT-SND数据页](https://huggingface.co/datasets/vpan1226/OPT-SND)是线激光采集的3D点云，8个原始类别、论文合并6类；HF metadata虽为Apache-2.0，访问协议另写研究用途、引用、不再分发原始数据。
固定数据revision `8b363280ca7c19e522d382e5a4f3b5530902586f`；未接受访问协议、未下载/执行示例中的pickle读取。

## 六面与Pack：不混淆外观、内部成像和结果归档

[DAEF作者仓库](https://github.com/sober-white/DAEF/tree/ef0e5538a907d4647bb74dc599c8a598190c846e)版本与既有审计一致：五类表面缺陷，仓库PTH仍是COCO初始化，不是电池微调权重。数据百度入口和许可缺口见 [BATTERY-INSPECTION](BATTERY-INSPECTION.md)，没有重复下载。

新增核对 [M-BSD/DS2A-Former作者说明](https://github.com/yikuizhai/DS2A-Former/tree/7b182181bf4c51433f1762e82e81e555e9cd60a8)：2491图，damage/smudge/dust，要求学校邮箱和签署非商用科研协议；不是可无条件下载的六面成品数据。
[LIBAD作者库](https://github.com/evenrose/LIBAD/tree/8f98f2ebde63cd45660e58d6413f5ccd638966f4)是极片制造多模态异常基准；[HF数据](https://huggingface.co/datasets/Evenrose/LIBAD)标CC-BY4且gated，原文件读取401。二者不能覆盖六面SN/面别关系。

[X-ray-PBD作者仓库](https://github.com/NTU-AI4X/X-ray-PBD/tree/112b9ddc7c2c23bd9e59e2b458f2f35d00ad69da)确有数据、预训练PTH与推理入口；任务为X-ray极片端点定位，附自定义csrc依赖，未取得ONNX/核实权重发布许可。
普通RGB Pack装配、热像/BMS故障和X-ray内部端点是不同任务，不相互补位。

## 胶路与针脚：已有资产保留，新入口有具体限制

胶路继续使用[作者GAN_Synth_Adhesive](https://github.com/RicardoSPeres/GAN_Synth_Adhesive/tree/b309653ec5ef27013f5dc0d6ce1fc7bf4763c148)的既有23.5MB权重及正确两类派生配置；真实断胶实推和许可未声明的状态见 [PUBLIC-SPECIALIZED-MODELS](PUBLIC-SPECIALIZED-MODELS.md)。
新核[Roboflow Glue Defect Detection](https://universe.roboflow.com/khoaaluann/glue-defect-detection)只有Glue分割类、52图、Models=0；页面的通用API不等于可下载的断胶/溢胶权重。

针脚新增[PAINE2026作者复现包](https://github.com/lfd/paine2026/tree/8e3084fafa246a494bbeb191c44ea2412e4fffe5)，用于PCB插针分割与板级特征判定。
[Zenodo 固定权重记录](https://zenodo.org/records/21887282)提供 `unet_trained.pth`，324,758,112字节，记录许可CC-BY4；作者说明工业原始数据保密，公开子集来自Roboflow且标CC-BY4。
权重已下载并核对官方 MD5；SHA256 为 `5d45d71d311e3d4d95e01bf6beced419b95df435cd649fbbd28fd3a589534d2e`。静态审计仅八个 GLOBAL，FastCore `L` 位于优化器 `hypers`；提取器用无行为容器映射该类型，NumPy 仅接收固定 float64 标量，始终 `weights_only=True`。未运行作者分类器 pickle。277 个纯张量与官方 FastAI ResNet34 U-Net 严格匹配，未下载预训练骨干、未训练。

### 针脚分割实际结果与入口

- 自包含 ONNX **164,893,241B**，SHA256 `5f69ff521211fed4f14a2743bf94df4e888e468bf299f62378fc76e31d1f5e4e`，41,221,168 个参数。
- 固定 float32 `images[1,3,512,512]` → `logits[1,2,512,512]`。原尺度灰度切块、右下反射补齐、复制三通道、ImageNet 归一化；不拉伸整图。
- 作者固定版本测试图 `pcb_0006_jpg.rf.3643e486b19e08c1dd189e7ac8bd3bbb.jpg`，1024×221，SHA256 `f802112c4a33d62eab8741582a0f6f63c4124c1b799c6f135a9cde293efa5556`。两块共524,288像素，PT/ORT最大 logit 误差0.0001831，argmax差异0像素；裁回原图前景9,148像素。此处验证导出一致性，不是准确率评测。
- CPU两线程两块推理2,425.755ms；独立无Torch试跑2,547.867ms，含载入/校验/保存3,142.424ms。适合离线按需试跑，不进实时检测链；不新增常驻服务。
- 仅背景/针脚掩膜，`defect_verdict=null`，不判弯针、压接深度或3D针高。登记为 `evaluation_only`；已接现有视觉应用试跑页，支持原图/掩膜切换与PNG下载；尚未纳入现有运行ZIP。

本机模型位于 `sop-vision/models/public/paine-pin-segmentation.onnx`，登记 `pin-models.json`，权重不入Git。复现无需Torch/FastAI：

```powershell
cd sop-vision
python tools/probe_pin_segmentation.py --image ../.v0/quality-gaps/paine2026/sample.jpg --output ../.v0/quality-gaps/paine2026/my-probe
python -m pytest tests/test_pin_checkpoint.py tests/test_pin_segmentation.py -q
```

输出 `mask.png` 与 `report.json`。本轮8项专项检查通过；实际导出及直接ORT试跑证据分别在 `.v0/quality-gaps/paine2026/export-v1`、`ort-probe-v1`。
导出工具 `tools/export_pin_segmentation.py` 需要独立 FastAI 2.8.6 / FastCore 1.12.11 / FastProgress 1.0.5；本机隔离依赖位于 `.v0/quality-gaps/paine2026/vendor`，未加入运行依赖。Conda导出进程设置 `MKL_THREADING_LAYER=SEQUENTIAL`，无需允许重复OpenMP。

原始树、模型metadata、API响应和实推结果保存在 `.v0/quality-gaps/`；未训练、未替换其他模型。

Web接线：GET /api/models 中 task=segment；POST /api/models/{id}/trial 复用原试跑入口，返回原尺寸二值PNG，不返回检测框或缺陷结论。单次最多400万像素/16块，30秒超时，任务结束释放CPU子进程；停服终止自有任务。框标注项目明确拒绝分割模型，电池六面检测列表不混入PCB针脚分割。真实Web公开图9148前景像素、切换/下载/只读权限已检查，记录位于.v0/output/playwright/pin-scope/。
