# C-MAPSS FD001 RUL 研究资产

来源：`D:/Documents/ChatGPT/weihu/outputs/rul-study/runs/20261031T072751Z-2c94fd86`，训练代码 `code/python/study_rul.py`。冻结模型 SHA256：`e859f24836a4a40a35d3c45b34d7646c14eb88223e80f04051740cab3f3c252c`。

输入为每台模拟涡扇发动机连续30个运行周期，三个工况设定及21个传感器，通道顺序与原始单位见 manifest。训练得到的特征聚合+ExtraTrees回归图直接输出剩余周期；不是线性趋势外推，也不是通用工业电机寿命模型。输出裁剪到0–125 cycles；区间半径40.3986由15台独立校准发动机得到。超过125的区间上界保留；不换算日期或天数。

源项目代码 MIT，完整许可见 LICENSE-weihu.txt；数据 NASA C-MAPSS FD001，来源 https://catalog.data.gov/dataset/cmapss-jet-engine-simulated-data 。模型为源项目本地自训资产，未发现单独权重发布许可声明，当前仅研究评估。模型未签名，SHA只能确认冻结内容，不证明发布者身份。部署可信公钥必须在包外配置，验签失败拒绝推理。

已存在的电机 RF `held-out-3000rpm-42` 模型 SHA `b1ad28926bce9ad3fda1268c4197754164cad31ce1f964ee6e82991af14e9c14`，输入114工程特征；作者记录3000rpm迁移失败，本批未上线。执行器/RUL跨设备泛化仍缺证据。

软件验收复用了源包 parity.npz 第一窗口：预测111.94605 cycles，原参考111.94611，区间[71.54744,152.34466]。parity没有保存该窗的真实unit/end_cycle，测试使用明确fixture身份和窗口末端序号30；这是推理一致性，不是新的RUL准确率评估。

## 平台入口与验签

机理实验室 → 专域寿命评估 → 导入 JSON。分析保存到现有机理报告库，可查询和导出；不会进入实时检测或维护控制链路。单次进程限时30秒，结束即释放。

默认研究模式保留“未签名”状态。需强制验签时，部署环境设置 `STEPEYE_RUL_TRUST_KEYS` 为外部可信 Ed25519 PEM 公钥绝对路径的 JSON 数组，例如 `["D:/trusted/release.pub.pem"]`。公钥不能来自模型包内部；空集合或签名不匹配均拒绝推理。签名文件为模型目录内 `signature.json`，签名覆盖目录文件清单。安装 `requirements-mechanism.txt` 提供验签依赖。此开关仅作用于本 RUL 服务，不代表全平台模型均已启用签名门禁。
