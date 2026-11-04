# 烟火检测候选模型训练来源泄漏排查报告（2026-11-05）

只读审计，不训练、不改数据、不动模型登记。固定测试集 = D-Fire 官方 test 切分 seed42 抽 200 图（协议复现通过）。对比脚本与中间产物在 `.v0/fire-leakage-audit/`。

## 0. 结论速览

| 候选 | 固定协议 AP50（D-Fire test 200 图, IoU0.5/conf0.25） | 泄漏风险 | 一句话结论 |
|---|---|---|---|
| `fire-smoke-zyazyz008-v1.onnx`（**0.800 候选**） | **0.7995** | **无法排除（偏高）** | 训练集 merged_fire_smoke（53489 train）未公开清单，无法做文件名/哈希对比；自报自集 mAP50 0.7957 与 D-Fire test 实测 0.7995 近乎重合，同分布/同源重叠嫌疑无法消除，0.800 视为存疑上界 |
| `fire-smoke-longlivewama-v1.onnx` | 0.3743 | 无法排除（实际风险低） | Roboflow 集（~7.5k 图）构成不可核验，可能含 D-Fire 衍生图；但实测 0.374 远低于自报 0.623，无虚高迹象，排名本就末位 |
| `fire-smoke-cctv.onnx` | （未入固定协议，仅 3 图探针） | 无法排除（评估记录缺失） | 上游 GitHub 仓库已 404、无 Wayback 快照，"CC-BY-4.0 数据集"无法溯源到具体集合；无固定协议指标可虚高，但也没有可比数字 |
| `fire-smoke-dfire-yolov8n-v1.onnx`（D-Fire 自训） | 0.7301 | **图像级已排除；场景级固有重叠已量化** | train/val/test 文件名零重叠、字节级 SHA256 零重复、21527 图哈希与镜像冻结 manifest 全对齐；但 dHash 发现官方切分存在跨切分同场景近重复帧（固定 200 样本中 126/200 受影响），这是 D-Fire 数据集固有属性，同等影响所有候选，非本训练管线引入 |

**核心判定：0.800 不能作为可信上界采信。** 在拿到 zyazyz008 训练数据清单或建成非 D-Fire 独立评测集之前，四个候选的 D-Fire test 数字只能用于同集排序，不能当作泛化能力声明。

## 1. 固定评测协议与测试集（审计基线）

- 协议出处：`.v0/fire-train/scripts/holdout_eval.py` + `.v0/fire-train/holdout-all.json`（2026-11-04）。
- 测试集：D-Fire 官方 test 切分 4306 图（未参与自训模型训练与 val 选模），`random.Random(42).sample(sorted(stems), 200)`。
- 真值：官方 YOLO txt，按训练同规则 sanitize（越界裁剪/零面积丢弃）；评测 `visual.detection_eval.evaluate_boxes`（IoU 0.5 / conf 0.25）+ COCO 101 点 AP。
- 本次复现（`reproduce_fixed_sample.py`）：200 图逐 stem 重放，truth_boxes=217 / smoke=102 / fire=115 / 负帧 99，与 `holdout-all.json` 记录逐项一致（`fixed_sample_200.json` 含全部 200 个 stem + 图像 SHA256，可作为固定集冻结清单，后续换集或复测时用于确认"同一固定集"）。

## 2. 四候选登记与评测记录盘点

| 候选 | 登记位置 | SHA256（本次复核=登记值） | 来源 | 许可 | 自报指标 | 固定协议指标 |
|---|---|---|---|---|---|---|
| zyazyz008 | `sop-vision/models/public/specialized-detection-models.json` 条目 `public-fire-smoke-zyazyz008-v1` | 0131d863…8b6d7a ✓ | ModelScope `zyazyz008/yolov8-fire-smoke-merged` | Apache-2.0（模型卡）；Ultralytics 基座 AGPL 单列 | mAP50 79.57% / P 82.01% / R 71.68%（自建 merged 集，120ep） | AP50 **0.7995**、AP50-95 0.4588、P 0.7877 / R 0.7696 / F1 0.7786、误报帧 37 / 漏帧 38 |
| longlivewama | 同上，条目 `public-fire-smoke-longlivewama-v1` | 826e2e2e…afa266 ✓ | GitHub `longlivewama/fire-and-smoke-detection` | Apache-2.0（源仓库 LICENSE） | mAP50 0.623 @832（Roboflow 集） | AP50 0.3743、P 0.4821 / R 0.3733 / F1 0.4208、误报帧 61 / 漏帧 81 |
| cctv | `sop-vision/models/public/safety-models.json` 条目 `public-fire` | f2699b75…c6fad ✓ | HF `fiacecson20/cctv-ai-fire-smoke` rev 343990e | 作者声明权重 MIT / 数据 CC-BY-4.0，`commercial_review_required` | val mAP@0.5 0.743（fire 0.912 / smoke 0.573，仅 12ep CPU） | 无固定协议指标；仅 3 图探针（test-fire*.jpg，conf 0.285–0.797），登记 `validation: single_image_smoke_only` |
| D-Fire 自训 | 同上，条目 `public-fire-smoke-dfire-yolov8n-v1` | 3cd72800…cf8a60 ✓ | 自训：D-Fire（Stevebirth CC0 镜像）官方 train 切分 + Ultralytics yolov8n 基座 | 数据 CC0-1.0（集合级）；基座 AGPL | val mAP50 0.7197（D-Fire 官方 val 1722 图） | AP50 0.7301、AP50-95 0.3916、P 0.7360 / R 0.6682 / F1 0.7005、误报帧 39 / 漏帧 49；48ms/img 最快 |

类别顺序注意：自训候选 ONNX names 为 `[smoke, fire]`（D-Fire 官方序），两个社区候选为 `[fire, smoke]`；评测时已按各自顺序传入并统一映射（`.v0/fire-train/onnx-names.json`、report.json）。四个 ONNX 的 `names`、任务、输入尺寸本次均经 `dump_onnx_metadata.py` 直接读取复核。

## 3. 0.800 候选（zyazyz008）训练来源追查 —— 无法排除

### 3.1 已查到的第一手证据

- **PT 权重内嵌训练参数**（`pt_train_args_dis.py`，pickletools 反汇编 data.pkl，全程未反序列化执行）：
  - `data = /home/system/merged_fire_smoke/merged_fire_smoke/data.yaml` — 私有 Linux 服务器路径，数据集名 `merged_fire_smoke`；
  - 恢复起点 `/home/system/fire_training_v4_real/yolov8s_real_fire/weights/last.pt`，且含 `distill_model` 参数 — 多阶段/蒸馏训练管线痕迹；
  - ONNX 本身（本地从该 PT 导出）metadata `args` 仅含导出参数（data=None），训练痕迹只在 PT 层。
- **ModelScope 模型卡**（API `modelscope.cn/api/v1/models/zyazyz008/yolov8-fire-smoke-merged`）：数据集描述仅"真实监控 / 合并烟火数据（含室内外、远近、小目标）"，**Train 53489 / Val 9039 / Test 7085 图**，类别 fire/smoke 2 类；未给出任何组成来源、外部数据集链接或文件清单。
- **数据集公开性**：`modelscope.cn/api/v1/datasets/zyazyz008/merged_fire_smoke` 返回 `{"Code":10020101002,"Message":"不存在的数据集"}` — 训练数据未随模型发布。

### 3.2 为什么无法做清单对比（缺什么）

任务方法要求的"图像文件名、标注文件名、数量、哈希抽样比对"需要 zyazyz008 的训练数据清单或数据包，两者都不存在（无公开仓库、无 README 附件、无 HF 同名数据）。本次能拿到且已拿到的全部可判证据只有 PT 内嵌的 data.yaml 路径与模型卡自述规模。**缺的信息：merged_fire_smoke 的文件列表（stem 列表或逐文件 SHA256）或可下载数据包。**

### 3.3 间接信号（ circumstantial，不作为证实）

1. 自报自集 val mAP50 **0.7957** vs D-Fire test 实测 AP50 **0.7995**，两个"不同测试集"的数字几乎重合——同分布（乃至同源）重叠的典型特征之一；
2. merged 集 train 53489 图 > D-Fire 全集 21527 图，D-Fire 是该领域最常见的公开烟火灾数据集，"merged"命名惯例上通常包含它；
3. 相对自训模型（同领域、官方 train 15499 图、40ep 未收敛）+0.069 AP50，无法区分是"数据量 3.5 倍的真实优势"还是"训练集包含 D-Fire（train 甚至 test）的同集优势"。

### 3.4 判定

**泄漏风险 = 无法排除（偏高）。** 0.800 的含义应记录为："同协议 D-Fire test 实测，训练来源与该 test 的同源性未排除"。这不是证实泄漏——恰恰相反，目前没有任何正面证据证明 merged_fire_smoke 含 D-Fire test 图像；但按证据标准（无法排除即不可采信为独立泛化指标），0.800 不得单独作为替换依据。

## 4. longlivewama（AP50 0.374）—— 无法排除，实际风险低

- 训练数据：PT 内嵌 `data = /content/Fire-and-Smoke-Detection-12/data.yaml`（Google Colab + Roboflow 导出包命名）；源仓库 README 声明 Roboflow `yang-nan/fire-and-smoke-detection-yqrpi` v12、6849 图；Roboflow Universe 页面（WebSearch 证据）显示 ~7.5k 图、4 个版本。
- 无法核验的部分：Roboflow 项目文件清单需账号/API key 才能导出，本次未取得；其构成是否含 D-Fire 衍生图（Roboflow 上大量烟火集为 D-Fire 上传改编）**未知 → 无法排除**。
- 但方向性证据明确：实测 AP50 0.3743 显著低于其自报 0.623（跨域失配），10 图试推中夜景火最高置信框为路灯误报——若真有 D-Fire 训练泄漏，方向应是虚高而非腰斩。**无虚高迹象，排名本就末位，不影响任何决策。**

## 5. fire-smoke-cctv（public-fire）—— 无法排除，且无固定协议指标

- 训练数据：HF 模型卡仅声明"CC-BY 4.0 dataset, downloaded at training time via scripts/download_dataset.py，Source images are NOT redistributed here"；HF `args.yaml`（本次取得）显示 `data: data/datasets/fire-smoke/data.yaml`（相对路径，无溯源信息）、12 epochs、CPU 训练。
- 溯源失败原因：上游 GitHub `fiacecson20/CCTV-AI` **已 404**（2026-11-05 实测），下载脚本与 TRAINING_REPORT.md 均无法取得；Wayback Machine 无该 URL 快照。
- 评测现状：从未进入固定 200 图协议（`holdout-all.json` 无此模型），登记仅 3 图探针。**它没有可虚高的指标，也没有可比指标**；对比表中缺席本身是一个需要补的洞（见 §7）。

## 6. D-Fire 自训候选（AP50 0.730）—— 图像级已排除 + 场景级固有重叠已量化

### 6.1 划分记录存在性 ✓

`.v0/fire-train/report.json` 含 `data_verification.splits_audit`（train 15499 / val 1722 / test 4306、逐切分空标签/框数/框问题统计）、训练 spec 固定 `dataset_sha256 = c41d3c8c…` 与基座 `base_sha256 = f59b3d83…`、`logs/audit.json` 复核记录。训练只用 train+val（repo_format），test 仅用于 holdout 评测。

### 6.2 本次独立复核（`dfire_split_census.py`，全量 21527 图哈希）

- 文件名 stem：train↔test、val↔test、train↔val 重叠均为 **0**；
- 字节级：SHA256 跨切分重复 **0**；
- 训练实际使用的 `dfire_repo_format` train/val stem 与官方 extract 完全一致（True/True），且不含 test（训练树无 test）；
- 镜像冻结 manifest `dfire_extract/dfire_yolo/file_manifest.json`（源=官方 D-Fire GitHub，CC0，21527 图 / 3,127,639,729 字节）与本次 census 逐图哈希 **21527/21527 全部一致，0 mismatch**。

**判定：test 对自训候选是图像级/字节级严格 holdout，排除训练管线引入的泄漏。**

### 6.3 场景级近重复（`dfire_phash_crosssplit.py`，64-bit dHash，hamming≤8）

官方 D-Fire 切分按图像而非按场景/相机切分，同一段视频的相邻帧大量横跨切分：

- 4306 张 test 图中 **2612（60.7%）** 在 train 有 hamming≤8 的伙伴，1929 在 val 有；命中对总数 train↔test 223,121 / val↔test 25,443；
- 固定 200 样本中 **126/200（63%）** 至少有一个跨切分近重复伙伴（train 125 / val 87）；
- 前 30 命中对像素级 MAD（64px 灰度）全部 ≤ 12.04，最低 0.60–1.04（例：test `AoF06738` ↔ train `AoF00100` MAD 0.60，内容几乎相同）。

**含义（如实声明）**：这不是训练管线造成的泄漏，任何在 D-Fire train 上训练的模型都同等受益于此"同场景重复"；它让 D-Fire test 的绝对 AP50 对所有候选一致偏乐观，同时 0.800 与 0.730 的同集排序仍有效。若要消除，需要按场景分组重新划分或直接换独立数据集——前者会破坏与官方口径的可比性，建议后者（§7）。

### 6.4 自训候选遗留事实（沿用 report.json，未推翻）

40ep 未收敛（mAP50 至 ep40 仍缓升）、AP50 0.730 未超过 zyazyz008 0.800、License 基座 AGPL 需商用评审。泄漏维度不改变这些登记结论。

## 7. 处置建议（只给方案，不执行）

### 7.1 可信度与采信

1. **provenance 可信度排序：自训 D-Fire > cctv ≈ longlivewama > zyazyz008**。自训是唯一"训练数据指纹+官方切分+独立 holdout"全链路可控的候选；zyazyz008 性能最高但来源黑箱。
2. 0.800 候选**不据其单独做替换决策**；若必须二选一，按"同协议 0.800 vs 0.730 + 泄漏未排除 vs 已排除"记录为：性能上界存疑的社区候选 vs 指标可信的自训基线。
3. D-Fire test 200 图数字对四者定位为**同集相对排序**，不是绝对能力声明；能力边界卡/文档引用时须带此限定语。

### 7.2 换独立评测集（建议尽快，方案如下）

- **目标**：与 D-Fire 及 merged_fire_smoke 均不同源，能同时区分"zyazyz008 是真优势还是同源优势"，并覆盖现役缺口负类。
- **组成建议**（每类 100–300 帧，多相机/多昼夜/多天气，全部人工框选真值，负样本空标注）：
  - 正样本：现场/公开来源的室内外烟火，但**避开 D-Fire 的三个来源族**（AoF、WEB、PublicDataset/HPWREN/FESB/FireNet），例如自采 Cifar 式现场巡检帧、FASDD 若采用需先确认 zyazyz008 未含（FASDD 也是热门 merged 成员，风险同 D-Fire）；
  - 专项负样本（本轮只列方案）：蒸汽（锅炉/厨房/高温管道排气）、焊接弧光（氩弧焊/CO2 焊近景与远景）、反光（金属面/玻璃幕墙/水面太阳反光）、灯光（路灯/车灯/探照灯夜景亮斑）、粉尘（打磨/切割/水泥输送扬尘）；另保留雾、朝霞、夕阳等自然硬负样本；
  - 时间持续性：每场景取连续 3–10 帧片段（不是孤立单帧），用于漏报/误报的帧级抖动与"起火前→冒烟→明火"时序记录（交接要求的时间持续性评测落在这里）。
- **协议**：沿用固定工作点 IoU0.5/conf0.25 + COCO 101 点 AP，增加"负样本误报帧率 = 误报帧数/负帧数"与"逐场景误报率"两个指标；样本清单与逐图 SHA256 冻结（仿照 `fixed_sample_200.json`）。
- **判别判据（预登记，防止事后解释）**：新独立集上若 zyazyz008 AP50 仍显著领先自训候选（差距量级与 D-Fire test 相当），可视为真实优势；若大幅回落（如 <0.60 且低于自训候选），同源优势/泄漏嫌疑坐实，转用自训或重训路线。

### 7.3 针对每个候选的定向动作

- zyazyz008：向作者开 ModelScope issue 索取 merged_fire_smoke 文件清单/数据包（成本最低的"排除"路径）；取得后用 `dfire_split_census.py` 同款哈希比对直接判定，或发布后按 stem 对 D-Fire test 4306 图查交集。
- cctv：先补跑固定 200 图协议（它目前无同口径数字），再谈候选资格；数据溯源可尝试联系作者或以 HF issue 询问。
- longlivewama：维持末位评估定位，无需专项动作；若 Roboflow 导出渠道可用（账号），可一次性导出 v12 文件清单核 D-Fire 交集，把"无法排除"转"已排除/证实"。
- 自训 D-Fire：保持现登记；100ep 重训与否按 report.json 原建议（与泄漏无关，属性能空间）。

### 7.4 协议加固

- 把固定 200 图集冻结为带 SHA256 的资产（本次已产出 `fixed_sample_200.json`）；后续任何烟火候选登记前要求其 `trial_evidence` 包含同协议数字，缺者标 `no_fixed_protocol_metric`（cctv 目前即此状态）。
- D-Fire test 的场景级重叠在模型卡/边界卡中作为已知限制写入（一句固定表述）："D-Fire 官方切分存在跨切分同场景近重复（固定 200 样本中 126/200 有跨切分近重复伙伴），绝对指标偏乐观"。

## 8. 证据文件清单

本次新增（均在 `D:\Documents\ChatGPT\visoul\.v0\fire-leakage-audit\`）：

| 文件 | 内容 |
|---|---|
| `dfire_split_census.py` / `dfire_split_census.json` / `census.log` | 21527 图全量哈希普查：切分互斥、前缀分布、与镜像 manifest 全对齐 |
| `reproduce_fixed_sample.py` / `fixed_sample_200.json` | 固定 200 图协议复现（217 框/102 smoke/115 fire/99 负帧）+ 逐图 SHA256 冻结清单 |
| `pt_pickle_scan.py` / `pt_train_trace.json` | .pt 原始字节模式扫描（zip 层，不反序列化） |
| `pt_train_args_dis.py` / `pt_train_args_strings.json` | pickletools 反汇编 train_args 字符串（未执行 pickle） |
| `dump_onnx_metadata.py` / `onnx_metadata.json` | 四候选 ONNX 元数据 + SHA256 与登记对账 |
| `dfire_phash_crosssplit.py` / `dfire_phash_crosssplit.json` / `phash.log` | 跨切分 dHash 近重复量化（2612/4306、固定样本 126/200、top30 MAD） |

既有记录引用：`sop-vision/models/public/specialized-detection-models.json`、`sop-vision/models/public/safety-models.json`、`.v0/fire-smoke/candidates.json`、`.v0/fire-train/report.json`、`.v0/fire-train/holdout-all.json`、`.v0/fire-train/scripts/holdout_eval.py`、`.v0/fire-train/onnx-names.json`、`.v0/fire-smoke/datasets/dfire_extract/dfire_yolo/file_manifest.json`、`docs/PUBLIC-SAFETY-MODELS.md`。

## 9. 诚实声明（未做/未验证）

- 未取得 merged_fire_smoke 与 Roboflow v12 的文件清单——三个社区候选的泄漏判定均为"无法排除"，且无法升级为"证实/排除"，除非拿到清单或建成独立评测集；
- 未运行任何模型推理（包括"D-Fire train 抽样记忆痕迹"实验——判别力有限：自训模型与疑似同源模型都会对训练过的图表现更好，无法区分 test 泄漏与 train 泄漏）；
- dHash hamming≤8 是启发式阈值，命中对是"近重复候选"，top30 像素 MAD 佐证其真实性但未逐对人工复核全部 22 万对；
- 所有网络查证（ModelScope/HF/GitHub/Wayback/Roboflow）为 2026-11-05 当日状态，上游内容可能随后变化；
- 本次未改动任何登记文件、未下载任何新数据、未触发任何训练。
