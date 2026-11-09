# 专用模型公开资源检索（2026-11-05）

> **2026-11-09 更新：文中"CoatingVision 分割路线待评估""训练方向待定"已过期——框/分割两条路线均已训练并登记为 rejected。最新状态见 [STATUS-2026-11-09](STATUS-2026-11-09.md)。**

本轮为纯检索任务：WebSearch 检索 + 官方页面/API 逐个核实。**未下载任何 >50MB 文件，未训练，未新增本地权重**。许可结论均以"实际打开许可页或官方 API 响应"为准，未打开的不标可用。证据文件保存在 `.v0/model-retrieval-20261105/`（Zenodo/Mendeley API 原始响应 JSON）。

先读避免重复的文档：BATTERY-MODEL-FINAL-AUDIT、MODEL-GAPS-QUALITY、MODEL-GAPS-SAFETY-SOP、SAFETY-MODEL-AUDIT-2026-11-04、PUBLIC-MODEL-NEXT。本轮**未重复请求**：LGASS/OPT-SND（401）、cleanroom Roboflow v4 导出（401）、ESD_NOT_WEARING（403）、LIBAD（gated 401）、M-BSD（需协议）、DAEF（百度盘）、SH17（CC BY-NC-SA）、Thalos（类别不符）。

## 结论速览

| 缺口 | 结论 | 最佳可用资源 |
|---|---|---|
| 1 密封钉焊接质检 | **未找到直接匹配** | GDXray Welds（域相近，禁商用） |
| 2 电芯六面外观 | **未找到面别/工件分组结构** | SPD-Vision-24（部分匹配，许可未确认）；LB-SD（需邮件申请） |
| 3 Pack 装配外观 | **Pack 专属未找到；紧固件任务找到 2 个部分匹配** | Fraunhofer 螺钉缺失（CC BY 4.0）；SDNET 2025 螺栓（CC BY 4.0） |
| 4 极片缺陷多类 | **找到 1 个可立即获取（部分匹配）** | CoatingVision（CC BY 4.0，Figshare 直链） |
| 5 洁净穿戴 | **找到 1 个可立即获取（部分匹配）+ 1 个许可未确认** | Kitchen Hygiene Gear（CC BY 4.0，Zenodo）；CPPE-5（许可未确认） |
| 6 洁净行为/滴漏时序视频 | **未找到直接匹配** | GOD-Video（域相近，CC BY-NC-SA，需申请） |
| 7 地面积水/负样本 | **找到更完整积水集（部分匹配）；干净地面负样本未找到** | Stagnant Water & Wet Surface（CC BY 4.0，Mendeley） |

---

## 缺口 1：电池密封钉焊接质检（RGB/X-ray）

**结论：未找到。** 公开检索范围内没有密封钉（sealing nail / sealing plug）真实缺陷标注的 RGB 或 X-ray 数据集或可微调权重。LGASS/OPT-SND（3D 点云、401/研究协议）沿用原审计，本轮未重复请求。

检索关键词：`battery sealing nail laser welding defect dataset X-ray labeled`、`"密封钉" 焊接缺陷 数据集`、`lithium battery X-ray inspection dataset mendeley zenodo`、`battery cap weld defect kaggle roboflow`。

登记候选：

| 项 | 内容 |
|---|---|
| BPLWD（电池极片激光焊缺陷数据集） | 论文 [MDPI Appl. Sci. 13(4):2402](https://doi.org/10.3390/app13042402)，深圳大学。8,439 张 CMOS RGB 图（2448×2048），2 类真实缺陷标注：焊孔 Welding hole 5,231、凸起 Bulge 3,208，PASCAL VOC 格式。**任务对象是极片焊接，不是密封钉**；数据可用性声明原文："Data is unavailable due to privacy or ethical restrictions."（本轮以 web_reader 打开全文核实）。**不可获取。** |
| GDXray Welds | 机构：PUC Chile + BAM（柏林材料研究所）。[官方仓库](https://github.com/computervision-xray-testing/GDXray)（本轮打开 README 核实）：Welds 组 209MB X-ray 焊缝图，来自 BAM 合作。许可：数据条款原文 "can be used free of charge for research and educational purposes only. Redistribution and commercial use are prohibited"，要求引用 Mery et al. 2015；仓库代码标 GPL-3.0。**域相近**（钢焊缝 X-ray，非电池）；超出 50MB 下载限，未下载。 |
| Few-shot sealing nail（ACM MM 2023） | [论文页](https://dl.acm.org/doi/10.1145/3603781.3604228)提出密封钉缺陷少样本分割+Copy-Paste 扩充；本轮未找到其数据公开托管页，**许可未确认、未验证**，仅作线索。 |
| 中文平台线索 | CSDN YOLOv5+U-Net 密封钉分享、专利 CN117007525A/CN115496728A：无公开数据集。综述（《基于机器视觉的锂电池缺陷检测研究进展》表 14）确认 10 个自建电池数据集均不公开。 |

## 缺口 2：电芯/电池六面外观缺陷（工件分组+面别+缺陷标签）

**结论：未找到**满足"工件分组+面别"结构的公开数据。本轮新增 2 个部分匹配线索；DAEF/M-BSD/LIBAD 等沿用原审计不重复。

| 项 | 内容 |
|---|---|
| SPD-Vision-24 | [OpenDataLab 页面](https://opendatalab.com/XiaoGaoMate/SPD-Vision-24)（搜索索引核实）：软包动力电池表面缺陷检测，300 张（裁剪/旋转增强后），任务=缺陷检测。ACM 论文 [10.1145/3788910.3788917](https://doi.org/10.1145/3788910.3788917) 引用并 GAN 扩充。缺陷类别清单与许可未能从页面核实（OpenDataLab 页面 JS 渲染，本轮两次抓取仅得平台标题）。**匹配度：部分匹配**（软包电芯外观，无六面/面别分组）；**许可未确认**；下载需 OpenDataLab 注册。规模小（300 增强图）。 |
| LB-SD | 论文 [Sci Rep 2025 / PMC12630893](https://pmc.ncbi.nlm.nih.gov/articles/PMC12630893)（本轮打开核实）：华南农业大学，宁德时代主工厂产线采集，海康 320 万像素相机。1,272 张（325 组电芯正/负极正反面各一张），6+1 类：Tab damaged、Nonmetal impurity、Metal scrap、Electrode fold、Electrode damaged、Electrode crack/weld crack、Free，像素级标注。**获取方式：需向通讯作者邮件索取**（sundaozong@scau.edu.cn，"available on reasonable request"），非公开下载。数据许可未声明。**匹配度：部分匹配**（有"组+面"采集结构但只有两个面，非六面）。 |

## 缺口 3：电池 Pack 装配外观（连接件/紧固件/线束/盖板 有无错漏朝向）

**结论：Pack 专属公开数据未找到**（busbar/盖板/线束在 Pack 上的装配数据均为企业自建）。找到 2 个"紧固件有无/状态"部分匹配，可作迁移/预训练数据。

| 项 | 内容 |
|---|---|
| Image dataset for AI-based screw detection（Fraunhofer IPA） | [Zenodo 记录 17173803](https://zenodo.org/records/17173803)（本轮 **Zenodo API 核实**：license `cc-by-4.0`，access_right `open`）。Desire4Electronics 项目（Fraunhofer IPA）。821 张吸尘器整机图，标注**螺钉头（Torx/Allen/Phillips）与缺失螺钉孔（holes）**，LabelMe 格式；单文件 `screw_candidate_detection.tar` 2,468,938,912 字节（约 2.47GB，超 50MB 限，未下载）。**匹配度：部分匹配**——"工件上紧固件有无"任务精确匹配，域为电子设备自动拆解（非电池 Pack）；无"朝向"类别。与已审计失败资源不重复。 |
| SDNET 2025 Defective Bolts and Nuts | [Mendeley 10.17632/cxnj6cp4rb.1](https://data.mendeley.com/datasets/cxnj6cp4rb/1)（本轮 **Mendeley 公开 API 核实**：data_licence = CC BY 4.0）。University of North Dakota（Jafari/Dorafshan）。826 张原图（增强至 1,500），4 类：Fixed / Loosened / Missing / Corroded Bolts and Nuts，bbox+多边形标注（CSV+JSON）。钢结构桥梁域。**匹配度：部分匹配**（紧固件缺失/松动状态精确匹配；域非电池）。 |
| Automotive wire harness connector 数据集（Chalmers） | 论文 [arXiv 2309.13746](https://arxiv.org/abs/2309.13746)（本轮打开 HTML 全文核实）：360 图、20 类汽车线束连接器（iPhone 拍摄）。全文无数据可用性声明，无公开仓库；明确写"there is no publicly available benchmark dataset"。**不可获取。** |
| Roboflow wiring-harness-connectors-detection | [页面](https://universe.roboflow.com/rafael-freitas/wiring-harness-connectors-detection)（搜索索引）：1,230 图线束连接器检测，带托管模型。**许可未确认**（未打开页面核实）；导出需 Roboflow 注册/API key（与 cleanroom 401 同一平台机制，未验证）。仅作线索。 |

## 缺口 4：极片缺陷多类（划痕、露箔、褶皱等，除针孔外）

**结论：找到 1 个可立即获取的数据集（部分匹配）**；划痕/露箔/褶皱完整类别集仍未找到。针孔（pinhole）项目已有，CoatingVision 的针孔类重叠但不冲突。

| 项 | 内容 |
|---|---|
| **CoatingVision** | 机构：Vignesh Sampath（作者署名）。[Figshare 29260121](https://figshare.com/articles/dataset/29260121)，DOI 10.6084/m9.figshare.29260121.v1；数据论文 [Sci Data 13:1333 (2026)](https://doi.org/10.1038/s41597-025-06419-1)。**许可：CC BY 4.0（本轮以 Figshare 官方 API 核实，响应 JSON 已存档）**。规模：2,227 张 RGB 480×640（slot-die 涂布，Pixelink 相机）；类别：Surface crack、Delamination（分层裂纹）、Pinhole、Unclassified；标注三套：4 通道 PNG 二值掩码（分割）+ 多标签 CSV（分类）+ YOLO txt bbox（检测）。单文件 `CoatingVision.zip` 94,296,797 字节，computed_md5 `7cbbf14725f9762a64a81a12b858a2b4`，直链无需注册：`https://ndownloader.figshare.com/files/55182476`。**匹配度：部分匹配**——涂布缺陷多类真实标注、标注格式全，但缺划痕/露箔/褶皱类（crack 与"裂纹"近、delamination 与"分层/褶皱起皮"近但不同名同义需人工判别）。超 50MB 下载限，本轮未下载。配套代码库 github.com/vigsam-coder/CoatingVision。与已审计失败资源不重复。 |
| RWTH PEM 电极缺陷 | 论文 [Adv. Intell. Syst. 4:2200142 (2022)](https://doi.org/10.1002/aisy.202200142)，RWTH Aachen PEM。类别 **agglomerate、bubble、foil（露箔）、scratch（划痕）** 四类标注——类别最贴合本缺口。但本轮检索未找到其 Zenodo/Figshare/OSF/GitHub 数据托管；Cambridge Open Engage 预印本页仅论文（CC BY-NC-ND 4.0，许可限预印本）。**数据未公开，不可获取；许可未确认。** |
| MDPI Coatings 14(12):1592 (2024) | 论文称 500 图 5 类（scratch、bubble、metal leakage、particle、decarbonization 各 100）。数据可用性声明本轮未核实到公开托管。**许可未确认，仅作线索。** |
| LB-SD（同缺口 2） | 6 类极耳/涂布缺陷，需邮件索取。部分匹配。 |
| IESDC | IEEE Access 2024 论文与 UNC 学位论文提及的 Lithium Electronic Surface Defect Classification 数据集；IEEE 页面反爬（418），公开独立托管页未找到。**托管与许可未确认，仅作线索。** |

## 缺口 5：洁净服/帽子/鞋套穿戴检测（许可+类别+权重或数据完整可用）

**结论：找到 1 个许可+数据完整、可立即获取的资源（部分匹配）**；鞋套与连体洁净服类别仍缺。cleanroom Roboflow v4（401）与 SH17（NC）不重复请求。

| 项 | 内容 |
|---|---|
| **Kitchen Hygiene Gear（7 类穿戴合规数据集）** | [Zenodo 记录 16329852](https://zenodo.org/records/16329852)（本轮 **Zenodo API 核实**：license `cc-by-4.0`，access_right `open`，title "Kitchen Hygiene Gear"）。论文 [PMC12526732](https://pmc.ncbi.nlm.nih.gov/articles/PMC12526732)（Benchmarking Lightweight YOLO for Hygiene Compliance，2025）合并多个 Roboflow 公开项目重整标注后发布。31,371 张，7 类：`glove / no_glove / hairnet / no_hairnet / mask / no_mask / incorrect_mask`——**洁净帽（发网）穿戴/未穿戴为明确正负类**。单 zip 1,944,884,945 字节（约 1.94GB，超 50MB 限，未下载）。**匹配度：部分匹配**——覆盖"帽子+手套+口罩"穿戴/未穿戴二元合规判断（可用现有 YOLO 训练链路）；**无鞋套、无连体洁净服/大褂**；域为厨房/食品卫生而非洁净室。不发布权重（论文未公开模型权重）。 |
| CPPE-5 | [HF rishitdagli/cppe-5](https://huggingface.co/datasets/rishitdagli/cppe-5)（本轮 **HF API 核实**）：1,029 图（train 1,000/test 29），5 类 Coverall/Face_Shield/Gloves/Goggles/Mask，COCO bbox，~4.6 框/图，真实场景。**许可：HF 卡 `license: unknown`（API 核实）；GitHub 仓库代码 Apache-2.0；arXiv 2112.09569 摘要页未声明数据许可——数据许可未确认，不得标可用**。Coverall（连体防护服）是洁净服大褂的最近类别；无帽子/鞋套。下载 241MB（parquet）。 |
| 其他线索 | Roboflow Universe `class:hairnet` 搜索页（多个社区数据集，需注册导出，许可逐个未核）；CodersArts hairnet/mask 教程集（107 训练图，规模过小）。 |

## 缺口 6：洁净行为 / 滴漏时序视频（方向、停留、滴落泄漏 + 时间标注）

**结论：未找到直接匹配。** 洁净行为（更衣顺序、区域停留、触碰）与水滴滴漏的"视频+时间标注"公开数据集均未找到。

| 项 | 内容 |
|---|---|
| SynSpill（工业泼洒合成数据） | 论文 [arXiv 2508.10171](https://arxiv.org/html/2508.10171v1)（本轮打开全文核实）：2,000 张合成工业泼洒场景（8 类，COCO 格式，1024×1024）。许可 **CC BY-NC 4.0**（补充材料 E）；数据"will be made public upon acceptance"——**当前未公开且许可非商业，不可用**。 |
| GOD-Video（气态目标检测视频） | [GitHub CalayZhou/Gaseous-Object-Detection](https://github.com/CalayZhou/Gaseous-Object-Detection)（本轮 **raw README 核实**）：T-PAMI 2024，600 段红外视频 / 141,017 帧，多种气体泄漏，视频级检测基准；**代码 MIT，数据集 CC BY-NC-SA 4.0**（README LICENSE 段原文核实）；数据需经作者数据页（calayzhou.github.io）**申请下载链接**。**匹配度：仅域相近**（红外气体泄漏视频，非水滴滴漏，模态不同）。 |
| CCTV-Pipe / QV-Pipe | [官方页](https://videopipe.github.io/cctvpipe/index.html)（本轮打开核实）：575 段真实管道 CCTV 视频、87 小时、16 类缺陷，含时序定位任务（single-frame 标注而非完整时间边界）。获取需在 CodaLab 竞赛签署学术版权表。**仅域相近**（管道内壁缺陷，非滴漏）。 |
| 洁净行为 | donning/doffing 论文 [arXiv 2407.17471](https://arxiv.org/html/2407.17471v1)（本轮打开全文核实）：仅用 CPPE-5 图像检测+排序算法判穿戴顺序，验证视频来自 YouTube，**未发布行为时序标注数据**。结论：更衣顺序/停留/触碰类公开时序数据未找到，维持原审计"需自建"结论。 |
| 其他 | IIG 数据集"on request"；GasVid 标注 not available（[awesome-ogi](https://github.com/ProAlize/awesome-ogi) 清单核实）。Roboflow 水漏图像集均为帧级无时序。 |

## 缺口 7：地面积水/污渍负样本（明确负样本或更完整积水集）

**结论：找到 1 个更完整的积水标注数据集（部分匹配）；"干净地面"明确负样本集未找到。** 已有 Kairos RT-DETR（单类 water，Apache-2.0）与 wetfloor 分类器沿用，不重复。

| 项 | 内容 |
|---|---|
| Dataset of Stagnant Water and Wet Surface with Annotations | [Mendeley y6zyrnxbfm v4](https://data.mendeley.com/datasets/y6zyrnxbfm/4)（本轮 **Mendeley 公开 API 核实**：data_licence = CC BY 4.0）。Bhutad et al.，配套 Data in Brief 论文 [S2352340921010271](https://www.sciencedirect.com/science/article/pii/S2352340921010271)。1,976 张 RGB 256×256，2 类 `water / wet surface`，YOLO 格式标注，手机拍摄，顶视+侧视、多种光照。**匹配度：部分匹配**——比现有单类 water 更完整（含 wet surface 湿面类，可提高水渍/反光难例区分）；户外积水域，非洁净室地面；**无"干净地面"负类**（所有图含水或湿面）。Mendeley 文件 API 返回 400，单文件大小未知，经页面"Download all"免登录下载。 |
| Water-Filled and Dry Potholes | [Mendeley tp95cdvgm8](https://data.mendeley.com/datasets/tp95cdvgm8)：路面坑洞有水/无水两态，VOC XML+YOLO TXT——"无水"类可作为负样本线索。**许可未确认（本轮未打开其许可页），仅作线索。** |
| 干净地面负样本 | **未找到**专门公开的"无水渍干净地面"负样本数据集。检索词 `puddle detection dataset`、`wet floor segmentation`、`clean floor dataset` 均只返回正样本积水/湿面或分类集。可行替代：从项目现有室内巡检相机取无水帧作负样本，并用已登记 wetfloor 分类器预筛，避免把阴影/反光误当负样本。 |

---

## 可立即获取（公开直链、许可已核实、无需注册）

以下四项许可均经官方 API/页面核实，**但单文件均超 50MB 下载限，本轮均未下载**；需要时按命令获取并做 MD5/内容校验后再登记：

```bash
# 1) CoatingVision 极片涂布缺陷（CC BY 4.0，94,296,797 B，md5 7cbbf14725f9762a64a81a12b858a2b4）
curl -L -o CoatingVision.zip "https://ndownloader.figshare.com/files/55182476"

# 2) Kitchen Hygiene Gear 洁净帽/手套/口罩穿戴合规 7 类（CC BY 4.0，1.94GB）
curl -L -o "kitchen-hygiene-gear.zip" "https://zenodo.org/records/16329852/files/kitchen%20hygiene%20gear.zip?download=1"

# 3) Fraunhofer 螺钉头+缺失孔（CC BY 4.0，2.47GB tar）
curl -L -o screw_candidate_detection.tar "https://zenodo.org/records/17173803/files/screw_candidate_detection.tar?download=1"

# 4) SDNET 2025 螺栓四态（CC BY 4.0）与 Stagnant Water 积水/湿面（CC BY 4.0）
#    Mendeley 页面 Download all（免登录）：文件清单 API 本轮返回 400，大小未知，下载后需自行校验
#    https://data.mendeley.com/datasets/cxnj6cp4rb/1
#    https://data.mendeley.com/datasets/y6zyrnxbfm/4
```

## 仍缺清单（按缺口）

1. **密封钉**：RGB/X-ray 密封钉焊缺陷真实标注——未找到；X-ray 通用焊缝（GDXray，禁商用）仅能作方法学迁移参考。
2. **六面结构**：带工件分组+六面别标签的数据——未找到；最接近的 LB-SD 只有正/负极两面且需邮件申请。
3. **Pack 专属**：连接件/线束/盖板错漏朝向在电池 Pack 上的数据——未找到；可用 Fraunhofer 螺钉缺失 + SDNET 螺栓状态作紧固件子任务迁移。
4. **极片划痕/露箔/褶皱完整多类**：CoatingVision 缺这三类；RWTH 四类数据未公开；仍缺。
5. **鞋套、连体洁净服穿戴类**：Kitchen Hygiene Gear 无这两类；CPPE-5 有 Coverall 但许可未确认；仍缺。
6. **滴漏时序视频+时间标注、洁净行为序列**：完全未找到；GOD-Video（气体、红外、NC 许可）仅域相近。
7. **干净地面明确负样本集**：未找到；建议自采+分类器预筛。

## 诚实条款

- 本轮所有"许可"结论均来自实际打开的页面/API 响应（存档于 `.v0/model-retrieval-20261105/`：`zenodo_ppe.json`、`zenodo_screw.json`、`mendeley_bolts.json`、`mendeley_water.json`）；未打开许可页的资源一律标"许可未确认"。
- 未下载任何超 50MB 文件，未取得任何新权重；未重复请求已审计失败资源。
- "未找到"仅指本轮检索范围（WebSearch + 指定平台/仓库核实），不等于互联网上不存在。
- OpenDataLab、Roboflow Universe、MDPI 部分页面 JS 渲染或反爬（403/418），相应字段以可核实的 API/索引/全文为准并已注明。

## 落地状态更新（2026-11-05 补记）

**CoatingVision 已下载并校验**：`.v0/coatingvision/CoatingVision.zip` 94,296,797 字节，官方 MD5 `7cbbf14725f9762a64a81a12b858a2b4` 匹配，ZIP CRC 全部通过，9,498 条目 / 8,908 图。

实测内容与官方声明一致：
- classification：2,227 图多标签 CSV（Surface_Crack 1947 / Delamination 203 / Pinhole 519 / unclassified 78）
- detection：仅 581 个标注文件、649 框（类 0: 564、类 1: 85），2,164 张图无框——检测子集偏小，类别名未随包声明，用途待确认
- segmentation：PNG 二值掩码配套

结论：数据资产登记价值成立（CC BY 4.0 已核实）；极片"裂纹/分层"两类首次有真实样本，分层仅 203 图需谨慎评估；训练方向（多标签分类 vs 扩充检测）待定，未启动训练，不重复已有针孔能力。

## CoatingVision 三类检测训练完成（2026-11-05 续记）

**结果：不达标，登记 `experimental-coating-three-class-v1`（rejected_for_deployment）**。固定 444 图 test（conf 0.25/IoU 0.5）：整体 P 0.732 / R 0.512 / F1 0.603；逐类 crack P 0.734/R 0.518、pinhole P 0.701/R 0.500、**delamination P 0.211/R 0.031（基本失败）**；误报帧 412/444。

失败分析（真实数据依据，已入登记）：
1. 分层仅 203 图/714 框——框检测样本量不足。
2. crack 是细长曲线，掩码连通域切段产生 53,907 碎片框，框 IoU 匹配天然吃亏。
3. **正确路线是分割训练**（掩码即监督），框检测路线对此数据证伪。
4. 针孔已另有专用候选（coating-pinhole-enhanced，选模集 R 0.810）。

后续：不重复框路线；分层类等更多样本；分割训练路线待资源窗口评估。

## Kitchen Hygiene Gear 落地（2026-11-05 续记）

已下载校验：`.v0/kitchen-hygiene/kitchen-hygiene-gear.zip` 1,944,884,945 字节，ZIP CRC 全过，62,742 文件 / 31,371 图（Roboflow v5 导出，YOLOv8 格式，640×640，CC BY 4.0 声明于 data.yaml）。

类别与规模：7 类正负对（glove/no_glove、hairnet/no_hairnet、mask/no_mask、incorrect_mask），88,788 框、每类 11.7k–19.8k 均衡，train/valid/test 官方三分，仅 3 张空标签。

匹配度评估（诚实）：**部分匹配**——覆盖 A3 缺口中的"帽/发网"与"手套"两类正负样本；**域差显著**（厨房后厨场景 vs 工业洁净室），无鞋套/连体服/洁净服；incorrect_mask 与"未按规定穿戴"语义部分对应。作为穿戴检测数据底座可用，作为工业洁净室最终数据不可替代。训练候选排队（seg 训练后 GPU 空闲即启动），域差如实标注不冒充洁净室模型。
