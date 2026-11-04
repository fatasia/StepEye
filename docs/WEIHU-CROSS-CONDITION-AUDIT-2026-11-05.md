# weihu 跨工况资产核查（电机跨工况诊断）

日期：2026-11-05。性质：只读盘点，未训练、未改 weihu 与 StepEye 任何文件。核查范围：本机 `D:/Documents/ChatGPT/weihu`（版本记录 `01d77002e960b93254f137a8dd7e618abb12c3ae`，见 visoul `docs/WEIHU-REUSE.md`）。

## 一、weihu 结构摘要

| 目录 | 内容 |
|---|---|
| `code/python/` | `src/phm_core/`（约 40 个核心模块）+ 约 50 个 `study_*.py`/`export_*.py` 独立研究脚本 + `tests/` |
| `code/ts/` | 演示界面（未迁移，按约定不搬后台） |
| `code/benchmarks/` | 汇总 JSON（含 `rejection.json` 工况拒识基准） |
| `data/raw/` | 21 个公开数据集本机副本：`mcc5-thu-motor`(41GB)、`servo-dc`(3.1GB)、`cwru`(67MB)、`cranfield-actuator`、`visa`、`zema-*` 等 |
| `outputs/` | 约 75 个研究运行目录（电机相关 9 个，见下） |
| `docs/` | 31 篇编号交付文档；**`23-变工况电机数据审计与实验协议.md` 是跨工况电机专项** |
| `work/` | 运行日志（`motor-*.log`、`download-cwru.log` 等） |

电机/旋转机械/诊断相关核心模块：`phm_core/rotating.py`（包络谱）、`phm_core/current_signature.py`（电流/振动特征 + 转速基线）、`phm_core/conditioning.py`（工况参考归一化）、`phm_core/actuator.py`、`phm_core/valve.py`、`phm_core/degradation.py`/`prognostics.py`。

## 二、已迁移资产对照表（StepEye 侧 vs weihu 原始位置）

| StepEye 资产 | weihu 原始位置 | 配套资产是否遗漏 |
|---|---|---|
| `models/mechanism/motor-rf/model.onnx`（电机 24 类 RF，manifest 标 `protocol=held-out-torque-profile, arm=fusion_rf, seed=42`，SHA `54e8ef68…`） | `outputs/motor-rf-export/runs/20261031T205846Z-a699e055`（训练源 `outputs/motor-study/runs/20261031T203348Z-59f2d37f`） | manifest 已含 `feature_names` 114 列与特征缓存 SHA，无遗漏 |
| `models/mechanism/motor-rf/motor_features.py`（114 维特征提取，6 通道统计/频谱 + 电流相序 + 27 项包络阶次） | `code/python/motor_features.py` | **SHA 逐字节一致（`b46eb635…`），已核对**。依赖它生成的特征缓存 `outputs/motor-features/runs/20261031T202007Z-23f5edbb`（12,657 窗口，394MB）留在 weihu，未迁移 |
| `visual/mechanism_rotating.py`（轴承包络谱） | `code/python/src/phm_core/rotating.py::envelope_evidence` | CWRU 验证研究 `code/python/study_rotating.py` + 数据 `data/raw/cwru` 未随迁（见下） |
| `visual/mechanism_core.py`/`mechanism.py`（周期偏离）、`mechanism_valve.py`（阀门双证据）、`models/mechanism/valve-fusion`、`cmapss-rul`、`actuator-ensemble` | `phm_core/degradation.py`/`prognostics.py`、`phm_core/valve.py`+`study_valve_fusion.py`+`support.py::valve_support`、`outputs/valve-runtime`、RUL 链路、`outputs/actuator-*` | 已在 `docs/WEIHU-REUSE.md` 登记，本表不重复 |

关键遗漏点：StepEye 只拿了**模型与特征提取函数**，没有拿**训练/评估链路**——`study_motor.py` 的整工况留出协议、`study_motor_cnn.py`、`extract_motor_orders.py`、以及 394MB 特征缓存都留在 weihu。

## 三、跨工况相关发现

### 1. weihu 已做过完整的跨工况电机专项，且结论是失败（不是没做过）

`docs/23-变工况电机数据审计与实验协议.md`（2026-11-01）记录了 MCC5-THU 288 条 CSV、12 种文件名工况的完整审计与实验：

- 协议（`code/python/study_motor.py` L117–131）：整组留出 `held-out-3000rpm`（`nominal_rpm==3000`）与 `held-out-torque-profile`（`profile=="torque"`）；源工况逐工况留一验证；三种子源准入门槛（FPR≤5%、故障召回≥95%、宏F1≥90%）。
- 结果（doc 23 表格）：**3000rpm 跨转速全部失败**——RF 宏F1 0.52–0.55、全带宽 CNN 0.26–0.53、二分类 0.49、角域谱 CNN 0.02–0.09、包络角域谱 CNN 0.02–0.07。同型 torque 任务均 0.86–0.95。
- **阶次/角域归一化已在电机数据上试过且无效**：`code/python/extract_motor_orders.py` 实现了键相等角度重采样（每转 256 点、时间域抗混叠、0–64 阶 512 点对数功率谱），角域缓存 `outputs/motor-order-features/runs/20261031T204743Z-19d3b7ef` 存在；跨转速反而更差。doc 23 明确"单纯转为角域谱没有解决当前表示和工况差异，不进入部署候选"。
- 这解释了 StepEye 侧"同转速准、跨转速失效"的现象：weihu 原始研究早已量化确认，且已排除"角域重采样"这一最常见修复。

### 2. 未迁移的方法资产（可移植，但 weihu 自己的证据均未达到可用水平）

| 资产 | 位置 | 作用 | weihu 实测结论 |
|---|---|---|---|
| `SpeedConditionedBaseline` | `code/python/src/phm_core/current_signature.py` L185–225 | 正常特征随转速在对数轴分段线性插值；尺度含"留一转速插值残差"，支持在没见过的转速上评分 | `study_servo_tuning.py`（L1–8 协议）在伺服电流上：速度环 4/8、松动 2–3/11，条件基线 vs 全局基线差异区间重叠，"不能当作改进的证据"（`docs/28` §5.6） |
| 按转速留出评估协议 | `study_servo_tuning.py`（评估单位=工况点、阈值取训练转速留一分位） | 跨转速泛化的冻结评估框架 | 协议本身成立，故障检出不足是信号问题 |
| 类条件均值/协方差域对齐（CORAL 思想） | `code/python/study_phase_coral.py`（`alignment_loss` L32–47，仅同类源工况对齐、不用目标标签） | 域自适应训练 | `docs/20-执行器深度工况对齐对照.md`：执行器两协议 54 次拟合，源准入全部未过，"没有形成跨协议稳定改善" |
| 工况参考归一化 | `code/python/src/phm_core/conditioning.py::fit_reference/transform_reference`（按工况 median 中心 + 共享 MAD 尺度；未知工况直接拒绝） | 变负载下残差标准化 | 用于执行器（`study_conditioning.py`、`study_context_gate.py`、`study_context_selection.py`）；doc 17/28 结论为"仍未解决新工况可靠选型"，候选未晋升 |
| 记录级工况拒识 | `code/python/study_condition_rejection.py`（robust 缩放特征空间最近邻，源校准定阈值）；基准 `code/benchmarks/rejection.json` | 未知工况下拒识而非误判 | 探索性基线，报告拒识覆盖与检出下界 |
| 整工况留出 + 源准入选型协议 | `study_motor.py`（上表）；同构逻辑在 `phm_core/actuator.py::split_records` | 跨工况实验的正确划分方式 | 协议可复用，与模型无关 |

StepEye 侧 grep 确认以上均不存在（`sop-vision` 中无 `SpeedConditioned`/`fit_reference`/`coral`/`angular_spectrum` 命中，仅 torchvision 类名误匹配）。StepEye 的执行器文档也明示"未知工况、跨工况未通过验证，不在本模块范围"（`docs/MECHANISM-ACTUATOR.md`、`MECHANISM-ACTUATOR-CLASSIFIER.md`）。

### 3. 本机已有带转速/工况标签的电机数据（跨工况训练数据可行）

| 数据 | 位置 | 标签证据 | 体量 |
|---|---|---|---|
| MCC5-THU 感应电机 | `data/raw/mcc5-thu-motor/extracted/`（288 CSV） | 文件名含工况：`…_bearing_ball_H_speed_circulation_20Nm_1000rpm_250707143259.csv`（轨迹 profile × 20/40Nm 负载 × 1000/2000/3000rpm），另有键相通道（每转一脉冲） | 41GB，逐文件审计已过（`outputs/motor-audit/runs/20261031T200929Z-86f4c204`） |
| CWRU 轴承 | `data/raw/cwru/`（16 个 .mat） | .mat 内含 `RPM` 字段（4 种负载/转速 1730–1797rpm） | 67MB |
| 直流伺服电机 | `data/raw/servo-dc/`（49 个振动 wav + 电流 bin） | 文件名 `speed001–speed100`（额定 2000rpm 的 1%–100%，14 档转速）+ 故障状态 | 3.1GB，逐文件 SHA-256 已核 |

weihu 在 CWRU 上只做了证据级检查（`study_rotating.py`：12 条 seeded-fault 记录包络证据 top-candidate 命中 8/12，重复信号 1 条，`outputs/rotating-study/runs/20261031T060441Z-116d9bae`），**没有跨工况分类研究**——这是一个未开垦的点。

## 四、建议下一步

1. **不要在 MCC5-THU 上重复任何已做实验**。RF/CNN/角域/二分类共 480+ 次拟合已完成，结论固定：该数据 3000rpm 外推失败且角域重采样无效（doc 23）。weihu 无任何"被证明有效"的跨工况电机方法可迁移——迁移不会带来已验证的增益。
2. **可迁移的评估基建**（低风险、方法学价值）：把 `phm_core/current_signature.py::SpeedConditionedBaseline` + `study_servo_tuning.py` 的按转速留出协议移植进 StepEye，先在 `data/raw/servo-dc`（14 档转速、3.1GB 本机已有）上做跨转速故障判别小实验；这是 weihu 唯一"协议成立、仅信号不足"的跨转速框架。
3. **CWRU 是现成的跨负载分类切入点**：数据本机已有、带 RPM 字段，weihu 只做过证据级检查；可在 StepEye 用已迁移的 `mechanism_rotating.py` 包络特征做 4 负载留一验证，成本远低于 MCC5-THU。
4. **根本瓶颈是工况覆盖而非算法**：doc 20 结论"下一轮重点扩展独立工况与真实多信号记录，而非只增加网络复杂度"。若需要变工况全寿命数据，doc 28 L136 已核实许可未下载的候选：zenodo 10868257 滚动轴承变工况全寿命（CC BY 4.0，约 152GB，17 次试验可只取单个试验）。
5. **若一定要在电机分类上继续**：可迁 `study_motor.py` 的整工况留出协议作为 StepEye 训练基建，并把 `outputs/motor-features` 394MB 特征缓存（12,657 窗口）作为现成训练输入，避免重跑 41GB 原始数据的特征提取——但预期收益受 doc 23 已证明的表示偏移限制。

## 证据索引

- `D:/Documents/ChatGPT/weihu/docs/23-变工况电机数据审计与实验协议.md`（全文）
- `D:/Documents/ChatGPT/weihu/docs/20-执行器深度工况对齐对照.md`、`docs/17-运行可靠性与工况选型.md`、`docs/28-P0P1P5证据与数据扩展.md`（§5.6、L136）
- `D:/Documents/ChatGPT/weihu/code/python/study_motor.py`（L117–131）、`study_servo_tuning.py`、`study_phase_coral.py`、`study_rotating.py`、`study_condition_rejection.py`、`extract_motor_orders.py`
- `D:/Documents/ChatGPT/weihu/code/python/src/phm_core/current_signature.py`（L185–225）、`conditioning.py`
- `D:/Documents/ChatGPT/weihu/code/RESULTS.md`（执行器/电机/伺服振动三行结论）、`EXPERIMENTS.md`（变工况电机入口）
- `D:/Documents/ChatGPT/weihu/outputs/{motor-study,motor-features,motor-order-features,motor-rf-export,motor-rf-verification,motor-cnn-study,motor-order-cnn-study,rotating-study,servo-tuning-study,servo-vibration-study,servo-vibration-orders-study,phase-coral-study}/`
- `D:/Documents/ChatGPT/weihu/data/raw/{mcc5-thu-motor,cwru,servo-dc}/`
- `D:/Documents/ChatGPT/visoul/docs/WEIHU-REUSE.md`、`docs/MECHANISM-MOTOR.md`、`docs/MECHANISM-ACTUATOR.md`、`docs/MECHANISM-ACTUATOR-CLASSIFIER.md`
- `D:/Documents/ChatGPT/visoul/sop-vision/models/mechanism/motor-rf/{manifest.json,motor_features.py}`（SHA 已核对一致）
