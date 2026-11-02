# 技术调研与升级方案:2025–2026 工业视觉 AI 生态

> 调研日期:2026-11-02。方法:WebSearch/WebFetch 检索论文(arXiv/顶会)与开源仓(GitHub API 核对 stars/license/最近提交),与 [PLATFORM-GAPS](PLATFORM-GAPS.md)、[ROADMAP](ROADMAP.md) 的 V(拍照即检)/S(实时安全)/Q(质量)/B(平台底座)/R(工程化)编号对齐。每个方向分「开源项目表」与「论文/顶会表」两张表。
>
> **诚实声明**:①Stars/许可证/最近提交取自 GitHub API(2026-11-02 查询),此后会漂移;②标"待复核"的条目未逐字核对 LICENSE 全文;③"MMIndustrial"检索中未见同名论文页,但 ICLR 2025 收录的《A Comprehensive Benchmark for Multimodal LLMs in Industrial Anomaly Detection》极可能即其正式出处(间接印证,arXiv id 未核到);④原 DEIM 仓库(ShihuaHuang75/DEIM)API 已 404,疑似迁移/改名,谱系现以 DEIMv2 仓为准;⑤⚠ 标记 = 未完全验证。

---

## 一、SOP/作业合规识别(工序/步骤/错误检测)

### 1.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [z1oong/Building-Egocentric-Procedural-AI-Assistant](https://github.com/z1oong/Building-Egocentric-Procedural-AI-Assistant) | 低 | 待复核 | 综述库,持续更新 | 仅阅读 | egocentric procedural AI(VLM 驱动)论文/数据集雷达 | 方向跟踪 | 当雷达用 |
| [Charmve/Surface-Defect-Detection](https://github.com/Charmve/Surface-Defect-Detection) | 数千 | 待复核 | 持续维护 | 仅阅读 | 中文圈最常引用的工业表面缺陷数据集/论文合集 | 评测、训练腿素材 | 当数据集索引用 |
| [WH-HuanWang/Defect-GLM](https://github.com/WH-HuanWang/Defect-GLM) | 111 | **无 LICENSE(不可复用代码)** | 2024-09 后停更 | 中(依赖旧栈) | 首个开源工业缺陷监测 VLM(晶圆缺陷,ChatGPT 造数据) | 借鉴造数据思路 | 只借鉴,不引代码 |
| [dl-cv/OpenIVS](https://github.com/dl-cv/OpenIVS) | 低 | 待复核 | 一般 | 高(.NET 栈,与我们 Python 不合) | 开源工业视觉软件框架(采集/处理/推理一站式) | 无(架构参考) | 观望 |

### 1.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| Make some Noise: Acoustic Classification of Manual Work Steps(TU Wien,Fink 2024) | [TU Wien PDF](https://repositum.tuwien.at/bitstream/20.500.12708/208831/1/Fink-2024-Make%20some%20Noise%20Acoustic%20Classification%20of%20Manual%20Work%20Steps%20To...-vor.pdf) | 用声音分类人工装配工步(拧/敲/锯有声学签名),mic 级成本无遮挡问题 | 感知层旁路信号(SOP 状态机事件触发,W6 拆封条) | 借鉴思路,1–2 工位 PoC |
| Learning to recognize correctly completed procedure steps in egocentric video(TU/e,Schoonbeek 2025) | [CVIU/ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1077314225002516) | 提出 **PSR 任务**:识别"哪些步骤已被正确完成及顺序",天然对齐 SOP 合规 | S 系列 SOP 状态机学术原型 + 评测指标 | 借鉴任务定义与指标 |
| A Synthetic-Driven Vision System for Assembly Step Recognition(2026) | [arXiv 2607.00129](https://arxiv.org/html/2607.00129v1) | 端到端合成数据驱动装配工步识别,解工厂数据稀缺 | 训练腿冷启动 | 借鉴思路 |
| MonitorVLM(2025-10) | [arXiv 2510.03666](https://arxiv.org/html/2510.03666v1) | VLM 把违规视频自动映射到具体监管/规程条款("用自然语言 SOP 约束视频判定") | V 系列判定 + DSL 条款编译链 + 告警可解释 | 借鉴"条款检索+视频判定"两段式 |
| EgoProceAssist / Building Egocentric Procedural AI Assistant(2025-11) | [arXiv 2511.13261](https://arxiv.org/html/2511.13261v1) | 系统评估 VLM 在步骤级程序性支持上的短板;含在线错误检测线 | V 系列 SOP 判定 | 观望+跟踪 |
| EgoVis Workshop(Ego4D/Ego-Exo4D Keystep Challenge) | **CVPR 2025/2026 Workshop**([egovis](https://egovis.github.io/cvpr26)、[challenge](https://docs.ego-exo4d-data.org/challenge)) | 熟练手工活动(含装配类)第一视角 keystep 识别官方赛道,含 award 榜 | S 系列、评测 | 跟踪榜单方案 |
| IKEA ASM(多视角多模态装配数据集) | [arXiv 2007.00394](https://arxiv.org/html/2007.00394v2) | 装配领域 instructional video 基础数据集(动作/物体/程序三层标注) | 训练腿、评测设计 | 观望 |
| 工程佐证:ADLINK SOP 案例、Roboflow《VLM in Manufacturing》(2026-04) | [adlinktech](https://www.adlinktech.com.cn/en/ai_vision_case_smart_camera_sop_compliance_monitoring)、[roboflow.com](https://roboflow.com/blog/vision-language-models-in-manufacturing) | 行业从固定规则 CV 转向"VLM 读 SOP 文本→对照视频挑违规"两段式 | 架构验证 | 引用佐证 |

---

## 二、工业 VLM/多模态质检与判定基准

### 2.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [MME-Industry](https://huggingface.co/datasets/Ajax102/MME-Industry)(HF 数据集,2025-01) | – | 待复核(HF 卡) | – | pip/HLF datasets 即用 | 工业多模态 MLLM 评测基准(设备/场景/文档子任务) | 评测(V6/B2)公开参考集 | 直接用(参考集) |
| [alibaba-multimodal-industrial-ai / IndustryBench-MIPU](https://github.com/alibaba-multimodal-industrial-ai)(阿里) | 待复核 | 待复核 | 2025 | datasets 加载 | 多图工业产品理解(4,481 产品×18 类,属性抽取/跨图一致性) | V5 首件多图对照失败模式 | 借鉴失败模式分析 |
| [MMIndustrial ⚠(疑即 ICLR 2025 基准)](#) | – | – | – | – | ICLR 2025 收录《A Comprehensive Benchmark for Multimodal LLMs in Industrial Anomaly Detection》,疑即用户所称 MMIndustrial(间接印证,id 未核到) | 评测 | 核到原文后再评估 |

### 2.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| 跨行业多模态评测基准(2025-01) | [arXiv 2501.16688](https://arxiv.org/html/2501.16688v1) | 1,050 张高分行业图 + QA 对,考"看图答业务问题" | 评测出题模板(图+业务问题+判分规则) | 借鉴题目形态 |
| VLM for Automated Quality Control(2026) | [Nature Sci. Reports](https://www.nature.com/articles/s41598-026-55179-4) | 9 个 SOTA VLM × 5 领域系统对比,给出 VLM 质检可靠性边界 | V 系列(判错风险的公开证据与缓解论据) | 引用其结论 |
| ReinAD: Towards Real-world Industrial Anomaly Detection | **NeurIPS 2025**([poster](https://neurips.cc/virtual/2025/poster/121395)) | 真实工业 AD 数据集:对比式异常定义 + 细粒度标注 | 评测、训练腿数据设计 | 跟踪(数据若开放则纳入标尺) |
| ICLR 2025 多模态工业异常检测综合基准 | [ICLR 2025 virtual](https://iclr.cc/virtual/2025/papers.html) | 专测 MLLM 找异常/判异常的基准(疑 MMIndustrial) | V 系列换模型横评 | 核到原文后用 |
| Adapting VLMs for Few-Shot Industrial AD | [MDPI Algorithms](https://www.mdpi.com/1999-4893/19/4/259) | VLM 特征进 few-shot AD(MVTec 验证) | V 系列 | 观望 |
| Enhancing PatchCore with Dynamic Scaling + VLM(工业螺栓头数据集) | [MDPI](https://www.mdpi.com/2076-3417/16/14/7096) | 经典 AD + VLM 混合工作流落地案例 | 感知层混合判定 | 借鉴 |
| MM-BRIGHT(2026-01) | [arXiv 2601.09562](https://arxiv.org/abs/2601.09562) | 首个工业/技术推理多任务多模态基准(2,803 查询×29 技术域) | 评测 | 观望 |

---

## 三、免训练异常检测(PatchCore/EfficientAD 后继)

### 3.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [open-edge-platform/anomalib](https://github.com/open-edge-platform/anomalib)(Intel,2.2.0 2025-10) | **6,357** | **Apache-2.0** | 极活跃(2026-11 仍有提交) | pip 安装;OpenVINO/ONNX 导出;**集成成本低**(predictor API) | 工业 AD 一站式训练/导出/推理(PatchCore/EfficientAD/AnomalyDINO 等) | **训练腿 v1 骨架 + 感知层** | **直接用** |
| [dammsi/AnomalyDINO](https://github.com/dammsi/AnomalyDINO)(WACV 2025) | 272 | **Apache-2.0**(API 核对) | 2025-12 仍有提交 | 低:HF 拉 DINOv2 权重 + anomalib 已集成;TensorRT 导出(batch=1) | PatchCore 范式 + DINOv2 特征,**零训练**;1-shot MVTec ~96.6 vs PatchCore 83.4 AUROC | **V 系列免训练引擎**(W5/W7 类场景) | **直接用** |
| [honda-research-institute/Anomaly-OneVision](https://github.com/honda-research-institute/Anomaly-OneVision)(CVPR 2025) | 待复核 | 待复核 | 2025 | 高(需训 MLLM 头) | 首个零样本 AD+推理解释的 MLLM 专家(异常专家挑可疑 token + Look-Twice) | V 系列两段式判定模板 | 借鉴思路 |
| 雷达清单:[M-3LAB/awesome-industrial-anomaly-detection](https://github.com/m-3lab/awesome-industrial-anomaly-detection)、[hustCYQ/Towards-Practical-IAD](https://github.com/hustCYQ/Towards-Practical-Industrial-Anomaly-Detection)、[mala-lab/Awesome-AD-Foundation-Models](https://github.com/mala-lab/Awesome-Anomaly-Detection-Foundation-Models) | – | – | 持续更新 | 仅阅读 | 2025–2026 IAD 论文/数据集持续收录 | 方向雷达 | 订阅 |

### 3.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| AnomalyDINO | [arXiv 2405.14529](https://arxiv.org/html/2405.14529v3) / **WACV 2025** | 见上表;少样本免训练工业 AD 的当前实用标杆 | V 系列 | 直接用 |
| Anomaly-OV | [arXiv 2502.07601](https://arxiv.org/abs/2502.07601) / **CVPR 2025** | 零样本 AD + MLLM 推理一体化 | V 系列升级模板 | 借鉴 |
| AF-CLIP | [arXiv 2507.19949](https://arxiv.org/abs/2507.19949)(2025-07) | anomaly-aware CLIP 微调做零样本 AD | 雷达 | 观望 |
| DE-CLIP(few-shot,MVTec 96.6/96.7 AUROC)、AnomalyCLIP、SAA+/SAA++(SAM 框架免训练分割异常) | 2024–2025 各会 | CLIP/SAM 系少/零样本 AD 族 | 雷达 | 观望 |
| Robust Distribution Alignment for IAD under Distribution Shift | [arXiv 2503.14910](https://arxiv.org/abs/2503.14910)(2025) | 分布移(换光照/换产线)下的工业 AD 鲁棒对齐——直击我们现场光照痛点 | 评测设计 + 感知层鲁棒性 | 借鉴思路 |
| Source-Free TTA for Online Surface Defect Detection | [arXiv 2408.09494](https://arxiv.org/abs/2408.09494) | 无源测试时自适应,新产线免重训 | 训练腿→部署过渡 | 观望 |
| Test Time Training for Industrial Anomaly Segmentation | **CVPR 2024 Workshop(VAND)**([PDF](https://openaccess.thecvf.com/content/CVPR2024W/VAND/papers/Costanzino_Test_Time_Training_for_Industrial_Anomaly_Segmentation_CVPRW_2024_paper.pdf)) | 测试时训练抗域移;VAND 赛道是该领域顶会窗口 | 感知层 | 观望 |

---

## 四、开放词汇检测/分割

### 4.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [IDEA-Research/GroundingDINO](https://github.com/IDEA-Research/GroundingDINO)(ECCV 2024) | **10,728** | **Apache-2.0** | ⚠ 2024-08 后未更新(IDEA 转做 API) | 中高(编译 CUDA 算子,Windows 更麻烦) | 文本提示开放集检测事实标准 | 标定向导、DSL 感知原语 | 用,但知其停维护;新项目考虑替代 |
| [IDEA-Research/Grounded-SAM-2](https://github.com/IDEA-Research/Grounded-SAM-2) | 数千 | Apache-2.0 系(随父仓;SAM2 权重 Apache-2.0,SAM1 权重自定义) | 2025 活跃 | 中高(多仓依赖,建议封装为独立服务) | 文本检测 → SAM2 精分割/跟踪管线 | **标定向导引擎** | **直接用**(离线服务化) |
| [facebookresearch/sam2](https://github.com/facebookresearch/sam2) | **20,095** | **Apache-2.0**(API 核对) | 2026-05 仍提交 | 低(pip + checkpoint) | 视频流式记忆分割;单帧点击分割最强 | 标定向导 + 训练腿辅助标注 + 证据高亮 | **直接用** |
| [google/owlv2](https://huggingface.co/google/owlv2-large-patch14-ensemble) | – | **Apache-2.0**(HF 权重) | 稳定 | **极低**(transformers 一行加载,CPU 可跑) | CLIP 骨干零样本文本条件检测 | 标定向导 CPU 兜底、辅助标注 | **直接用** |
| [wleilei/OV-DEIM](https://github.com/wleilei/OV-DEIM)(开放词汇 DEIM) | 50 | **Apache-2.0** | 2026-07 活跃 | 低-中 | DEIM 速度 + 开放词汇文本查询 | 检测器接入层(B0)候选 | 观望/小规模试 |
| DINO-X / Grounding DINO 1.5/1.6、T-Rex2(ECCV 2024)API | – | **不开源**,DeepEXT API 商用需协议 | – | – | 统一开放世界理解;T-Rex2 点/框视觉提示 | 标定向导体验上限 | 观望(协议+离线冲突) |
| [luca-medeiros/lang-segment-anything](https://github.com/luca-medeiros/lang-segment-anything) | 数千 | Apache-2.0 系(待复核) | 活跃 | 低(单文件管线,Grounding DINO+SAM2.1 本地) | 文本→框→mask 一行调用 | 标定向导 PoC | 直接用 |

### 4.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| Grounding DINO | **ECCV 2024**([GroundingDINO 仓](https://github.com/IDEA-Research/GroundingDINO)) | DINO + grounded 预训练开放集检测 | 感知层 | 直接用(注意停更) |
| DINO-X: A Unified Vision Model | 2024-11([paper](https://www.researchgate.net/publication/386021701_DINO-X_A_Unified_Vision_Model_for_Open-World_Object_Detection_and_Understanding)) | 对象中心统一开放世界检测+分割+关键点+OCR(仅 API) | 雷达 | 观望 |
| T-Rex2 | **ECCV 2024**([GitHub](https://github.com/IDEA-Research/t-rex)) | 交互式视觉提示(点/框指认目标),工业标定最贴合的交互范式 | 标定向导交互设计 | 借鉴交互范式 |
| SAM 2 | Meta(2024;[GitHub](https://github.com/facebookresearch/sam2)) | 流式视频分割记忆 | 见上表 | 直接用 |
| OWLv2 | Google DeepMind([HF](https://huggingface.co/docs/transformers/en/model_doc/owlv2)) | 缩放 OWL-ViT,自训练器目标性提升零样本检测 | CPU 兜底 | 直接用 |
| NVIDIA TAO + Grounding DINO 部署会话 | [NVIDIA](https://www.nvidia.com/en-us/on-demand/session/other2024-tao55gdino) | 文本检测进生产工作流的 NVIDIA 路径 | 部署参考 | 观望 |

---

## 五、端侧 VLM 蒸馏与小模型

### 5.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [qwenlm/qwen3-vl](https://github.com/qwenlm/qwen3-vl)(阿里) | **20,157** | **Apache-2.0**(API 核对) | 2026-01 仍提交 | 中(GPU);2B/4B 有 RK3576 级边缘指南;Ollama/llama.cpp/vLLM/SGLang 全支持 | 2B–32B 全尺寸,长视频/OCR/文档/Thinking 模式 | **V 系列本地化 + 蒸馏学生模型** | **直接用**(服务器 8B / 端侧 2B–4B) |
| [zai-org/GLM-4.5V](https://huggingface.co/zai-org/GLM-4.5V)(智谱) | HF | **MIT**(已确证;⚠ GLM-5.3 系已改许可,升级前复核) | 2025–2026 | 中(106B-A12B MoE,需大显存 GPU;vLLM/SGLang 官方支持;FP8 变体) | 高吞吐全能 VLM(图推理/视频/文档/GUI grounding) | 教师模型/私有化兜底 | 直接用(仅教师/兜底) |
| [OpenBMB/MiniCPM-V](https://github.com/openbmb/MiniCPM-V)(4.5 8B / 4.7 1B) | 数千 | Apache-2.0 系(HF 卡为准) | 活跃 | 低-中(手机/端级) | 3D-Resampler 高压缩视觉 token + 快/慢混合思考 | 端侧备选 | 观望 |
| InternVL3 系(OpenGVLab) | HF | Apache-2.0(卡标注,待复核) | 活跃 | 中 | 1B–78B 全系开源 VLM | 备选 | 观望 |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 8 万+ | MIT | 极活跃 | 低(预编译/容器);`libmtmd`+mmproj 统一图/音/视频输入 | 端侧 GGUF 推理;Qwen3-VL 可跑 | **工控盒部署(D 系列)** | 直接用(⚠ Qwen3-VL-8B 图像偶发挂起 issue #17012,端侧先 2B/4B) |
| vLLM | 万级 | Apache-2.0 | 极活跃 | 中(GPU 服务化) | PagedAttention 批量生产服务;官方支持 Qwen/GLM 多模态 | 服务器 VLM 服务 | 备选(⚠ 社区实测 Qwen3-VL-8B-FP8 上比 SGLang 慢 ~3x,issue #29869) |
| **SGLang** | 万级 | Apache-2.0 | 极活跃 | 中(GPU 服务化) | RadixAttention + 高效内存调度;多份社区实测(Qwen3-VL/32B-AWQ)显著快于 vLLM | **服务器 VLM 服务首选候选** | **引入并实测**(与 vLLM 同台跑回归) |
| Ollama | 万级 | MIT | 极活跃 | 极低 | 一条命令本地拉起,OpenAI 兼容 | POC/演示、Frigate 式富化 | 直接用 |

### 5.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| Qwen3-VL Technical Report | [arXiv 2511.21631](https://arxiv.org/abs/2511.21631) | 全尺寸开源多模态技术报告(训练/数据/长视频) | 选型依据 | 参考 |
| MiniCPM-V 4.5: Cooking Efficient MLLMs | [arXiv 2509.18154](https://arxiv.org/html/2509.18154v1)(CVPR 2026) | 3D-Resampler + 混合思考,8B 打 72B 级视觉任务 | 端侧蒸馏参考 | 借鉴蒸馏/压缩思路 |
| GLM-4.5V 模型卡/技术报告 | [HF zai-org/GLM-4.5V](https://huggingface.co/zai-org/GLM-4.5V) | MoE VLM 全能型,MIT 许可 | 教师 | 参考 |
| ICLR 2026 工业异常问答/检测类论文 | [ICLR 2026 virtual](https://iclr.cc/virtual/2026/papers.html) | 工业异常 QA、检测基准进入主会 | 评测雷达 | 跟踪 |

---

## 六、数据引擎/自动标注

### 6.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [cvhub520/x-anylabeling](https://github.com/cvhub520/x-anylabeling) | **10,871** | **GPL-3.0**(API 核对;只作独立桌面工具不链平台代码即可规避传染) | 活跃(2026-11 仍提交) | 零服务端(exe/桌面包) | 桌面端 SAM/SAM2/YOLO 离线预标注,文本/图像/视频/点云 | 数据闭环(R6)修框工具 | **直接用**(独立工具) |
| [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | **17,051** | **MIT**(API 核对) | 极活跃 | 中(docker compose 自托管);REST API 集成容易 | 视觉专用协作标注 + Nuclio serverless 挂自动标注 + 标注质检工作流 | 数据闭环规模化 + 标注质检 | 阶段 2 自托管 |
| Label Studio | 万级 | **Apache-2.0** | 活跃 | 中(pip/docker);ML backend 协议可接自家模型 | ML backend + active learning 工作流;多模态 | 告警复核协议参考 | 借鉴 ML backend 协议,自建 UI |
| [luca-medeiros/lang-segment-anything](https://github.com/luca-medeiros/lang-segment-anything) | 数千 | Apache-2.0 系(待复核) | 活跃 | 低 | 文本→框→mask 预标注管线 | 标定向导/预标注 | 直接用 |

### 6.2 论文/顶会表

| 论文/资源 | 来源 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| 主流标注工具对比(CVAT vs Label Studio 官方;Kili 2026 自托管指南) | [cvat.ai](https://www.cvat.ai/resources/blog/cvat-or-label-studio-which-one-to-choose)、[kili-technology](https://kili-technology.com/blog/best-computer-vision-annotation-tools-for-on-premise-labeling-2026-guide) | 视觉专精 vs 多模态灵活的选型依据;私有化部署维度对比 | 数据闭环选型 | 参考 |
| Label Studio 自动标注三式(ML backend/预标注/active learning) | [labelstud.io](https://labelstud.io/blog/3-ways-to-automate-your-labeling-with-label-studio) | 误报复核→回流训练集的工作流原型 | V7 告警闭环 | 借鉴工作流 |
| SAM 系辅助标注实践(Grounded-SAM2、AnyLabeling 文档) | [IDEA-Research](https://github.com/IDEA-Research/Grounded-SAM-2)、[anylabeling docs](https://anylabeling.nrl.ai/docs/sam) | SAM/SAM2/SAM2.1 全家交互分割 | 预标注 | 直接用 |

---

## 七、视频分析编排/边缘推理框架

### 7.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/FastAPI 集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [blakeblackshear/frigate](https://github.com/blakeblackshear/frigate)(0.16 2025-08 / 0.17 beta) | **37,153** | **MIT**(API 核对) | 极活跃(2026-11 仍提交) | Docker Compose;作为**独立 NVR** 不集成进平台,抄设计 | 本地人脸识别/LPR;**GenAI 机制**:事件缩略图发本地 VLM(Ollama/OpenAI 兼容)答"为什么发生",检测仍走本地小模型 | **V7 告警闭环产品形态模板**(事件 schema、去抖参数、富化链路) | **借鉴思路**(不引入依赖) |
| [NVIDIA-AI-Blueprints/video-search-and-summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization)(VSS) | – | NVIDIA 专有(需 NIM/API) | 2025 | 高(绑定 NVIDIA 栈) | VLM 视频智能体:直播/录像检索-摘要-推理 | 架构参考、W 系录像检索灵感 | 观望 |
| Jetson Platform Services(原 Metropolis Microservices,并入 JetPack 6) | – | NVIDIA 专有 | 2024–2025 | 高 | 边缘视觉 AI 微服务底座(VST 存流/多相机) | D 系列 Jetson 部署参考 | 观望 |
| DeepStream | – | NVIDIA 专有(免费运行时) | 持续 | 高 | GStreamer 流式 AI 视频管线 | 仅当客户指定 NVIDIA 栈 | 观望 |

### 7.2 论文/顶会表

| 论文 | 来源 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| NVIDIA VITA(CES 2025) | NVIDIA Research | Jetson AGX Orin 上实时流视频多模态 LLM 交互感知 | 雷达 | 观望(未见稳定开源) |
| Cosmos Reason(NVIDIA VLM) | NVIDIA 2025 | 物理/空间常识 VLM,面向视频智能体 | 雷达 | 观望 |

---

## 八、检测器新 SOTA 与许可

### 8.1 开源项目表(核心产出:许可与速度对比)

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/集成成本 | 亮点 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| [Peterande/D-FINE](https://github.com/Peterande/D-FINE) | **3,385** | **Apache-2.0**(API 核对) | 2026-10 仍提交 | 中-低(HF 权重、ONNX 导出、config yaml) | DETR 回归→细粒度分布细化(FDR)+GO-LSD;D-FINE-L ~54 mAP@~8ms(T4);**ICLR 2025 Spotlight** | **训练腿默认检测器** | **直接用** |
| [Intellindust-AI-Lab/DEIMv2](https://github.com/Intellindust-AI-Lab/DEIMv2) | **2,086** | ⚠ **NOASSERTION**(SPDX 非标准,**须人工读 LICENSE 全文**) | 活跃(2026-10) | 中 | **DINOv3 backbone** 嫁接 DEIM,8 尺寸,至 57.8 AP@~50M | 训练腿升级候选 | 观察 + 核 LICENSE 后再定 |
| ⚠ 原 DEIM 仓(ShihuaHuang75/DEIM) | – | 曾 Apache-2.0 | **API 404(疑似迁移/改名)** | – | Dense O2O 匹配 + MAL,CVPR 2025 | 谱系并入 DEIMv2 | 经 DEIMv2 获取 |
| [roboflow/rf-detr](https://github.com/roboflow/rf-detr) | **9,950** | **Apache-2.0**(API 核对) | 极活跃(2026-11 当天仍提交) | **低**(微调 API 简洁,文档好) | COCO SOTA 实时检测+分割,微调友好;**ICLR 2026** | 训练腿检测器(与 D-FINE 双选) | **直接用** |
| RTMDet(mmdetection/OpenMMLab) | mmdet 万级 | **Apache-2.0**(随 mmdet) | 稳定 | 中(依赖 mmdet 栈较重) | 大核卷积实时检测;RTMDet-Ins 实例分割/小目标变体强 | 小目标/分割场景备选 | 备选 |
| YOLOv11/YOLOv12(Ultralytics) | 万级 | **AGPL-3.0 + 商业授权** | 极活跃 | 低 | 工程生态最成熟 | ⚠ **默认避开**(AGPL 与私有化交付冲突,ADR-03 许可红线);仅客户已有授权时作开关 | 避开 |

### 8.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 建议 |
|---|---|---|---|
| D-FINE | **ICLR 2025 Spotlight**([arXiv 2410.13898 系](https://github.com/Peterande/D-FINE)见仓) | 回归分布细化 | 直接用 |
| DEIM | CVPR 2025(谱系经 DEIMv2) | 训练法改进 | 直接用(经 DEIMv2) |
| DEIMv2: Real-Time Object Detection Meets DINOv3 | 2025-09([GitHub](https://github.com/Intellindust-AI-Lab/DEIMv2)) | DINOv3 特征进实时检测 | 核许可后跟进 |
| RF-DETR | **ICLR 2026**([GitHub](https://github.com/roboflow/rf-detr)) | 实时检测+分割、微调友好 | 直接用 |

---

## 九、视觉智能体/可视化编程

### 9.1 开源项目表

| 项目 | Stars | 许可证 | 活跃度 | 部署难度/集成成本 | 核心创新 | 落进模块 | 建议 |
|---|---|---|---|---|---|---|---|
| Roboflow Workflows ⚠ | – | 核心引擎可自托管(许可以仓库为准,待复核) | 商业活跃 | 高(生态绑定) | 商业化最成熟的块图式视觉编排(检测/VLM/逻辑/告警块) | 控制台"规则画布"块类型设计 | 借鉴块类型 |
| [smallfryy/corl-2025-papers](https://github.com/smallfryy/corl-2025-papers) | 低 | – | 2025 | 仅阅读 | CoRL 2025 200+ 论文 TLDR+代码索引 | 方向雷达 | 订阅 |

### 9.2 论文/顶会表

| 论文 | 来源/会议 | 一句话创新 | 落进模块 | 建议 |
|---|---|---|---|---|
| VipAct: Visual-Perception Enhancement via Specialized VLM Agent Collaboration and Tool-Use | [arXiv 2410.16400](https://arxiv.org/abs/2410.16400) / **AAAI 2025**(Adobe) | 编排器 + 专职智能体 + 工具调用视觉专家模型,协同规划/推理 | **DSL 远期形态**:感知条件注册为 VLM 工具,自然语言→DSL 草案 | 借鉴工具化接口(代码可用性未确证,不引框架) |
| VLAgent | [arXiv 2506.07778](https://arxiv.org/html/2506.07778v2) | 组合视觉-文本推理智能体系统 | 雷达 | 观望 |
| VisProg/ViperGPT(2023 基础线) | CVPR 2023 最佳论文线等 | NLP→程序化视觉推理范式源头 | "自然语言→DSL"编译器学理依据 | 观望 |

---

## 十、升级方案(按三腿架构与缺口分阶段)

### 10.1 总体判断

1. **生态趋势与我们的架构判断一致**:2025–2026 主流 = "基础模型特征(DINOv2/CLIP)+ 免训练轻适配 + VLM 富化解释 + 小模型蒸馏落地",正是"实时规则 + 拍照即检 + 简化训练"三腿的分布式版本。方向不改,缺的是把公开件接进来。
2. **许可主线(ADR-03 台账即建)**:Apache-2.0/MIT 可覆盖全部新选型(D-FINE 3.4k★、RF-DETR 10k★、anomalib 6.4k★、SAM2 20k★、Qwen3-VL 20k★、CVAT 17k★、OWLv2、GLM-4.5V、Frigate 37k★);必须避开/谨慎的:Ultralytics AGPL 系(默认关)、DINO-X/GD1.5 API(商用协议)、X-AnyLabeling GPL-3.0(只作独立工具)、DEIMv2 license NOASSERTION(先读全文)、Defect-GLM 无许可(只借鉴)。
3. **最优先三件**:评测回归(B2/V6)是所有选型的前提;AnomalyDINO/anomalib 是拍照即检腿当天上线的第二引擎;X-AnyLabeling/CVAT + Grounded-SAM2 是训练腿与标定向导的共用底座。

### 10.2 阶段 1(0–3 个月):评测回归 + 免训练异常检测 + 标注工具起步

| 动作 | 选型 | 落进 | 理由 | 风险与对策 |
|---|---|---|---|---|
| 固定回归集 + 一键跑分 | MVTec AD 2([arXiv 2503.21622](https://arxiv.org/abs/2503.21622),公开标尺)+ 自建 30–50 图业务集;沿用 tools/eval_* | 评测回归(V6/B2) | 无回归,后续换模型/改提示词全是盲改 | MVTec AD 2 学术用途限制;自建集须含现场光照变化 |
| 拍照即检第二引擎 | **AnomalyDINO(经 anomalib)**,导出 OpenVINO/ONNX | 感知层 + V 系列 | 零训练、1–8 张 OK 图可用;与 VLM 双通道互证(分歧→UNCERTAIN 转人工) | AD 强于外观缺陷、弱于逻辑错误(漏装/错装归 VLM) |
| 告警富化 | 抄 Frigate GenAI 机制:事件缩略图 → glm-5.3-flash 生成"发生了什么/依据什么" | 告警闭环(V7 前置) | provider 层现成,纯增量 | 富化不进判定链(可解释性≠判定义务) |
| 标注起步 | **X-AnyLabeling**(桌面、SAM2 预标注)+ 样本池目录规范 | 数据闭环(R6) | 零服务端运维,误报当周可修框 | GPL-3.0:独立工具,不 import 进平台代码 |
| 本地 VLM 首测 | Qwen3-VL-8B(SGLang 与 vLLM 同台)+ glm-5.3-flash,在自建回归集上对比 | 评测回归 | 社区实测 Qwen3-VL-8B-FP8 上 SGLang 快 ~3x(vLLM issue #29869),须自测定夺 | 两个运行时都吃 OpenAI 兼容 API,provider 层零改动切换 |

### 10.3 阶段 2(3–6 个月):检测器接入 + 标定向导 + 告警闭环成型

| 动作 | 选型 | 落进 | 理由 | 风险与对策 |
|---|---|---|---|---|
| 通用检测器接入层 | **D-FINE(ICLR 2025 Spotlight)与 RF-DETR(ICLR 2026)双选**,均 Apache-2.0;导出 ONNX/TensorRT;Ultralytics 仅客户授权时开开关;小目标/分割补 RTMDet | 检测器接入层(B0/R11) | 许可干净 + SOTA + 微调生态;RF-DETR 微调 API 最友好 | 默认权重 COCO 类,工业类需训练腿微调;GroundingDINO 仓已停更(2024-08),不作为长期底座 |
| 标定向导 | **lang-segment-anything / Grounded-SAM-2**(文本圈选)+ OWLv2(CPU 兜底)+ SAM2 分割转 zone 多边形 | 标定向导(V9/B 系列) | "描述即圈选"比手画快一个量级;双引擎覆盖有/无 GPU | Grounding DINO CPU 慢;文本歧义须人工确认步骤兜底 |
| 告警闭环 | 自建状态机(待处理→处理中→已处理/误报)+ 飞书推送 + 误报复核页(Label Studio ML backend 协议思想,自建 UI) | 告警闭环(V7/B3) | 契合无构建前端;复核结果直回流样本池 | 依赖登录认证先行(既有排期) |
| 训练腿 v1 | anomalib(PatchCore/EfficientAD/AnomalyDINO)+ D-FINE/RF-DETR 微调;统一 ONNX/OpenVINO 导出 → 模型上传接口(格式/签名/回归通过才可发布) | 训练腿 + 模型上传(R 系列) | anomalib 全链路现成,工作量在 UI 与上传校验 | 模型包签名与回滚 v1 必做;台账同步登记(ADR-03) |
| 私有化 VLM 服务 | GLM-4.5V(MIT)教师/兜底;Qwen3-VL-8B 默认本地判官;运行时 SGLang 优先实测 | V 系列 + 部署 | MIT/Apache 干净;与 glm-5.3-flash 同族平滑 | 需 GPU 服务器;⚠ 任何 GLM 升级先复核模型卡许可 |

### 10.4 阶段 3(6–12 个月):蒸馏升级 + 边缘部署 + SOP 步骤识别试水

| 动作 | 选型 | 落进 | 理由 | 风险与对策 |
|---|---|---|---|---|
| 蒸馏升级管线 | 教师(GLM-4.5V/glm-5.3-flash)对样本池伪标注 → 人工复核 → 蒸馏/微调 **Qwen3-VL-2B/4B** → llama.cpp GGUF 上工控盒;MiniCPM-V 1B 备选学生 | 训练腿 → 部署 | "云端判 → 端侧跑"是拍照即检规模化降本唯一路径;Qwen3-VL 小模型运行时生态最全 | llama.cpp 视觉量化稳定性 issue 仍在修;每个蒸馏版必须过回归集(评测回归是闸门) |
| 边缘编排 | 参考 Jetson Platform Services 架构,自研 FastAPI + llama.cpp/SGLang 容器化;CPU 站点用 OpenVINO(anomalib/OWLv2) | 一键部署(R11/阶段6) | 避 NVIDIA 专有锁定,保"离线包 + Docker"路线 | 站点分层:无 GPU 只跑规则+AD,有 GPU 加小 VLM |
| SOP 步骤识别试水 | PSR 思路(TU/e 2025)+ MonitorVLM 条款映射:SOP 步骤表 → 步骤事件(视觉+声学旁路)→ 状态机对账;声学 PoC 限 1–2 工位 | 实时规则(S 系列)+ DSL | SOP 状态机从手工配步升级为"步骤词典自动对账" | 学术成熟度一般,先做 ≤5 步小 SOP PoC,不进承诺排期 |
| 自然语言编排(远期) | 感知条件注册为 VLM 工具(VipAct/Workflows 块类型参考),LLM 生成 DSL 草案 → 人工确认 + 回归 → 落库 | DSL + 控制台 | 降低换型门槛,产品差异点 | LLM 生成规则必须"确认+回归"双闸门,不自动上线 |

### 10.5 风险总表

| 风险 | 涉及选型 | 缓解 |
|---|---|---|
| 许可变更/传染 | GLM-5.3 系许可已变、X-AnyLabeling GPL-3.0、Ultralytics AGPL、DEIMv2 NOASSERTION、DINO-X API 商用协议、Defect-GLM 无许可 | 模型/数据**许可台账**(ADR-03 落地项),每次引入登记;GPL 只作独立工具 |
| 端侧 VLM 稳定性 | llama.cpp Qwen3-VL 图像挂起(issue #17012) | 端侧默认 2B/4B + watchdog 超时降级只拍不判;8B 只放服务器 |
| 免训练 AD 能力边界 | AnomalyDINO 弱于逻辑性缺陷 | 分工:外观→AD,逻辑/语义→VLM,分歧→UNCERTAIN 人工 |
| 域移(换光照/换产线) | 全部检测/AD 组件 | MVTec AD 2 已证明光照是最大杀手;参考 TTA 线(arXiv 2503.14910 / 2408.09494)做标定重校流程,评测集必含多光照 |
| 公开基准与现场差距 | MVTec AD 2 SOTA <80% 上限 | 公开集只当标尺,验收以自建回归集 + 现场抽检为准 |
| 回归集腐化 | 自建集长期不改 | 误报复核固定抽 N 条入回归集(告警闭环→评测回归自动回流) |
| 关键仓停更/迁移 | GroundingDINO 2024-08 停更、原 DEIM 仓 404 | 关键依赖选双源(如 D-FINE+RF-DETR),锁定 commit 离线包 |

### 10.6 与现有排期的映射(一句话版)

- **B2/V6 评测回归** ← MVTec AD 2 标尺 + 自建集 + Qwen3-VL/GLM 横评(SGLang vs vLLM)(阶段 1)
- **B0 检测器接入** ← D-FINE + RF-DETR(Apache-2.0 双底座);开放词汇候选 OV-DEIM(阶段 2)
- **V9 标定向导** ← Grounded-SAM2 文本圈选 + OWLv2 CPU 兜底(阶段 2)
- **V7 告警闭环** ← 自建状态机 + Frigate 式 GenAI 富化 + 复核回流(阶段 1 富化、阶段 2 闭环)
- **R6/R7 数据闭环** ← X-AnyLabeling(现在)→ CVAT 自托管(规模化)
- **模型上传/一键部署** ← anomalib + D-FINE/RF-DETR 导出 ONNX/OpenVINO + 签名(阶段 2);llama.cpp/SGLang 容器化(阶段 3)
- **训练腿升级** ← 教师伪标注 → Qwen3-VL-2B/4B 蒸馏 → 工控盒 GGUF(阶段 3)

---

*本文件为 2026-11-02 调研快照。所有"待复核/⚠"项在采纳时须核对仓库 LICENSE 全文与模型卡;stars 与活跃度以查询日 GitHub API 为准。*
