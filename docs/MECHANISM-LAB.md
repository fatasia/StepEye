# 机理实验室：周期参考偏离与趋势

面向已切好周期的单通道设备信号，导入 CSV 即可生成固定参考偏离、持续越界和短期趋势报告。

- 参考期固定在最早的周期，不用后续异常数据重拟合基线。
- 提供标准差、均值、差分 RMS、95% 偏离与有效特征列表；参考没有变化时输出 unknown。
- 越界须连续满足 hold 个周期。短期线性外推与实际观测分开，不输出剩余寿命或故障根因。
- 按请求计算，无训练、模型下载、后台线程。报告不可变，支持列表、查看与 JSON 导出。

## 输入与接口

CSV 表头必须为 `cycle,value`。同周期行相邻，周期编号连续递增，各周期等长。至少24周期、每周期4–2048点，总计不超过20000行/2000周期/2MB。信号须来自同一设备、同一通道、相同工况与采样设置；调用方声明最早参考周期正常。时间单位为周期，不换算小时或天。

```json
{
  "csv_text": "cycle,value\n0,0\n0,1\n...",
  "title": "电机电流周期",
  "unit": "A",
  "reference_fraction": 0.2,
  "threshold": 6,
  "hold": 3,
  "horizon": 5
}
```

示例省略号不是有效输入；需完整周期数据。参考数量为 `max(int(周期数 × reference_fraction), 20)`，参考期后至少留下 hold 个周期。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/mechanism/capabilities` | 格式与能力范围 |
| POST | `/api/mechanism/analyses` | JSON 中上传 csv_text，返回201及完整报告 |
| GET | `/api/mechanism/analyses?limit=50&offset=0` | 分页摘要 |
| GET | `/api/mechanism/analyses/{id}` | 输入、摘要与逐周期证据 |
| GET | `/api/mechanism/analyses/{id}/export` | JSON下载 |

`result.status` 为 `unknown`、`within_reference` 或 `review`。within_reference 仅表示末尾连续窗口未满足越界条件，不是健康认证；历史首次持续越界仍单独保留。`trend/forecast` 使用末尾最多20个非参考周期作线性外推，至少5个样本且 horizon 不超过拟合样本数；不提供预测置信区间。`rul_cycles` 固定 null。

调用入口：

```python
from visual.mechanism_routes import MechanismReports, router
store = MechanismReports(data_dir / 'mechanism.sqlite')
app.include_router(router(store))
# 应用退出时 store.close()
```

服务挂载方沿用 viewer GET、operator/admin POST；MCP 不自动开放。单进程一次分析，忙返回429；输入错误400/422。每份报告保存输入 SHA256、输入参数、结果、actor。质量报告的 AnalysisBody 限定分析种类，不能直接复用其 create；此处沿用不可变 SQLite 存储模式，不扩质量模块。

## weihu 复用审计

原项目：`D:/Documents/ChatGPT/weihu`，审计提交 `01d77002e960b93254f137a8dd7e618abb12c3ae`。根 LICENSE 为 MIT，Copyright (c) 2026 fatasia；完整许可保留于 `visual/mechanism_core.py`。未导入原管理界面、服务、凭据或模型权重。

| 原文件 | 本次复用 | SHA256（本机原文件字节） |
|---|---|---|
| `code/python/src/phm_core/degradation.py` | cycle_features、reference_z、persistent_crossing | `9ab078d64c9564c7ecd867704828816e0d0d102f3cc7b62be2fe4fa94e50076a` |
| `code/python/src/phm_core/prognostics.py` | life_features 的窗口线性斜率 | `6170a358596a2e9e54b4753f653be3beee804ab7ca4cc78daddf2077ee90b76d` |

以上函数按原代码摘取，只保留所需 NumPy 导入。平台层新增 CSV 验证、绝对参考偏离聚合、结果解释和报告存储。

另审查 `rotating.py`：轴承包络谱需要实际采样率、转速、轴承阶次和带通范围，未接入本次无设备参数的导入接口。`actuator_mechanism.py` 的25Hz指令运动基线针对指定执行器周期，也未套用到任意设备信号。跨设备特征选择、已训练故障分类、校准寿命预测不在此版本内。

## 验证

14项定点测试通过：已知周期特征/斜率数值、固定参考持续越界、常量参考 unknown、非法数值与周期布局、真实 FastAPI CSV→持久报告→导出、重开数据库读取、并发429和数据库不可变保护。测试使用自制确定信号，不是现场诊断精度评测。
