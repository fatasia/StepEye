# weihu 核心复用

机理实验室复用设备分析内核，不搬演示后台、账号或训练管理页面。

## 实际代码映射

已核对本机 `D:/Documents/ChatGPT/weihu` 源码，版本
`01d77002e960b93254f137a8dd7e618abb12c3ae`。仓库 LICENSE 为 MIT，
版权 `Copyright (c) 2026 fatasia`；移植模块保留完整许可声明。

| weihu 源码与真实 API | StepEye 状态 | 依赖和输入 |
|---|---|---|
| `phm_core/degradation.py`、`prognostics.py` | 已有 `mechanism_core.py`、`mechanism.py`：固定参考周期偏离和线性外推，不重复移植 | NumPy；等长周期 CSV；首段健康参考是输入假设 |
| `phm_core/rotating.py::envelope_evidence(values,sample_rate,rpm,orders,band)` | 本批新增 `mechanism_rotating.py::analyze_rotating`，复用带通/Hilbert/功率谱/三谐波证据 | NumPy、可选 SciPy；单路加速度、实际采样率、恒定转速、轴承阶次和共振带 |
| `phm_core/valve.py::mechanism_features/fit_mechanism_reference/mechanism_score` | 换向时序、冻结参考与波形AE已嵌入复用的ONNX；不另训练，不是通用阀门模型 | NumPy、ONNX Runtime；严格 `[batch,400,2]`，PS1/PS2、bar、100Hz、周期第8–12秒窗口 |
| `study_valve_fusion.py::conjunction_score`、`phm_core/support.py::valve_support` | 本批新增 `mechanism_valve.py`：融合图隔离影子分析，先检查命令边沿及逐通道时标 | 波形AE与时序参考使用冻结健康校准；同步/通道偏斜容差1点（10ms） |
| `phm_core/inference.py::BundleScorer`、`portable.py::PortableScorer`、`phm_runtime/registry.py` | 未搬后台运行时；复用SHA/通道/单位/适用域检查设计，session只在一次性子进程中创建 | 固定schema1包由BundleScorer真实验证；PortableScorer仅支持schema2，不能直接读取此包 |

上游 `pyproject.toml` 必装仅 NumPy。包络谱实际还依赖 SciPy；StepEye
用 `sop-vision/requirements-mechanism.txt` 单独声明，不把训练栈带进实时进程。

## 本批：轴承包络谱证据

```powershell
cd sop-vision
python -m pip install -r requirements-mechanism.txt
python -m pytest tests/test_mechanism_rotating.py tests/test_mechanism.py -q
```

入口：`mechanism_rotating_routes.rotating_router(store)`。主服务挂载后：

- `GET /api/mechanism/rotating/capabilities`：可用依赖、单位与大小限制。
- `POST /api/mechanism/rotating/analyses`：分析并保存不可变报告。
- 列表、详情、JSON导出沿用 `/api/mechanism/analyses`，报告种类为
  `bearing_envelope_evidence`，不新建第二套SQLite表。

JSON 输入示意，`values` 为完整连续波形，以下空数组仅标明字段位置：

```json
{
  "title": "轴承振动窗口",
  "unit": "g",
  "sample_rate_hz": 12000,
  "rpm": 1200,
  "band_hz": [1200, 5000],
  "orders": {"inner_ring": 5.4152, "outer_ring": 3.5848},
  "values": []
}
```

也可用 `csv_text` 替代 `values`，两者互斥。CSV表头必须为
`time_s,acceleration`，时间间隔与声明采样率相符（容差1%），不重排或补点。
要求1–10秒、最多200000点、幅值绝对值≤1e6、单位 `g` 或 `m/s2`。
阶次必须显式提供；上例取自上游6205轴承参数，不适用于所有轴承。
三次特征谐波须低于奈奎斯特频率。

结果字段：

- `status=evidence_only`；无变化波形为 `unknown`，不返回健康/故障合格判定。
- `samples/duration_s/sample_rate_hz/unit/rms/crest_factor`：窗口和时域信息。
- `candidates[].peaks[]`：`harmonic`、`expected_hz`、`observed_hz`、`contrast`；
  `evidence_score` 是三谐波局部谱峰对比的 `sum(log1p(contrast))`，不是概率。
- `frequency_hz/envelope_psd`：≤1000Hz的显示谱，分组最大值保峰且≤2000点；
  证据始终使用完整功率谱，显示抽样不参与计算。
- `review_required=true`、`production_approval=false`、`supports_rul=false`。

算法按请求执行，无模型权重、后台线程或常驻采集任务。导入模块不加载SciPy；
依赖缺失时实际分析返回503。未获得安装位置、稳速和轴承几何验证时，结果只能作人工参考。

## 验证

本批实际运行：StepEye专项与既有机理测试 **48 passed**；上游
`test_support.py`、`test_industrial_algorithms.py` **15 passed**。
确定性12kHz/1秒AM波形在上游原函数与移植函数之间，候选、时域统计及完整显示谱逐值一致；
100Hz调制峰正确定位。测试覆盖CSV/JSON等价、缺样拒绝、容量、503/429、不可变报告重开，
不包含真实设备诊断准确率结论。上游未找到直接针对 `envelope_evidence` 的测试。

## 本批：阀门双证据影子分析

本机已有
`outputs/valve-runtime/runs/20261102T091746Z-1c000aed/bundle/ae.onnx`，435073字节，
清单SHA `c5250ae6d94a8f2a4b755a538451e24496200c2864e4a01aa56ce3d84c45fa67`。
实际文件SHA与清单相同，7个随包校验窗口经CPU推理，与参考输出逐值一致，最大绝对差0。
本机ORT 1.27.0单次记录：加载23.84ms，首批7窗口4.82ms，热单窗口1.11ms；不是性能分位数。
清单声明 `valve-fusion-runtime`、`research_only`，输入 `[batch,400,2]`，
命令边沿后8秒起窗、容差1点。只依赖NumPy与ONNX Runtime，不加载torch、pickle或训练器。
上游已有 `test_valve.py`、`test_valve_residual_study.py`、`test_support.py`、
`test_runtime_sync.py`、`test_portable.py`；本批仅执行前述两项无训练依赖测试文件。

固定资产目录 `sop-vision/models/mechanism/valve-fusion/` 跟踪manifest、来源、MIT及数据署名；
ONNX不入Git。新部署须将已校验的 `ae.onnx` 放入此目录，缺失时能力不可用，不自动下载。
权重清单未单独声明发布许可，登记为 `evaluation_only`。

主线挂载 `mechanism_valve_routes.valve_router(store, ValveShadow(ROOT))` 后提供：

- `GET /api/mechanism/valve/capabilities`：固定模型及输入契约。
- `POST /api/mechanism/valve/analyses`：共享机理报告存储，种类 `valve_fusion_shadow`。

请求字段为 `title`、`channels:["PS1","PS2"]`、`units:["bar","bar"]`、
`sample_rate_hz:100`、`values`（400行、每行两数）；同步声明为
`clock_id`、`timestamps_s:{PS1:[400个秒数],PS2:[400个秒数]}`、
`command_edge:{time_s,clock_id,source,kind}`。`kind` 仅接受
`controller_command`、`digital_input`、`replay_fixture`。
不能传模型路径、覆盖历史阈值或用 `sync_ok:true` 代替时标。

缺命令/时标、时钟ID不一致、缺样/逆序、起窗偏移或通道偏斜超10ms：
保存 `status=unsupported`、`score=null`，不启动推理。
通过的是**上传时标声明的一致性**，不是可信设备认证；`trusted_device_source=false`。
回放声明标为 `validation_scope=replay_fixture`，其他上传为 `uploaded_timestamp_claims`。

可评分时返回 `status=shadow_evidence`、融合 `score`、`historical_threshold:1` 和
`threshold_exceeded`；大于1仅表示两支路同时越过历史校准阈值，不是概率/当前故障定论。
不发报警、不控制、不进入SOP。一次性CPU进程用私有有界请求文件，退出后不留session；
繁忙429、超时终止504、模型/依赖不可用503，关闭仅终止自己的子进程。

新增阀门+轴承+既有机理测试 **80 passed**；包括实际睡眠子进程超时、运行中关闭、
不误杀其他进程、缺证据不推理、固定SHA、不可变报告、真实随包窗口经本功能HTTP评分。
回放样例 `.v0/mechanism/valve-api-fixture.json` 的压力来自随包 `raw[0]`；
时标是明确生成的回放轴，非现场设备采集。期望分数约0.81715834。

主服务已挂载轴承入口；机理实验室新增“轴承振动分析”，支持波形CSV、采集参数和实际轴承阶次，报告与历史/导出共用。真实Web已验证2秒合成100Hz调制信号的上传、分析、持久报告、viewer只读及403；这是软件与信号处理验证，不是设备故障准确率。截图位于 `.v0/output/playwright/rotating/rotating_1920.png`。
