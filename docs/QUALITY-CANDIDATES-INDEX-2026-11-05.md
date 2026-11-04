# 质量检测候选模型统一索引（2026-11-05）

交接单 B4 项：对分散在 15 个登记 JSON 与多篇审计文档中的质量类候选模型做统一视图。本文是**只读整理**：不训练、不下载、不修改任何登记 JSON 与模型文件；所有数字从登记文件直接引用并注明来源，登记里没有的标"未登记"。文档侧补充数字单独注明来源文档，用于说明登记缺什么。

## 1. 盘点范围

登记文件（`sop-vision/models/public/`，共 15 个 JSON）：

| 登记文件 | checked_at | 内容 |
|---|---|---|
| battery-candidates.json | 2026-11-03 | EV 焊缝数据集审计 + pilot 训练登记 |
| classification-models.json | 2026-11-03 | 湿地面 / 焊缝 CNN v1/v2 分类 |
| quality-candidates.json | 2026-11-03 | PCB/表面/包装/极片数据与模型候选 |
| quality-models.json | 2026-11-04 | PCB 六类、极片针孔、PCB 九类 |
| joint-candidates.json | 2026-11-03 | 焊缝检测、PCB 引脚 ROI 分类 |
| glue-models.json | 2026-11-04 | 胶路断胶/过量胶 ONNX |
| pin-models.json | 2026-11-04 | PAINE 针脚分割 |
| ppe-candidates.json | 2026-11-03 | PPE 14 类候选 |
| safety-training-candidates.json | 2026-11-04 | 腕带 v1/v2 训练候选 |
| safety-models.json | 2026-11-04 | 仓储 20 类、烟火 CCTV、腕带 v2（重复登记） |
| specialized-candidates.json | 2026-11-03 | 湿地面、胶路 Darknet 源、积水/胶数据集 |
| specialized-detection-models.json | 2026-11-04 | PPE14、焊缝、烟火三候选 |
| gauge-models.json | 2026-11-04 | 表盘四关键点 |
| water-models.json | 2026-11-04 | 地面积水 RT-DETR |
| industrial-action-candidates.json | 2026-11-05 | 工业动作四类线性头 |

审计文档：`docs/BATTERY-MODEL-FINAL-AUDIT.md`、`MODEL-GAPS-QUALITY.md`、`SAFETY-MODEL-AUDIT-2026-11-04.md`、`MODEL-REMAINING-2026-11-04.md`、`PCB-NINE-TRAINING.md`、`GLUE-ONNX.md`、`MODEL-GAPS-SAFETY-SOP.md`、`PCB-ERROR-ANALYSIS-2026-11-05.md`，及 `HANDOFF.md` 末尾各批次记录。

同工件重复登记（同一 sha256 多处出现，本文各计 1 行）：
- `public-battery-weld-cnn-v1`（classification-models.json）= `ev-battery-weld-cnn-pilot-v1`（battery-candidates.json trained_models[0]），sha `b2fc4b04…`
- `public-wetfloor-convnext-q8`（classification-models.json）= `wetfloor-convnext-tiny-q8`（specialized-candidates.json），sha `87caee35…`
- `public-welding-defect-yolov8`（specialized-detection-models.json）= `welding-defect-yolov8`（joint-candidates.json），sha `dbddf8b3…`
- `public-ppe-construction-v03`：ppe-candidates.json 与 specialized-detection-models.json 双登记，sha `55f4dd2f…`
- `experimental-visible-wrist-v2`：safety-training-candidates.json 与 safety-models.json 双登记，sha `4348fa1b…`
- `public-glue-yolov4-tiny`（glue-models.json）是 specialized-candidates.json 中 Darknet 权重 `gan-synth-adhesive-yolov4-tiny` 的 ONNX 转换产物（源权重 sha `c51a715e…`），后者未标 superseded。

## 2. 总表（21 个已登记模型工件）

评测口径均为登记声明的固定工作点；"独立评测"指独立于作者/发布者数据与选模验证集的第三方或产线测试。

| # | 模型 id（别名） | 任务 | 覆盖类别 | 未覆盖类别 | 适配器 / 输入规格 | 权重与数据来源·许可 | 独立评测范围与指标 | 已知失败样例 | 当前可用级 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | public-battery-weld-cnn-v1（=ev-battery-weld-cnn-pilot-v1） | 分类 | EV 焊缝外观 OK/KO | 密封钉、六面/SN、缺陷定位框（battery-candidates.json limitations："no boxes, no six-face/SN capability"） | 分类契约，不进 YOLO 框检测器；gray224 [1,1,224,224]→logits[1,2]（classification-models.json） | 数据 Zenodo 10.5281/zenodo.14025630 CC-BY-4.0（MD5 已验，61,670 图）；权重项目自训随机初始化，发布许可未指定（battery-candidates.json） | 无独立评测：pilot 官方 split 内抽样 2000 图、哈希去重、工件分组未知；acc 0.7805、KO 召回 0.9058、KO 精确 0.4619、KO 误放 0.0942（battery-candidates.json evaluation） | KO 漏放 36/382=9.42%，OK 误杀 403/1618；无逐图清单（未登记） | evaluation_only，production_approved=false |
| 2 | public-battery-weld-cnn-v2 | 分类 | EV 焊缝 OK/KO | "not sealing-pin localization or six-face acceptance"（classification-models.json） | 同 v1 契约 gray224 [1,1,224,224] | 项目自训（v1 best-state 初始化，初始 state sha `4a7996d9…`）；43,169 训练图/5,000 test；数据 CC-BY-4.0，code/weights 发布条款未单列（classification-models.json source） | 登记未含数字；同批 5,000 图官方 split 内对比（来源 docs/BATTERY-INSPECTION.md）：总 acc 85.56%、平衡 acc 86.92%、KO 召回 89.12%、KO 精确 57.96%、KO 误放 10.88%、良品误杀 15.28% | "fewer false rejects, four more false accepts"（classification-models.json；文档侧：误杀 1045→618、漏放 100→104）；逐图未登记 | evaluation_only，production_ready=false，"neither version release-approved" |
| 3 | coating-pinhole-enhanced（极片针孔） | 检测 | pinhole 单类 | CoatingVision 其余类别（Surface_Crack/Delamination/unclassified 检测框未训）、胶宽/3D（未登记明确声明，由 quality-candidates.json 类别表推得） | yolo_raw；输入规格未登记（quality-models.json 该条无 native_input） | 数据 CoatingVision figshare 29260121.v1 CC-BY-4.0（MD5 匹配）；权重 AGPL-3.0 派生（"existing 3-epoch checkpoint + 30 epochs; R1 train / R7 validation"，quality-models.json） | 登记未含评测；来源 docs/HANDOFF-2026-11-03.md：固定 21 图 TP17/FP6/FN4、R=.810/P=.739，且 R7 仍是选模验证集（非独立） | 登记未含；数据侧 blocker：1646 图无检测标注、unclassified 仅 78 图低于每类最小值（quality-candidates.json） | usage 字段未登记（quality-models.json 仅 production_ready=false；许可 not_release_approved） |
| 4 | public-pcb-yolo26（=betty0-pcb-yolo26s） | 检测 | missing_hole、mouse_bite、open_circuit、short、spur、spurious_copper 六类 | HB/CS/CFO/BMFO（九类集的其余四类）；登记未另声明 | yolo_e2e；[1,3,640,640]→[1,300,6]（quality-candidates.json/quality-models.json） | HF betty0/pcb-defect-detection rev 08f7ff0；权重 AGPL-3.0；训练数据 HRIPCB Kaggle 镜像许可 Unknown（quality-candidates.json licenses） | 无独立评测；固定 DeepPCB 二值样本试推 targets=0、accuracy=null（quality-candidates.json validation） | "fixed DeepPCB binary sample returned zero detections; no target-production evaluation"（quality-candidates.json blockers） | evaluation_only_license_review_required（quality-candidates.json status） |
| 5 | public-pcb-nine-v1 | 检测 | SH、SP、SC、OP、MB、HB、CS、CFO、BMFO 九类 | 登记未声明（训练集覆盖全九类；弱类见失败栏） | yolo_raw；[1,3,640,640]→[1,13,8400]（quality-models.json） | 数据 DsPCBSD+ figshare 24970329.v1 CC-BY-4.0（整包 sha `47eb32b6…`）；YOLOv8n 30ep；权重 AGPL-3.0 派生，not_release_approved（quality-models.json） | 作者留出 2051 图、conf 0.25/IoU 0.5，"no verified physical-board independence"：P 0.7265 / R 0.7407 / F1 0.7335；HB R 0.9720 最高，CFO R 0.5012、CS P=R 0.6161 最弱（quality-models.json test） | PCB-ERROR-ANALYSIS-2026-11-05.md §7：CFO 漏检前 20 图（Y_011029.jpg 等）、CS 误检/漏检各前 20 图；CFO 真值被跨类强盖 66 对（MB 35/BMFO 13/OP 12）；CS fp 中重复型 66、定位偏差 72（IoU 中位 0.22） | evaluation_only；"不作自动放行模型"（PCB-NINE-TRAINING.md） |
| 6 | public-glue-yolov4-tiny（Darknet 源：gan-synth-adhesive-yolov4-tiny） | 检测 | 断胶 discontinuity、过量胶 excess | "not glue width, volume, UV coverage or 3D"（glue-models.json limitations）；三防漆覆盖率（MODEL-REMAINING-2026-11-04.md） | 专用 glue_yolov4 适配器；[1,3,416,416]→[1,2535,7]；RGB/255 直接缩放非 letterbox；末两列已含 objectness（glue-models.json/GLUE-ONNX.md） | GAN_Synth_Adhesive rev b309653 权重（sha `c51a715e…`），项目转 ONNX（`4a398f4e…`）；"Weights and dataset license not declared by publisher"，commercial_review_required=true（glue-models.json） | 无独立评测：作者 holdout 19 图 57 框 conf 0.25：P 0.6875 / R 0.5789 / F1 0.6286；断胶 P0.710/R0.550，过量胶 P0.647/R0.647（glue-models.json validation） | FN 24（断胶 18、过量胶 6，glue-models.json）；Darknet 探针 1.png 真值 6 框仅检出 4 框 discontinuity（specialized-candidates.json detected_labels）；逐图清单未入登记 | evaluation_only |
| 7 | public-paine-pin-segmentation | 分割 | background、pin 二类掩膜 | "no defect/OK/NG classifier"；"No 3D height, press-fit depth or six-face inspection"（pin-models.json limitations） | independent_pin_segmentation；[1,3,512,512]→[1,2,512,512]；灰度原尺度切块+右下反射补齐（pin-models.json） | paine2026 rev 8e3084f；Zenodo 21887282 权重记录 CC-BY-4.0；作者工业数据保密、Roboflow 公开子集 CC-BY-4.0；源码无根 LICENSE（pin-models.json license_status） | 无准确率评测（登记如实声明 "No independent accuracy evaluation"）；仅 PT/ORT 掩膜一致性验证（差异 0 像素，属导出验证非准确率，MODEL-GAPS-QUALITY.md） | 未登记（无准确率评测可产生失败样例） | evaluation_only |
| 8 | public-ppe-construction-v03（PPE14） | 检测 | person、hardhat、no-hardhat、safety vest、no-safety vest、no-mask、gloves、safety shoes、excavator、wheel loader、dump truck、goggles、no-goggles、no-gloves | 正向 mask、耳塞、安全带、防护服细类（MODEL-GAPS-SAFETY-SOP.md："只有 no-mask，没有正向 mask"）；ESD 材料/电阻 | yolo_raw；[1,3,640,640]→[1,18,8400]（ppe-candidates.json） | HF constructelligence/construction-site-safety-hazards rev 79673cf；权重 AGPL-3.0（Ultralytics 派生）；训练数据 CC-BY-4.0（发布者声明，归属链未独立审计）；license_approved_for_release=false | 无独立评测（accuracy=null）；两探针为发布者标注拼图+项目冒烟图，"not independent evaluation"（ppe-candidates.json probes） | conf 0.25 全拼图上 gloves、goggles、no-gloves、no-goggles 四类零检出（ppe-candidates.json probes[0].missing_requested_labels + blockers） | evaluation_only |
| 9 | experimental-visible-wrist-v1 | 检测 | ring、watch、wrist-strap（可见近景） | "no missing-PPE inference or ESD electrical compliance"（safety-training-candidates.json limitations） | yolo_raw（v1 无 adapter_compatible 字段；输入 640 见 docs/SAFETY-MODEL-AUDIT-2026-11-04.md） | Edge Impulse 公开项目 129799（取得 141/196 图），数据声明 Apache-2.0；训练框架/基座 Ultralytics AGPL-3.0 或商业授权（safety-training-candidates.json） | 无独立评测：作者 test 31 图、同拍摄会话、49 对 train/test 近重复候选：P 0.9231 / R 0.2069 / F1 0.3380；wrist-strap R0.4、watch R0.222、ring R0.0（safety-training-candidates.json test） | ring 20 FN 全漏（recall 0.0）；逐图清单未登记（证据 .v0/safety-model-audit/edge-wrist/） | status=rejected_for_deployment，usage=evaluation_only |
| 10 | experimental-visible-wrist-v2 | 检测 | ring、watch、wrist-strap | "no absent-PPE or ESD electrical compliance inference"；"No independent factory validation"（safety-training-candidates.json limitations） | yolo_raw，adapter_compatible=true | 同 v1 数据源扩至 177 图（118 train/24 val），GPU 40ep；许可同 v1（weights AGPL-3.0 派生 / 数据 Apache-2.0，not_release_approved）（safety-training-candidates.json） | 无独立评测：同一作者 test 31 图：P 0.9355 / R 0.5 / F1 0.6517；wrist-strap R0.85、watch R0.667、ring R0.0；新增 4 图 added4 R0.556（safety-training-candidates.json test） | "ring recall 0; not a working ring detector"；另 19 张公开图下载失败（MODEL-REMAINING-2026-11-04.md）；逐图未登记 | status=partial_candidate_not_for_deployment，usage=evaluation_only |
| 11 | public-warehouse（仓储 20 类） | 检测 | barcode、car、cardboard box、fire、forklift、freight container、gloves、helmet、ladder、license plate、person、qr code、road sign、safety vest、smoke、traffic cone、traffic light、truck、van、wood pallet | 登记未声明（类别表无 no-goggles/no-gloves/safety shoes 等负类与细类，由类别表对比得出） | 登记无 adapter 名字段（仅 adapter_compatible=true）；[1,3,640,640]→[1,24,8400]（safety-models.json） | HF MTerryJack/Element-Detect-WarehouseLogistics rev 4b37c4f；license_status="not_declared"，commercial_review_required=true（safety-models.json） | 无独立评测：validation="single_image_smoke_only"，两图探针；"非独立评测集；无生产准确率结论"（safety-models.json notes） | 未登记 | usage=evaluation_only |
| 12 | public-fire（烟火 CCTV） | 检测 | fire、smoke | 蒸汽/弧光（MODEL-REMAINING-2026-11-04.md："烟火蒸汽/弧光…仍需各自的独立验证"） | 登记无 adapter 名字段；[1,3,320,320]→[1,6,2100]（safety-models.json） | HF fiacecson20/cctv-ai-fire-smoke rev 343990e；license_status="author_mit_base_agpl_review_required"（safety-models.json） | 无独立评测：single_image_smoke_only，3 图探针（safety-models.json probes） | 未登记 | usage=evaluation_only |
| 13 | public-fire-smoke-longlivewama-v1 | 检测 | fire、smoke（@832） | 登记未声明 | yolo_raw；[1,3,832,832]→[1,6,14196]（specialized-detection-models.json） | GitHub longlivewama/fire-and-smoke-detection；权重 Apache-2.0（源仓库 LICENSE；ONNX 内嵌样板字符串与实际不符，以源仓库为准）；commercial_review_required=false | 无独立评测：自报 0.623 mAP50（Roboflow 集）；10 图试推 | 10 图试推：夜景火检出但最高置信框为误报，烟雾召回弱；建议阈值 0.3–0.35（specialized-detection-models.json source.note） | usage=evaluation_only |
| 14 | public-fire-smoke-zyazyz008-v1 | 检测 | fire、smoke（@640） | 登记未声明 | yolo_raw；[1,3,640,640]→[1,6,8400]（specialized-detection-models.json） | ModelScope zyazyz008/yolov8-fire-smoke-merged；权重 Apache-2.0（模型卡；ONNX 内嵌字符串不符以卡为准）；commercial_review_required=false | 无独立评测：自报 mAP50 79.57%（自建合并集）；10 图试推 | 10 图试推：夜景明火漏检（烟柱 0.791 检出、负样本零误报；specialized-detection-models.json source.note） | usage=evaluation_only |
| 15 | public-fire-smoke-dfire-yolov8n-v1 | 检测 | smoke、fire（@640，D-Fire 自训 40ep） | 登记未声明 | yolo_raw；[1,3,640,640]→[1,6,8400]；类别序为 D-Fire 官方序 [smoke,fire]，与两个社区候选相反，接入注意 | D-Fire（Stevebirth/fire-ulm-dfire CC0 镜像，集合级）自训；基座 YOLOv8n AGPL-3.0；commercial_review_required=true（specialized-detection-models.json） | 本表中最接近独立评测的口径：D-Fire test 严格 holdout 200 图：P 0.736 / R 0.668 / F1 0.700，AP50 0.730，AP50-95 0.392，48ms/img；4 负样本零误报（specialized-detection-models.json source.note） | 40ep 未收敛、100ep 有反超空间（同上 note） | usage=evaluation_only |
| 16 | public-welding-defect-yolov8（=welding-defect-yolov8） | 检测 | Bad Weld、Good Weld、Defect | "Visible weld does not imply internal X-ray/solder-pin defect coverage"（specialized-detection-models.json limitations） | yolo_raw；[1,3,640,640]→[1,7,8400] | HF avinashhm/welding-defect-yolov8-full-training rev 2cbc6dc；权重 ONNX metadata AGPL-3.0、模型卡未单列；数据 The Welding Defect Dataset v2 许可 unknown（joint-candidates.json licenses） | 无评测：accuracy="not_evaluated"；实推图为作者标注 validation montage，"not independent accuracy evidence"（joint-candidates.json trial） | 未登记（无准确率评测） | usage=evaluation_only；release_approved=false |
| 17 | pcb-lead-solderinsufficient | ROI 分类 | Golden、SolderInsufficient | 登记未声明（仅两类 AOI ROI） | 未实现："explicit ROI classifier, not bounding-box detector; verify output activation before probability conversion"（joint-candidates.json adapter_needed） | HF JcProg/PCBInspect-LeadDefect rev fe29664；许可冲突：模型卡 MIT vs ONNX metadata AGPL-3.0；数据为专有 AOI ROI 裁剪未发布（joint-candidates.json licenses） | 无评测：trial="onnxruntime_session_loaded_static_only"，accuracy="not_evaluated"（joint-candidates.json） | 未登记 | 候选（joint-candidates.json），未入 models/public 正式登记；release_approved=false |
| 18 | public-wetfloor-convnext-q8（=wetfloor-convnext-tiny-q8） | 分类 | 地面 dry / wet 整图二分类 | "no location/segmentation/ESD or safe-floor verdict"；"no coverage claim for oil, thin films, generic stains or slippery-floor friction"（specialized-candidates.json blockers） | 分类不入检测注册表："Classification logits are not YOLO boxes"；imagenet_center224 [1,3,224,224]→[1,2]（specialized-candidates.json/classification-models.json） | HF builtbyd3v/spot-wetfloor-convnext-tiny rev 942bd9c；权重 CC-BY-4.0（发布者）、基座 Apache-2.0、数据链 pending（classification-models.json license_status） | 无评测（accuracy=null）；仅单公开图探针（specialized-candidates.json validation） | 公开湿滑地面图（Halifax station, sha `c951d7c6…`）被判 dry 0.9982（specialized-candidates.json validation.scores；classification-models.json limitations："existing slippy-floor image returns dry"） | usage=evaluation_only |
| 19 | public-gauge-pose-e120 | 姿态/关键点 | 单表盘 4 关键点 min/max/tip/center + 显式标定读数 | "One gauge only"；"Manual linear range/unit/direction; no OCR or automatic scale guessing"；"no perspective correction or nonlinear scale support"（gauge-models.json limitations） | independent_gauge_pose_4keypoints；[1,3,640,640]→[1,17,8400]；需 min_value/max_value/unit/direction 标定（gauge-models.json） | GitHub Jason-Shell/gauge-ai-reader rev 139e43a；ONNX metadata AGPL-3.0；数据 Gauge v1 CC-BY-4.0（作者声明）；源码无根 LICENSE；照片许可未单列（gauge-models.json license_status） | 无准确率评测："Keypoints and single-image output are not accuracy evidence"（gauge-models.json limitations）；真实照片探针 conf 0.99048（数字在 MODEL-GAPS-QUALITY.md，登记 JSON 仅含 probe_image 元数据） | 未配置标定时返回 uncalibrated、reading/ratio/unit=null（gauge-models.json 接线契约，MODEL-GAPS-QUALITY.md）；失败样例字段未登记 | usage=evaluation_only |
| 20 | experimental-industrial-action-v1 | 动作片段分类 | align_objects、tighten_nut、fit_nut、loosen_nut | "streaming boundary detection unverified"（连续视频步识别未验证）；"must not replace the generic K710 action model without approval"（industrial-action-candidates.json limitations） | videomae_feature_linear_head（npz 10,670B）；冻结 VideoMAEv2 特征 + 16 帧均匀采样（industrial-action-candidates.json） | IndustReal Apache-2.0（4TU，10 条子集 260,237,319B，官方 split，subject leakage check passed）；VideoMAEv2 代码 MIT/权重 Apache-2.0；head 自训（industrial-action-candidates.json） | 无独立评测：官方固定 split test 16 段 acc 0.5625（基线随机 0.25）；val acc 0.5556；"NOT continuous-video step recognition"（industrial-action-candidates.json test） | tighten_nut/fit_nut 混淆主导错误（混淆矩阵已登记）；45 train/18 val/16 test 段规模极小；逐段明细 evidence_dir | status=research_candidate，usage=evaluation_only |
| 21 | public-kairos-water | 检测 | water 单类（地面积水） | "Small spill recall weak; no box is not dry."；非滴漏/管线泄漏模型（water-models.json limitations；MODEL-GAPS-SAFETY-SOP.md） | kairos_rtdetr（非 YOLO raw/e2e）；动态 batch [3,640,640]→logits+pred_boxes（water-models.json） | HF builtbyd3v/kairos-water-rtdetr rev 54c3bb4；权重 Apache-2.0（作者卡）；训练数据作者卡 CC BY 4.0（Mendeley/Roboflow）+ CC0（Kaggle/SCAND），"individual source review required"；commercial_review_required=true（water-models.json） | 无独立评测：single_public_image_smoke_only，1 图 1 框 conf 0.862（water-models.json probes） | 小水洼召回弱（定性，无量化样例，未登记）；旧 letterbox 探针 0.882/0.169 已作废（"Earlier letterbox probe superseded"，water-models.json） | usage=evaluation_only |

## 3. 各登记 JSON 字段完整性检查

核对字段：任务类型 / 覆盖与未覆盖声明 / 适配器与输入规格 / 权重与数据来源 / 许可状态 / 独立评测 / 失败样例 / 可用级（usage）。

| 登记文件 | 缺失或不完整字段 | 补齐建议 |
|---|---|---|
| battery-candidates.json | v2 未在此登记（仅 classification-models.json 且无指标数字）；无逐图失败样例；trained_models[0] 无 usage 字段；DAEF-BSD data_license 未声明（已如实标注） | v2 的 5000 图对比指标（docs/BATTERY-INSPECTION.md）入登记或加 cross_ref；为 KO 漏放/误杀图建立样例清单；pilot 补 usage 字段 |
| classification-models.json | 三个模型均无评测数字字段（v1 指标在 battery-candidates.json；v2 仅在 docs；wetfloor probe 在 specialized-candidates.json 且 accuracy=null）；wetfloor 失败样例（湿滑图判 dry 0.9982）未写入本文件 | 评测字段内联或加 evidence 指针；把 wetfloor 失败探针复制为登记内 failure_sample 字段 |
| glue-models.json | 无逐图失败样例（24 FN 未列图名）；权重/数据许可未解决（已如实声明 commercial_review_required） | 逐图匹配明细（.v0/specialized-models/glue-onnx-eval-v1/ 已有）以路径形式入登记；许可跟进设责任人与复查日期 |
| pin-models.json | 无任何准确率评测（已如实声明）；无失败样例；probe 计时证据未入登记 | 加 probe_evidence 指针（.v0/quality-gaps/paine2026/）；如后续做掩膜 IoU 评测再补指标 |
| ppe-candidates.json | accuracy=null（如实）；无独立评测集；"手套/护目镜未检出"仅在 probes/blockers，未形成失败样例字段 | 五用品逐人独立评测后补 accuracy；把 missing_requested_labels 固化为 failure_samples 字段 |
| safety-training-candidates.json | v1 与 v2 字段不一致（v1 无 adapter_compatible）；逐图失败样例未登记 | 统一 v1/v2 字段；加 test-evaluation.json 指针；ring 类建议显式标注 unusable_class |
| gauge-models.json | 无读数准确率评测（已声明）；无失败样例字段 | 增加标定-读数一致性测试协议与样例登记位 |
| quality-candidates.json | 数据候选注册完整性较好（status/blockers/next_step 齐全）；betty0 候选 accuracy=null 已如实记录 | 保持 kind 字段区分 dataset/pretrained；deeppcb-author 的 split_group_overlap（7 组跨 split）结论同步到后续训练数据决策 |
| quality-models.json | **最不完整**：三条模型均无 task、无 usage 字段；pcb-yolo26 无 validation、无 limitations；coating-pinhole-enhanced 无 native_input/native_output、无 validation、无 limitations；仅 pcb-nine-v1 有完整 test | 为三条补 task/usage 字段；pcb-yolo26 与 coating-pinhole 补输入规格与 limitations；coating-pinhole 的 21 图评测（docs/HANDOFF-2026-11-03.md）入登记并标注"选模验证集、非独立" |
| safety-models.json | warehouse/fire 无 adapter 名字段、无准确率；experimental-visible-wrist-v2 与 safety-training-candidates.json 完全重复登记 | 重复登记加 cross_ref 防双头维护失同步；warehouse 许可跟踪设责任人 |
| specialized-detection-models.json | PPE14/welding 的 validation 为字符串摘要，无结构化指标；fire 候选 trial 数字在 source.note 文本里 | PPE/welding 升级为结构化 validation 字段；dfire 的 holdout 指标与类别序警告已登记（保持） |
| joint-candidates.json | welding/lead 均 accuracy=not_evaluated（如实）；lead 权重许可冲突（模型卡 MIT vs ONNX metadata AGPL-3.0）未解决 | 冲突解决前不得迁入 models/public 正式登记；补 ROI 分类器适配器规格后再评估 |
| specialized-candidates.json | wetfloor accuracy=null（如实）；gan-synth-adhesive 条目已被 ONNX 转换取代但未标 superseded | gan-synth 条目加 superseded_by=public-glue-yolov4-tiny；rejected_leads 记录完整（保持） |
| industrial-action-candidates.json | 完整性最好（training/test/混淆矩阵/limitations/license 齐全）；连续视频边界未验证（已如实声明） | 可选：逐段预测明细字段（evidence_dir 已有指针） |
| water-models.json | 无独立标注评测；"小水洼召回弱"无量化数字与样例图 | 建立室内标注小样集后补评测；弱召回样例图编号入登记 |

## 4. "不能做什么"（复述各登记与文档已有声明，未改写）

**分割 ≠ 缺陷判定。** pin-models.json：分割仅输出 "Binary pin mask only; no defect/OK/NG classifier"、"No 3D height, press-fit depth or six-face inspection"。MODEL-GAPS-QUALITY.md："只输出针脚区域……不判弯针、压接深度或 3D 针高"、"框标注项目明确拒绝分割模型，电池六面检测列表不混入 PCB 针脚分割"。

**分类概率 ≠ 伪框、整图结论 ≠ 定位。** specialized-candidates.json（wetfloor）："Classification logits are not YOLO boxes; existing detection registry must not guess this format"、"dry is not safe"。classification-models.json：湿地面为 "Image-level advisory classification, no location/segmentation/ESD or safe-floor verdict"；焊缝 CNN 为 "Image-level OK/KO research prediction, not defect localization or release verdict"。docs/BATTERY-INSPECTION.md："不进 YOLO 框检测器"。

**检出类名 ≠ 放行判定。** specialized-detection-models.json（焊缝）："Good Weld is a detection class, not a release verdict"、"Visible weld does not imply internal X-ray/solder-pin defect coverage"。glue-models.json："not an automatic product release model"、"Only discontinuity/excess; not glue width, volume, UV coverage or 3D"。ppe-candidates.json："Visible shoes/gloves do not establish protective material certification or ESD resistance"、"do not switch detector and assume compliance rules are integrated"。water-models.json："no box is not dry"。

**未检出 ≠ 未穿戴。** MODEL-GAPS-SAFETY-SOP.md："没检出不能直接判未穿戴"。safety-training-candidates.json（腕带）："no missing-PPE inference or ESD electrical compliance"、"not a working ring detector"（ring 召回 0）。

**研究候选 ≠ 生产模型。** industrial-action-candidates.json："research candidate; must not replace the generic K710 action model without approval"、"NOT continuous-video step recognition"。battery-candidates.json："pilot false acceptance of KO 9.42%; not production qualified"。PCB-NINE-TRAINING.md："CFO 召回 0.5012、CS 精确率/召回 0.6161 仍弱，不作自动放行模型"。quality-candidates.json（CoatingVision 数据）："never randomly split same-frame patches/adjacent frames"。

**发布者/作者评测 ≠ 独立评测。** glue-models.json："Author holdout only; no independent target-line validation"。safety-training-candidates.json："author same-session holdout; 49 train/test near-duplicate candidate pairs"。quality-models.json（pcb-nine）："author held-out images; no verified physical-board independence"。ppe-candidates.json："publisher annotated four-panel collage; not independent evaluation"。gauge-models.json："Keypoints and single-image output are not accuracy evidence"。

## 5. 数据集候选与被拒线索（非模型，附录）

| 候选 | 类型 | 许可 | 状态（来源） |
|---|---|---|---|
| ev-battery-weld-aesthetical | 数据集（61,670 图） | CC-BY-4.0，MD5 验证 | 已完整下载审计，pilot/v2 已用（battery-candidates.json） |
| coatingvision-v1 | 数据集 | 数据 CC-BY-4.0；代码未声明 | 已下载需分组复核；1646 图无检测标注（quality-candidates.json） |
| DAEF-BSD | 数据集 | 未明确声明 | 未下载，仅百度入口（battery-candidates.json） |
| deeppcb-author | 数据样本 | 仓库 MIT；数据集仅限研究用途 | 单样本验证；7 个 source-group 跨 trainval/test 重叠（quality-candidates.json） |
| gc10-det-author / merge-yolo-packaging-author / securecoating-community-yolo26 | 引用 | 未声明 / 未声明 / AGPL-3.0 | 未取得可验证工件（quality-candidates.json） |
| stagnant-water-wet-surface-v4 | 数据集（1976 图） | CC-BY-4.0 | 未完整下载（specialized-candidates.json） |
| unimatch-glue-300 | 数据集（298 对） | 未确立 | 未下载，需按父图分组（specialized-candidates.json） |
| 密封钉 LGASS / OPT-SND | 数据/代码入口 | Apache-2.0 代码；OPT-SND 另有研究用途协议 | 无可用 checkpoint；3D 模态不冒名 RGB 检测（MODEL-GAPS-QUALITY.md） |
| 被拒：thalos-oil-gas-safety-v1 | 权重 | — | 实际序列化类别仅 6 类 PPE，不含 leak，不登记（SAFETY-MODEL-AUDIT-2026-11-04.md） |
| 被拒：itsadityabaniya/yolov8n-ppe、qualcomm/PPE-Detection、kareem1232/spill、srao0996/segformer-sealants、湿地面标识牌类 | 候选 | — | 无 ONNX/类别不符/无权重/域不符（ppe-candidates.json、specialized-candidates.json rejected_leads） |

## 6. 汇总：审批 / 缺独立评测 / 缺数据改进依据

### 6.1 可直接进入审批发布流程的候选

**当前没有。** 21 个登记模型全部为 evaluation_only（或无 usage 字段但 production_ready=false、许可 not_release_approved），无一个具备独立产线评测，且许可侧普遍未获放行（AGPL-3.0 派生权重或未声明许可）。最接近审批门槛的两个：

- **public-pcb-nine-v1**：评测链最完整（2051 图固定工作点 + 逐类指标 + 错误模式分析 + 逐图失败样例），数据 CC-BY-4.0 明确；阻塞项为权重 AGPL-3.0 派生未获批、拆分非同板隔离、CFO/CS 弱类。
- **public-fire-smoke-dfire-yolov8n-v1**：唯一带严格外部 holdout（D-Fire test 200 图）的检测候选；阻塞项为基座 AGPL-3.0 商用评审、40ep 未收敛。

### 6.2 缺独立评测的候选

按登记声明归类：

- 登记明确无准确率评测：public-paine-pin-segmentation（"No independent accuracy evaluation"）、public-welding-defect-yolov8（not_evaluated）、pcb-lead-solderinsufficient（仅静态加载）、public-pcb-yolo26（DeepPCB 样本 0 检出）、public-wetfloor-convnext-q8（accuracy=null）、public-warehouse / public-fire（single_image_smoke_only）、public-fire-smoke-longlivewama-v1 / zyazyz008-v1（10 图试推）、public-kairos-water（单公开图）、public-ppe-construction-v03（accuracy=null）、public-gauge-pose-e120（关键点非准确率证据）。
- 评测存在但非独立（作者/发布者数据或选模验证集）：public-pcb-nine-v1（作者留出、非同板隔离）、public-glue-yolov4-tiny（作者 holdout 19 图）、experimental-visible-wrist-v1/v2（作者同会话 test + 49 对近重复）、coating-pinhole-enhanced（登记无数字；文档侧 21 图且 R7 为选模验证集）、ev-battery-weld-cnn-v1/v2（官方 split 内抽样、工件分组未知）、experimental-industrial-action-v1（官方 split 但仅 10 条录像 16 test 段）、public-fire-smoke-dfire-yolov8n-v1（D-Fire holdout 为外部数据集但非目标产线）。

### 6.3 缺数据改进依据的候选（结合 PCB-ERROR-ANALYSIS-2026-11-05.md 结论）

- **public-pcb-nine-v1**：改进依据**已具备**——错误分析给出可执行结论：CFO 训练框 1242（第二少）、真值被 MB/BMFO/OP 跨类强盖 66 对、救回上界召回 0.6596，应补 CFO 硬样本而非调阈值；CS 走框回归/去重（66 重复型 fp + 72 定位偏差型、IoU 中位 0.22），不应加类间样本；SH 训练框 675 全类最少，可与 CFO 采集合并；下次评测应导出 conf≥0.01 以区分"没检到"与"低于阈值"。
- **experimental-visible-wrist-v1/v2**：ring 召回 0，登记与文档一致指向数据不足（公开项目仅取得 141→177 图、19 张下载失败），缺远景/遮挡与独立现场样本——改进方向明确但数据未到位。
- **coating-pinhole-enhanced**：登记无评测、无错误分析 → **无改进依据**；且数据侧 1646 图无标注、unclassified 仅 78 图（quality-candidates.json），训练前需先做分组复核与标注补齐。
- **ev-battery-weld-cnn-v1/v2**：KO 误放 ~9.4–10.9%，且登记声明 "resize does not prove removal of acquisition shortcuts"、工件分组未知——缺捷径排除与工件级分组依据，扩量（v2）未能消除误放。
- **public-glue-yolov4-tiny**：断胶 FN 18/40（作者 19 图 holdout 规模过小），缺产线断胶样例；数据许可未解决也阻塞再训练取数。
- **experimental-industrial-action-v1**：45/18/16 段规模极小，tighten_nut/fit_nut 混淆；val/test 视频尚有 5 条未下载完成（HANDOFF.md），数据未齐前不宜再训。
- **public-ppe-construction-v03**：手套/护目镜在发布者拼图上零检出，缺小目标真值与逐人误配率评测（MODEL-GAPS-SAFETY-SOP.md 验收计划）。

### 6.4 覆盖/未覆盖类别总口径

- 极片针孔：覆盖 pinhole 单类；Surface_Crack/Delamination 未训（#3）。
- 焊缝：EV 焊缝 OK/KO 分类（#1/#2）与外观 3 类检测（#16）并存；密封钉、内部缺陷、六面均未覆盖。
- 胶路：断胶/过量胶两类（#6）；胶宽、体积、UV 覆盖率、三防漆未覆盖。
- PPE：warehouse20（#11）+ PPE14（#8）+ 腕带（#9/#10）三档；无 ESD 电气合规、无正向 mask、无耳塞/安全带/防护服。
- 仓储：warehouse20 类（#11）；登记未声明未覆盖类别。
- PCB：六类（#4）与九类（#5）并存；针脚分割（#7）仅掩膜。
- 相邻域（非质量但同批登记）：烟火 4 候选（#12–#15）、积水 2 候选（#18/#21）、表盘（#19）、动作头（#20）。
- 完全无模型的场景：密封钉、电芯六面、Pack 装配、滴漏、洁净穿戴、ESD 专用、弯针/压接深度、三防漆/胶体积（MODEL-REMAINING-2026-11-04.md 表格）。
