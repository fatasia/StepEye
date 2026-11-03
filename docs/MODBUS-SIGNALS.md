# Modbus TCP 只读信号桥

给设备接入人员：按明确寄存器表读取温度、气体浓度或电阻，转换单位后发送到 StepEye 已有认证信号入口。独立前台运行，不扫描设备、不写PLC、不改生产模型。

- 只实现 FC03 读保持寄存器、FC04 读输入寄存器，使用原生 TCP 短连接。
- 显式配置0基地址、类型、字节序、字序、比例、偏移和有效范围；没有自动猜测。
- 异常、断连、超时、非有限数、越界或设备无效码 → `quality=invalid,value=null`，不伪造0。
- SQLite持久序号，同状态文件只允许一个进程；默认禁用时不打开设备、数据库或线程。

实现：[modbus_signal_bridge.py](../sop-vision/tools/modbus_signal_bridge.py)。仅用Python标准库及项目已有Pydantic，没有安装PyModbus或引入Docker。协议依据 [Modbus Application Protocol V1.1b3](https://modbus.org/docs/Modbus_Application_Protocol_V1_1b3.pdf) 的FC03/04及[官方地址示例](https://www.modbus.org/introduction-to-modbus)。这是小范围客户端，不是完整Modbus协议栈。

## 1. 配置

将以下内容保存为本机 `bridge.json`。地址和值仅为配置示例，必须用设备手册核对；**enabled默认false**，确认后显式改true。

```json
{
  "enabled": false,
  "server": "http://127.0.0.1:8000",
  "station": "sensor-station",
  "token_env": "STEPEYE_BRIDGE_TOKEN",
  "interval_s": 1.0,
  "timeout_s": 2.0,
  "ttl_ms": 5000,
  "mappings": [
    {
      "source": "modbus-device",
      "name": "temperature",
      "host": "192.168.10.20",
      "port": 502,
      "unit_id": 7,
      "function": 4,
      "address": 0,
      "datatype": "int16",
      "byte_order": "big",
      "word_order": "big",
      "quantity": "temperature",
      "unit": "C",
      "scale": 0.1,
      "offset": 0.0,
      "min_value": -50.0,
      "max_value": 150.0,
      "invalid_raw": [32767]
    }
  ]
}
```

| 字段 | 约定 |
|---|---|
| `function/address` | 只能3或4，地址0–65535；设备手册的30001/40001通常对应PDU地址0，不直接填30001/40001 |
| `unit_id` | 显式指定1–255；网关后的设备ID按手册配置，不使用广播0 |
| `datatype` | `uint16/int16/uint32/int32/float32`；16位读1寄存器、32位读2寄存器 |
| `byte_order` | 每个16位字内字节顺序，big或little；Modbus线上寄存器字节默认big |
| `word_order` | 32位值的两个16位字排列，big或little；不自动探测厂商排列 |
| `scale/offset` | 输出=`解码值×scale+offset`；禁止表达式或脚本，scale必须非0有限数 |
| `min_value/max_value` | 转换后规范单位的有效范围，包含端点；越界直接invalid |
| `invalid_raw` | 解码前、完成字节/字序转换后的无符号位模式，例如uint16的65535；不是浮点测量阈值 |
| `quantity/unit` | 温度=`temperature/C`；气体=`gas_concentration/ppm`或`%LEL`；电阻=`resistance/ohm`或`Mohm` |
| `ttl_ms` | 100–600000，必须大于轮询间隔+一次读取超时；串行多映射的实际一轮时间也要纳入现场预算 |

最多64个映射，同一source/name不能重复。气体species、ESD电极／测试方式和设备校准记录需在现场寄存器台账明确，名字不能代替它们。例如 `gas_h2s_ppm` 与 `gas_lel` 不要共用信号名；设备电阻读数不自动等于人员ESD合规判定。

`server`只接受无凭据、路径、查询或片段的HTTP(S) origin，非本机必须HTTPS。不使用系统代理、不跟随重定向，避免把会话令牌转发给别的目的地。Modbus TCP自身不提供本桥所需的设备认证/TLS，应放在受控设备网络；本桥不读取任意网页URL。

## 2. 平台侧注册

工位YAML注册来源，温度可绑定已有热监测规则：

```yaml
signal_sources: [modbus-device]
thermal:
  enabled: true
  rules:
    - id: overheat
      source: modbus-device
      name: temperature
      kind: temperature
      threshold: 60
      min_s: 1
      min_samples: 3
      max_gap_s: 2
```

工位还需其余合法基础配置，不能将该片段直接当完整工位。阈值是示例，不是设备或工艺限值。温度包使用已有契约：

```json
{"temperature": 80.0, "temperature_unit": "C"}
```

气体／电阻使用带单位对象，例如：

```json
{"quantity": "gas_concentration", "value": 50.0, "unit": "ppm"}
```

桥只完成采集和入站，**本批没有新增气体/ESD范围报警规则**。通用信号条件目前按值匹配，不把对象自动转换为数值阈值；温度使用现有ThermalMonitor。温度信号在没有相机帧时也能进入温场观察器，读取失败会使相关状态unknown。

## 3. 认证与启动

平台需启用认证，至少operator权限，且source已在目标工位注册。桥只从 `token_env` 指定的**进程环境变量**读取现有登录会话令牌；不支持内联密码／token，不使用MCP令牌。

PowerShell在本机交互登录、令牌仅留当前进程环境，示例端口须与实际服务一致：

```powershell
$origin = 'http://127.0.0.1:8000'
$credential = Get-Credential
$body = @{ username=$credential.UserName; password=$credential.GetNetworkCredential().Password } | ConvertTo-Json
Invoke-RestMethod "$origin/api/auth/login" -Method Post -ContentType 'application/json' -Body $body -SessionVariable session | Out-Null
$env:STEPEYE_BRIDGE_TOKEN = $session.Cookies.GetCookies([uri]$origin)['vsession'].Value
Remove-Variable body,credential
cd sop-vision
python -m tools.modbus_signal_bridge --config ..\bridge.json --once
python -m tools.modbus_signal_bridge --config ..\bridge.json
Remove-Item Env:STEPEYE_BRIDGE_TOKEN
```

Linux使用同一Python模块，路径换成 `/`，从本机会话管理脚本设置 `STEPEYE_BRIDGE_TOKEN` 后运行即可。不要把实际凭据写进命令历史、仓库或示例JSON。现有登录会话有有效期，401/403使桥停止，需要重新登录；它不是新增长期设备密钥系统。

`--once`仅采一轮；默认前台按配置采集，Ctrl+C/SIGTERM停止，不自动安装或重启服务。禁用配置直接退出0，零设备连接。`--state`可指定序号库，默认配置旁同名 `.state.sqlite3`；跨重启保留该文件，不能删掉后沿用同一source/name。409停止，先恢复原序号库／查平台审计；不会自动重置平台历史。

CLI只输出source/name、sequence、质量、错误码和HTTP状态，不输出token、密码、原始响应正文或测量值。一次模式全部ok且HTTP202退出0；测量/发送失败退出1；配置、认证、来源/序号拒绝等退出2。

## 4. 时序与失败

`observed_at`为读取开始的桥主机Unix时间，不是Modbus设备内部采样时间；故障包记失败观察时间。设备不提供原始时间或更新计数时，桥不能识别“寄存器数值卡死但通讯正常”；此类设备需额外的心跳/质量寄存器接入，本批未实现。

每条包先持久预留序号，再发送认证 `/api/stations/{id}/signals`。允许跳号，重启不会复用已预留序号。202只表示平台接收，不等于规则执行或设备合格。HTTP超时、429/5xx不会紧循环重试旧包，下一轮读取新样本；无法联系平台时，其旧信号依TTL失效。Modbus事务ID、协议ID、unit、长度、功能码和字节数不匹配均按invalid处理；单包上限254字节。

映射串行采集，不启动后台线程。停止请求会在当前有界socket/HTTP调用结束后生效；不要把单独桥进程当实时PLC控制任务。设备名DNS解析依操作系统，现场建议固定设备IP。主机与平台必须校时，平台仍检查未来2秒偏差和TTL。

## 5. 本机实际验证

```powershell
cd sop-vision
python -m pytest -q tests/test_modbus_signal_bridge.py
```

本批 **46 passed**：真实本机Modbus TCP socket模拟器 → 真正`build_app`/Uvicorn登录和Bearer鉴权 → 温场告警落库；未启动相机读取线程。两次80°C产生告警，下一次Modbus异常发invalid并清为unknown；桥重开续用sequence3。另覆盖FC03/04、拆包、超时、异常码、MBAP/字节数错误、类型/字序/缩放、NaN/越界/无效码、跨进程单消费者锁、禁用零连接、viewer403／无效会话401及密钥不回显。模拟器和HTTP服务在测试结束均停止，未访问真实PLC或公网设备。
