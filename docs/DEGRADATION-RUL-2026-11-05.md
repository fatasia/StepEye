# 退化 RUL 公开数据集登记与轻量基线（2026-11-05）

> 任务：为 StepEye 找真正退化 RUL 的公开数据（替代 NASA FD001 涡扇专域），建立趋势级退化基线，并给出接入机理实验室的方案。
> 结论先行：选定 **FEMTO/PRONOSTIA 轴承全寿命数据集**（Learning_set 6 个试验、565MB、许可=公开+学术引用）实际下载并校验；**相似性 RUL 基线 MAE=35.2 分钟（≈试验寿命 15.7%），±50% 命中率 80%**；趋势外推法在本数据上存在原理性病态（已实测并如实呈现）。数据与代码在 `.v0/degradation-rul/`，可复跑。

---

## 1. 数据集登记（三项候选，许可均实际打开核实）

| 数据集 | 来源 | 许可（实际核实结果） | 体积 | 试验数 | 采样协议 | 能否部分下载 |
|---|---|---|---|---|---|---|
| **FEMTO/PRONOSTIA**（IEEE PHM 2012 Challenge） | GitHub 镜像 [wkzs111/phm-ieee-2012-data-challenge-dataset](https://github.com/wkzs111/phm-ieee-2012-data-challenge-dataset)（原 femto-st.fr 链接已失效，此为全量备份）；原始论文 Nectoux et al. 2012, [HAL hal-00719503](https://hal.science/hal-00719503) | **无正式 LICENSE 文件**（镜像 README 原话："I don't know if there was any license to this, but always cite"）；数据当年为公开挑战赛发布，学界通行做法=公开使用+引用 Nectoux et al. (2012)。**引用义务成立，法律许可状态为"公开但未声明"** | Learning_set 565MB + Test_set 1099MB + Full_Test_Set 1363MB + 挑战文档 PDF 2.2MB | Learning_set 6 个全寿命试验（3 工况×2 轴承，寿命终点已知）；Test/Full_Test_Set 11+11 个（挑战真值在镜像中未附 RUL 标签文件） | 每 10s 采样 0.1s@25.6kHz（2560 点/文件），双通道（水平/垂直加速度），逐文件 CSV | **可以**：逐 CSV 存放，git partial clone + sparse-checkout 可只取任意子集；本次只取 Learning_set（565MB） |
| **XJTU-SY**（西安交大+昇阳） | 官方页 [biaowang.tech/xjtu-sy-bearing-datasets](https://biaowang.tech/xjtu-sy-bearing-datasets)，作者 GitHub [WangBiaoXJTU/xjtu-sy-bearing-datasets](https://github.com/WangBiaoXJTU/xjtu-sy-bearing-datasets)（仅 README，数据在外部网盘） | **无正式 LICENSE**；官方页声明"publicly available"+引用 Wang, Lei, Li & Li, IEEE Trans. Reliability 2020 (DOI: 10.1109/TR.2018.2882682)。Google Drive 整包 ID `1_ycmG46PARiykt82ShfnFfyQsaXv3_VK`，另有百度网盘 | 未注明总量（15 试验×每分钟 1 文件×32768 点，估算 >2.5GB） | 15 个 run-to-failure 试验（3 工况×5 轴承），失败模式有记录 | 每 1 分钟采样 1.28s@25.6kHz（32768 点/CSV），双通道 | **受限**：官方渠道为整包网盘分发（Google Drive/百度网盘），无按试验直链；百度网盘需登录。如后续需要可再评估 |
| **IMS**（辛辛那提） | NASA 官方 [data.nasa.gov/dataset/ims-bearings](https://data.nasa.gov/dataset/ims-bearings) 及 [PCoE 数据仓库](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository)（S3 镜像 phm-datasets.s3.amazonaws.com） | NASA 公开数据（PCoE 仓库发布，公开使用+引用 Qiu et al. 2006） | **整包 IMS.zip ≈ 10GB 级**（3 个试验 2156+984+672 个采样文件） | 3 个 run-to-failure 试验（4 轴承试验台，每次失效含 1 个被验证的失效轴承） | 每 10 分钟采样 1s@20kHz（20480 点） | **实质不可行**：单一大 zip 无官方拆分包，超过本任务 2GB 预算；如需必须整包下载 |

**选用决策：FEMTO/PRONOSTIA Learning_set。** 理由：(a) 体积可控且支持真正的按试验子集下载；(b) 6 个全寿命试验带寿命终点（Test_set 无公开真值标签，不能算 RUL 误差，故不取）；(c) 3 种工况便于做"同工况跨轴承"的干净划分；(d) 真实轴承试验台加速退化，与工厂旋转设备退化模式（RMS/峭度上升、突发失效）同域，优于 FD001 仿真涡轮风扇。XJTU-SY 与 IMS 登记在案，留作后续扩充（IMS 需整包 10GB，XJTU-SY 需网盘通道）。

---

## 2. 下载与完整性校验

**下载方式**（可续传、不重复下载）：

```bash
git clone --filter=blob:none --no-checkout https://github.com/wkzs111/phm-ieee-2012-data-challenge-dataset.git
git sparse-checkout init --no-cone
git sparse-checkout set 'Learning_set/**' 'README.md' 'IEEEPHM2012-Challenge-Details.pdf'
git checkout HEAD
```

落盘：`.v0/degradation-rul/femto-pronostia/`，工作树数据 565MB + PDF 2.2MB（含 .git 共 751MB，远低于 2GB 预算）。partial clone 按需取 blob，中断后 `git checkout` 重跑即续传。

**三层校验**（脚本 `.v0/degradation-rul/verify_femto.py`，退出码 0，全部通过）：

1. **结构对账**：6 轴承文件数与 GitHub trees API 一致——Bearing1_1=2803、1_2=871、2_1=911、2_2=797、3_1=515、3_2=1637（合计 7534 CSV）；文件名 acc_00001..acc_NNNNN 连续无缺口。
2. **对象校验**：`git fsck --full` 无损坏；抽样 8 个 blob 本地 SHA 与 GitHub API SHA 全部一致（partial clone 的 blob 本身按 SHA 寻址取包，此为二道关）。
3. **数据合理性**：抽样 18 个文件（每轴承首/中/末），全部 2560 行×6 列、振动值有限、文件内时长 0.100s（25.6kHz）。时间戳字段实测线性：Bearing2_1 acc_00001=08:14:15 → acc_00910=10:45:45，严格 10s 间隔。

**数据格式**：逗号分隔 6 列无表头 = 时,分,秒,微秒×10,水平加速度(g),垂直加速度(g)。

---

## 3. 基线协议

**定位（明确区分）**：这是**趋势预测/健康评分级别的轻量基线**，验证"公开退化数据 + 轻量信号特征 + 寿命外推"这条链路可行，不是精密 RUL 模型。所有误差数字来自 `results/baseline_report.json` 真实计算。

- **间隔采样**：每 6 个文件取 1（≈每分钟 1 点），6 试验共 1257 个采样点。
- **划分（按试验，同一试验绝不跨划分）**：同工况内对分——train {Bearing1_1, 2_1, 3_1}，test {Bearing1_2, 2_2, 3_2}。
- **特征**（双通道各一套）：RMS、峭度（Fisher 校正后 +3 约定）、峰值因子、标准差、高频带能量占比（FFT 5–12.8kHz / 全带）。
- **健康指标 HI**：log(max(rms_h, rms_v) / 基线)，基线=该试验前 3 分钟 RMS 中位数，前沿单调化（max-accumulate）。
- **失效阈值**：train 试验终点 HI 中位数 = **1.607**（三试验终点 2.34/1.61/1.18，跨度大——这本身是关键发现，见 §6）；阈值不确定性区间 [1.18, 2.34]。
- **寿命（实测）**：train 467/151/85 分钟，test 145/132/272 分钟（与官方文档的 2803/871/911/797/515/1637 个 10s 周期一致）。

**方法 1 趋势外推**：模型形式（指数 exp(a+b·t) vs 线性）由 train 试验 SSE 低者决定 → 选出线性（SSE 41.7 < 57.8）；test 试验用自身观测段（截至预测时刻）拟合参数外推至阈值。防护：预测 <0 截 0（越过阈值）、>700 分钟（1.5×最长 train 寿命）截断并标记 capped——防护只用 train 信息，无标签泄漏。

**方法 2 相似性匹配**（NASA 式）：test 观测段 HI 形状（min-max 归一化）与 train 全程 HI 逐段匹配（步长 5 分钟），最小欧氏距离段的剩余时长即预测 RUL。预测范围天然有界（≤ 最长 train 寿命 467 分钟）。

**评估协议**：每 test 试验在寿命 25/40/55/70/85% 五个预测点各预测一次（15 预测点/方法）。

---

## 4. 逐预测点误差表（真实计算值）

**方法 2 相似性匹配（推荐）**：

| 试验 | 预测点 | 观测时刻(min) | 真实 RUL(min) | 预测(min) | 误差(min) | 相对误差 | 匹配参考 |
|---|---|---|---|---|---|---|---|
| Bearing1_2 (145min) | 25% | 36 | 109 | 115 | +6.0 | +5.5% | 2_1 |
| Bearing1_2 | 40% | 58 | 87 | 93 | +6.0 | +6.9% | 2_1 |
| Bearing1_2 | 55% | 80 | 65 | 71 | +6.0 | +9.2% | 2_1 |
| Bearing1_2 | 70% | 102 | 43 | 49 | +6.0 | +14.0% | 2_1 |
| Bearing1_2 | 85% | 123 | 22 | 28 | +6.0 | +27.3% | 2_1 |
| Bearing2_2 (132min) | 25% | 33 | 99 | 93 | -6.0 | -6.1% | 2_1 |
| Bearing2_2 | 40% | 53 | 79 | 98 | +19.0 | +24.1% | 2_1 |
| Bearing2_2 | 55% | 73 | 59 | 78 | +19.0 | +32.2% | 2_1 |
| Bearing2_2 | 70% | 92 | 40 | 59 | +19.0 | +47.5% | 2_1 |
| Bearing2_2 | 85% | 112 | 20 | 39 | +19.0 | +95.0% | 2_1 |
| Bearing3_2 (272min) | 25% | 68 | 204 | 43 | -161.0 | -78.9% | 2_1 |
| Bearing3_2 | 40% | 109 | 163 | 358 | +195.0 | +119.6% | 1_1 |
| Bearing3_2 | 55% | 150 | 122 | 102 | -20.0 | -16.4% | 1_1 |
| Bearing3_2 | 70% | 190 | 82 | 62 | -20.0 | -24.4% | 1_1 |
| Bearing3_2 | 85% | 231 | 41 | 21 | -20.0 | -48.8% | 1_1 |

**汇总**（15 预测点）：

| 指标 | 相似性匹配 | 趋势外推 |
|---|---|---|
| MAE | **35.2 min**（占寿命 15.7%） | 220.9 min（91.4%） |
| RMSE | **66.7 min** | 337.5 min |
| 相对误差中位数 | +9.2% | -4.2% |
| ±25% 命中率 | 53.3% | 6.7% |
| ±50% 命中率 | **80.0%** | 6.7% |
| 分试验 MAE/寿命 | 1_2: 4.1%, 2_2: 12.4%, 3_2: 30.6% | 25.3% / 36.2% / 212.5% |

**方法 1 趋势外推的完整明细**（如实呈现病态）：Bearing1_2 早期偏短 -51%~-67%、85% 处 +154%；Bearing2_2 后期 3 点越阈被截 0（-100%）；Bearing3_2 全部 5 点被 cap 到 700 分钟（相对误差 +243%~+1608%）。8/15 预测点触发 cap。

---

## 5. 不确定性说明

1. **外推法 bootstrap 95% 区间**（200 次残差扰动重拟合）：观测充足的试验上较紧（如 Bearing1_2 70% 处 [32, 54]min），但 Bearing3_2 的原始外推分布远超 cap（如 85% 处 [9810, 25287]min）。**CI 未做 cap 截断**——保留原始分布正是为了暴露外推法在平坦 HI 上的巨大不确定性，cap 只是工程防护不是置信修正。
2. **阈值不确定性**：失效阈值 HI 取自 3 个 train 试验终点 {2.34, 1.61, 1.18}，区间宽达 ±36%——直接转化为 RUL 预测的系统偏差（ Bearing1_2 阈值偏高→预测偏短；Bearing2_2 阈值偏低→预测越阈归零）。
3. **相似性法**：无显式区间；其误差上界天然受参考库寿命约束（≤467min）。参考库仅 3 试验，Bearing3_1（85min）因容纳不下长观测段从未被匹配到。
4. **样本量**：test 仅 3 个试验、每试验 5 预测点。所有百分比指标的置信度低，数字应视为"该协议下的实测点"而非总体估计。

## 6. 关键发现与限制（诚实清单）

1. **趋势外推法对"突发型失效"原理性失效**：Bearing3_2 的 HI 全程平坦（P95=0.39，RMS 增长不足 1.5 倍），最后一刻跳到 1.72——RMS 趋势外推对这类模式无信息可用。FEMTO 文献公认约半数试验属突发型。**这决定接入平台后健康评分必须与相似性/参考轨迹方法配合，不能只用单试验曲线外推。**
2. **失效阈值跨试验不可迁移**：终点 RMS 倍数在 3.26×~10.34× 之间（4.7 倍跨度），单一中位阈值必然对部分试验系统性偏差。改进方向（未实施）：分位数阈值、kurtosis 主导 HI、混合 HI。
3. **相似性法的系统性偏差**：匹配集中于 Bearing2_1（形状渐变最丰富），预测误差呈恒定偏移（+6min/+19min），说明形状匹配对"相位对齐"敏感。
4. **本基线的边界**：85 分钟的 Bearing3_1 是最短参考，观测段>85min 的查询只能匹配更长参考；工况差异（转速/负载）未建模，HI 的试验内基线归一化部分吸收但未消除。
5. **许可边界**：FEMTO 无正式 LICENSE，商用/对外分发前建议邮件联系 FEMTO-ST 或改用许可更明确的 XJTU-SY（同样无正式 LICENSE，仅引用要求）。两者目前均为"公开+学术引用"惯例。
6. **未验证项**：XJTU-SY 与 IMS 仅完成桌面核实（官方页/API/文档），未实际下载验证体积与文件结构；IMS 的 ~10GB 体积为文档与仓库元数据推算，非实测。

## 7. 接入方案（只写方案，不改平台代码）

现有 `sop-vision/visual/mechanism_rul.py`（NASA-CMAPSS-FD001 专用）的可复用模式：**pinned bundle（SHA256 校验资产）+ pydantic 严格契约（Literal dataset 枚举）+ capabilities() 声明 scope + 子进程 worker IPC（30s 超时、512KB 限制）+ 线程锁 429**。退化 RUL 按**平行服务**接入，不动现有代码：

1. **模型资产打包**：`results/baseline_report.json` 中的可迁移参数——模型形式（线性）、失效阈值 1.607 与区间 [1.18, 2.34]、rul_cap=700min、特征定义（通道/FS/高频带）、HI 定义（基线窗口 3min、单调化）——整理为 `manifest.json`（含 `dataset: FEMTO-PRONOSTIA`、`scope: trend-level, MAE 35min/15.7% on 3 held-out bearings`），存 `models/mechanism/femto-degradation/`，仿 `pinned_bundle()` 做 SHA pin（把 6 个 train 试验的下采样 HI 参考序列 `reference_hi.json` 一并打入，相似性法需要）。
2. **新服务** `visual/mechanism_degradation_rul.py`（与 `mechanism_rul.py` 平行，不修改后者）：仿 `RULService` 结构：
   - 输入契约：`{unit_id, t_obs_min, windows:[2560 点×2 通道]}`（2560 点=0.1s@25.6kHz，与 FEMTO 同构；工厂振动采集按 25.6kHz/0.1s 窗口对齐），pydantic 严格校验列数/点数/有限值（复用 `Finite` Annotated 模式）；
   - 服务端算特征→HI→双方法 RUL：相似性法（主）+ 外推法（辅，带 cap 标记）+ 健康评分（建议 `score=100×exp(-HI/1.607)`，阈值处=36.8 分，语义=退化到 train 终点中位水平）；
   - `capabilities()` 明确声明：`scope: bearing trend-level RUL, not precision RUL; validated on FEMTO 3 held-out bearings; sudden-failure mode partially unpredictable`；
   - worker 子进程计算（特征+FFT 在 worker 内做，主进程只做契约校验），沿用 30s 超时/锁/429 三件套。
3. **机理实验室 UI**：RUL 卡片增加 dataset 切换（FD001 / FEMTO-degradation），响应体已有 `dataset` 字段可直接区分；预测区间 `ci95_min` 与 `capped` 标志透传到 UI，突发型高风险（外推 CI 超限）时显示"趋势不可用，建议参考相似性结果"。
4. **数据更新路径**：后续接入 XJTU-SY（15 试验、许可同为公开+引用）扩充参考库时，只需重打 `reference_hi.json` + 更新 manifest SHA；服务契约不变。

## 8. 复跑指南

```bash
cd D:/Documents/ChatGPT/visoul/.v0/degradation-rul
../action-venv/Scripts/python.exe -I verify_femto.py femto-pronostia          # 完整性校验
../action-venv/Scripts/python.exe -I femto_baseline.py femto-pronostia/Learning_set --out results   # 基线
```

产物：`results/features.csv`（逐点特征）、`results/baseline_report.json`（全部误差数字）、`results/rul_curves.png`（六试验 HI 曲线+外推段+阈值带+相似性匹配点）。依赖：numpy 2.5.4 / scipy 1.16.3 / matplotlib 3.10.6（action-venv 实测存在）。运行耗时约 1 分钟。

**代码文件**：`D:\Documents\ChatGPT\visoul\.v0\degradation-rul\{verify_femto.py, femto_baseline.py, results/}`；数据：`D:\Documents\ChatGPT\visoul\.v0\degradation-rul\femto-pronostia\Learning_set\`。

**引用义务**（使用该数据必须附带）：Nectoux, P., et al. "PRONOSTIA: An experimental platform for bearings accelerated degradation tests." IEEE PHM 2012 Data Challenge, 2012. (HAL hal-00719503)
