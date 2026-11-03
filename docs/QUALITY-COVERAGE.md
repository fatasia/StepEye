# 质量任务与分析工具

Q11/Q12 提供测量或分类结果的 CSV/JSON 导入、统计分析和不可变报告。它分析已经取得的数据，不替代相机标定、现场采样和量测系统验收。

## 三步使用

1. operator 在 `POST /api/quality/analyses` 提交 `kind`、`rows` 或 `csv_text`，可附 `title`、`unit`、`options`。
2. viewer 可读 `GET /api/quality/analyses` 与 `/{id}`；`/{id}/export` 下载包含原始输入、输入 SHA256 和结果的 JSON 报告。
3. 核实报告警告、采样设计与现场事实后人工决定下一步。报告没有自动合格判定或生产发布动作。

最多 20,000 行；CSV 最多 2MB UTF-8，JSON 规范化输入最多 4MB。报告保存在 `quality_reports.db`，重启后保留；数据库拒绝修改或删除已有报告。MCP 专用标注令牌不获得质量数据访问权限。

## Q11：SPC、能力与缺陷报表

### 个体值 I-MR

```json
{
  "kind": "spc",
  "title": "间隙复核",
  "unit": "mm",
  "rows": [{"value": 10.1}, {"value": 9.9}, {"value": 10.0}],
  "options": {"lsl": 9, "usl": 11, "baseline_count": 3}
}
```

输入顺序就是采集顺序，不自动排序、删点或补测量。`baseline_count` 是首批基线点数，至少 2；省略时用全部输入。后续点使用冻结的控制界限，不因漂移而重新学习。控制界限和产品规格上下限是两回事。

公式采用[ NIST 个体控制图](https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc322.htm)：`MR=abs(x[i]-x[i-1])`、`σwithin=MRbar/1.128`、I 图 `baseline_mean±3σwithin`；MR 图用 `LCL=0`、`UCL=3.267MRbar`。本版标记 I 图越 3σ 和 MR 越上限，不包含完整 Western Electric/Nelson 规则，返回“未发现本规则信号”而非“过程稳定”。

- `Cp=(USL-LSL)/(6σwithin)`；`Cpk=min((USL-mean)/(3σwithin),(mean-LSL)/(3σwithin))`。
- `Pp/Ppk` 公式相同，但分母使用**全部观测的样本标准差，ddof=1**；四个指标都使用本次分析期均值。Cp/Cpk 的 within sigma 来自指定基线移动极差。
- 单边规格只给 CPU/CPL、PPU/PPL 及相应最小可用单边指数；无双边规格时 Cp/Pp 为 null。均值越过规格时 Cpk/Ppk 可以为负，不能截成 0。
- 零变差不输出无穷大，也不表示过程完美；过少基线、控制图信号和未验证假设均显式警告。规格外比例是输入样本的实测比例，不是正态分布推算的未来 PPM。

within 与 overall 的区别见 [Minitab 能力口径](https://support.minitab.com/en-us/minitab/help-and-how-to/quality-and-process-improvement/capability-analysis/supporting-topics/capability-metrics/potential-and-overall-capability/)。正态性、独立性、基线稳定性和量测系统质量没有被本工具验证，所有指数为描述性估计；没有自动放行阈值、置信区间或非正态转换。[NIST 能力研究前提](https://www.itl.nist.gov/div898/handbook/pmc/section1/pmc16.htm)

### 缺陷 Pareto

`kind="pareto"`，行格式为 `{"defect":"划伤","count":3}`，`count` 可省略为 1。同名缺陷合并并按次数降序，返回占比与累计占比。缺陷次数不等于不良件数；没有检验件数和缺陷机会数，不返回良率或 DPMO。

## Q12：GR&R 与人员／系统分类一致性

### 连续测量 GR&R

`kind="grr"`，支持以下 CSV 表头：

```csv
part,operator,trial,value
P1,人员A,1,10.2
P1,人员A,2,10.1
```

以上仅展示格式，不是完整研究。每个零件必须被每个测量者测量同一组重复次数；至少 2 零件、2 测量者、2 次重复。缺测、重复的 part/operator/trial、嵌套／破坏性试验和不平衡设计直接拒绝，不悄悄补值。可把 operator 命名为视觉系统版本或工位，不涉及人员身份识别。最多 50 测量者、1,000 零件、1,000 次重复，总行数仍限 20,000。

实现完整平衡交叉随机效应 ANOVA，**始终保留零件×测量者交互**。设 p/o/r 为零件／测量者／重复数：

```text
var_repeat = MSerror
var_interaction = (MSinteraction - MSerror) / r
var_operator = (MSoperator - MSinteraction) / (p*r)
var_part = (MSpart - MSinteraction) / (o*r)
var_reproducibility = var_operator + var_interaction
var_GRR = var_repeat + var_reproducibility
var_total = var_GRR + var_part
```

各基础负分量按 0 截断，原值及被截断项一并返回。输出 SS/df/MS、分量标准差、6σ study variation、%Contribution、%Study Variation、可选双边规格的 %Tolerance 和 ndc。`%Contribution` 用方差比，`%Study Variation` 用标准差比，两者不能混用；ndc 为 `max(1,floor(1.41*SDpart/SDgrr))`，分母为零时 null。有限数值范围外的派生比率也返回 null，而不是 JSON Infinity。

公式来源：[Minitab 交叉 GR&R 方差分量与百分比](https://support.minitab.com/en-us/minitab/help-and-how-to/quality-and-process-improvement/measurement-system-analysis/how-to/gage-study/crossed-gage-r-r-study/methods-and-formulas/gage-r-r-table/)、[SAS 平衡交叉模型](https://support.sas.com/documentation/cdl/en/statug/66859/HTML/default/statug_varcomp_details10.htm)。本版不做交互显著性检验、自动合并、置信区间或偏倚／线性／长期稳定性分析。独立性、正态残差、同方差、随机化和零件代表性均为未检验，不产生量具合格结论。

### 分类一致性

`kind="agreement"`，列为 `part,operator,trial,label`，可加 `reference`。同一零件的参考标签必须一致；缺参考或只覆盖部分零件，会返回明确覆盖率和状态。

输出每位测量者重复判定一致率、同次重复的测量者间一致率、所有测量者／所有重复全一致的零件比例，以及按 part/trial 配对的两两 Cohen Kappa 描述值。全部只用单一类别、机会一致率为 1 时 Kappa 无定义，返回 null；不把“一直判 OK”当作量测合格。参考准确率仅在提供参考标签的观测中计算，不给未提供真值的数据虚构准确率。

Kappa 使用 `(observed-expected)/(1-expected)`；本版不提供重复观测的显著性检验或置信区间，也不声称等同 Minitab 的完整 Attribute Agreement 流程。解释依据：[分类一致性研究](https://support.minitab.com/en-us/minitab/help-and-how-to/quality-and-process-improvement/measurement-system-analysis/supporting-topics/attribute-agreement-analysis/what-is-an-attribute-agreement-analysis-also-called-attribute-gage-r-r-study/)。一致性与准确率分开，盲测和记忆效应需由研究设计控制。

## 按任务名核查的覆盖情况

以下只按任务名核查，不把旧选型表当成必须实现的技术路线。

| 任务 | 既有路径／本次范围 | 仍需完成或验证 |
|---|---|---|
| Q1 工位级外观检查 | inspection.py、ROI状态和VLM通用路径 | 每工位场景定义、独立实图验收 |
| Q2 读码与OCR | VLM文字／读数模板可复用 | 专用读码/OCR契约与结果校验 |
| Q3 异常检测（只用良品） | anomaly.py良品建库、注册/inspect API | 现场良品覆盖、阈值、独立异常评测 |
| Q4 缺陷检测与分类 | ONNX检测、标注/训练候选链 | 专用质量模型、独立分类与检测验收 |
| Q5 AOI/AXI的AI复判 | 即检可作为图像复核底座 | AOI/AXI结果与图像适配、真值对照 |
| Q6 尺寸与间隙测量 | ROI参考标定不等于计量标定 | 像素尺度/几何标定、测量算法及MSA |
| Q7 涂胶、点胶、三防漆检查 | 通用ROI/即检底座 | 专用数据、检验规则和可见性验收 |
| Q8 连接器针脚与压接针 | 通用检测/即检底座 | 小目标专用能力及误放/误杀评测 |
| Q9 焊缝与焊点外观 | 通用检测/即检底座 | Busbar、极耳等分别采样验证 |
| Q10 装备商结果接入 | fusion.py通用设备信号入箱 | 厂商线扫/X-ray/CT适配及结果溯源 |
| Q11 SPC与质量报表 | 本次导入、I-MR、指数、Pareto、持久导出 | 完整失控规则、数据自动采集与现场统计研究 |
| Q12 视觉系统GR&R/MSA | 本次交叉ANOVA、分类一致性和报告 | 嵌套/不平衡设计、偏倚/线性/稳定性及现场研究 |
| V4 设备点检（读表盘） | 数字/指针表盘VLM提示词模板 | 结构化读数、单位/量程校验及独立评测 |
| V5 首件OK/NG参考图对照 | first_article两步判定与参考图路径 | 现有5/6实测不等于通过；继续独立真值回归 |

测试使用 NIST I-MR 公布例子、可手工计算的交叉设计和分类表，验证计算、无效输入、重开、并发、不可变报告、CSV/JSON、权限与导出。它们是软件验证，不是现场 SPC 或 MSA 结果。
