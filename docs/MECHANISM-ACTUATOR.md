# 执行器健康运动残差

用于同工况执行器的离线周期比较。输入25Hz、400点的指令位置mm、跟随误差mm、电流A；明确声明健康的参考记录与评测记录必须分开。结果是误差与电流的残差RMS及相对健康参考比例，不提供故障类别、物理参数或RUL。

实现直接复用 `D:/Documents/ChatGPT/weihu/code/python/actuator_mechanism.py` 的三个函数。MIT，Copyright (c) 2026 fatasia；完整许可保留在 mechanism_actuator_core.py。SG11/poly3对已知指令计算速度/加速度，固定岭回归alpha0.001，仅参考窗拟合。单请求最多10参考窗+20评测窗，无模型和后台线程常驻。

POST `/api/mechanism/actuator/analyses`，GET `/api/mechanism/actuator/capabilities`。报告沿用MechanismReports。调用方须核对采样时钟、单位、健康参考来源；context/source_group为输入声明，不是设备身份认证。指令位置超出参考覆盖范围返回unknown；相同位置范围不保证速度/负载分布相同。没有告警阈值，残差比例不是健康概率。

实测使用已有Cranfield公开记录 [DOI](https://doi.org/10.17862/cranfield.rd.5097649)，本地来源台账标CC BY 4.0。`Normal.mat` SHA256 `5ec388106129477ca190397df93270fa097ba0fee81ffc040fb0bf9cae98c260`。同sin/20kg工况，trainsin20kg1的5个周期作参考，trainsin20kg2的5个周期评测。首窗残差0.586930mm/0.238134A，标准化1.192585/1.035423。两条记录均为公开正常记录；此结果证明协议和运算可运行，不用于估计故障检出率。

请求和结果：`.v0/mechanism-actuator/real-request.json`、`real-result.json`。未知工况、没有健康参考、未来在线因果分析与设备寿命评估不在本模块范围。
