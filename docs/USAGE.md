# StepEye 使用说明

从装好到日常用起来，按实际操作顺序写。截图来自真实界面（1600×900，暗色主题）。

## 1. 装起来

### 安装包

双击 `StepEye-Setup-0.9.0.exe`，装到 `%LOCALAPPDATA%\StepEye`，开始菜单会出现 StepEye。打开后自动启动演示工位，浏览器访问 <http://127.0.0.1:8000>。

演示工位是仿真画面，不需要摄像头，用来熟悉界面和流程：取线束 → 插连接器 → 锁 B1/B2 两颗螺丝 → 贴标签，四个场景循环播放（正常、漏步、正常、顺序错）。

### 便携包

解压 `stepeye-runtime-windows.zip` 到任意目录：

```powershell
.\stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --port 8000 --data data
```

### 源码

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m visual serve configs/demo_sim.yaml --port 8000
```

源码模式默认没有装条码依赖，需要读码时补一句 `pip install zxing-cpp`；OCR 用的 RapidOCR 已在 `requirements.txt` 里。

### 数据放哪

`--data` 指定的目录（默认 `data`）就是全部业务数据：SQLite 库（告警、追溯、评测、发布、文档索引、账号）、标注图片、文档原件、设备结果、证据快照。迁移或备份就是拷这个目录，注意先停服务，`.db-wal` 要一起拷。

## 2. 登录与角色

不带 `--auth` 时不需要登录，适合单机试看。正式使用建议开启：

```powershell
python -m visual passwd admin --role admin --data data     # 交互式输入密码
python -m visual serve configs/demo_sim.yaml --port 8000 --auth --data data
```

| 角色 | 能做什么 |
|---|---|
| viewer | 看监控、告警、追溯、报告、文档 |
| operator | 处置告警、标注、导入盘点期望值、发起评测、试推模型 |
| admin | 用户管理、模型审批/激活/回滚、AI 服务配置、配额、治理参数 |

左侧导航按任务分组（运行监控 / 视频监控 / 质量视觉 / 数据知识），当前页所在分组自动展开。

## 3. 接第一路相机

工位配置是一份 YAML，`source` 决定画面从哪来：

```yaml
# RTSP 相机
source: {type: rtsp, uri: "rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101", fps: 25, width: 1280, height: 720}

# 本地视频回放
source: {type: file, uri: "D:/videos/line1.mp4", fps: 25}

# USB 相机
source: {type: camera, index: 0, fps: 25, width: 1280, height: 720}

# 仿真（演示用）
source: {type: sim, fps: 25, realtime: true, loop: true, width: 640, height: 480, scenarios: [ok, missing, ok, wrong_order]}
```

先校验配置能不能编译：

```powershell
python -m visual validate configs/my_station.yaml
```

校验通过再启动。多个工位可以一次传给 `serve`：

```powershell
python -m visual serve configs/st01.yaml configs/st02.yaml --port 8000 --data data
```

相机地址写在 YAML 里，平台不做 ONVIF 自动发现。「视频巡防 → 厂区图」是把工位和设备放到平面图上，看点位状态和关联事件，不负责拉流。

## 4. 建一个工位

### 4.1 画区域

打开「数据知识 → SOP 配置」，左边是画面，点「编辑区域」直接在图上拉框。区域 id 就是后面规则里引用的名字，比如 `fixture`（工件位）、`bin_harness`（料盒）、`label_zone`（标签位）。

区域坐标是归一化的（0–1），换分辨率不用重画。

### 4.2 标定参考状态

`template` 和 `diff_empty` 两种核验方式需要一张参考图：把区域摆成目标状态，拍一张。

```powershell
python -m visual calibrate configs/my_station.yaml --zones fixture,connector_J1 --data data
```

参考图存在 `data/` 下，后续每次核验都和它比。工位换位置、换镜头、换光照，要重新标定。

### 4.3 写步骤

右侧「步骤规则」里每一步由三部分组成：

```yaml
steps:
  - id: S1
    name: 取线束
    timeout_s: 8
    evidence: {all: [{hand_dwell: {zone: bin_harness, min_ms: 300}}]}

  - id: S3
    name: 锁付螺丝
    foreach: {var: hole, values: [B1, B2], order: strict}
    evidence: {all: [{obj_state: {zone: "${hole}", state: in, min_ms: 300}}]}
```

- **evidence**：证据条件。`hand_dwell` 手在区域内停留，`obj_state` 区域状态，`all` 全部满足，`any` 任一满足。
- **foreach**：同一步骤对多个对象重复，`order: strict` 表示必须按顺序。
- **timeout_s**：单步超时，超时按 `violations` 里的策略处理（warn / 记异常）。
- 步骤可以有前置步骤（`after`），用来表达"A 做完才能做 B"。

循环的起止由工件状态触发，不是按时间切：

```yaml
cycle:
  start: {obj_state: {zone: fixture, state: present, min_ms: 1000}}
  end:   {obj_state: {zone: fixture, state: absent,  min_ms: 800}}
  takt_s: 14
  grace_ms: 1500
```

### 4.4 安全规则

安全规则和 SOP 步骤相互独立，常驻生效：

```yaml
safety:
  enabled: true
  rules:
    - {id: unattended, kind: person_absent, zone: post, min_s: 300, severity: warn, name: 无人值守}
    - {id: crowd, kind: person_count, zone: post, count_ge: 3, min_s: 10, severity: warn, name: 人员聚集}
```

`person_absent`、`person_count`、`object_present`、`fall` 是内置类型，PPE、烟火、距离/限速、睡岗在「SOP 配置 → 安全防护」里按开关配。

### 4.5 上线

保存后工位热加载，不用重启。确认三件事再交付：

1. 「实时监控」里画面正常、区域框位置对；
2. 手伸进料盒，S1 变绿；
3. 故意漏一步，告警中心能收到。

新工位建议先跑一天影子模式（`serve` 加 `--shadow`），只记录不告警，第二天看报告再决定要不要开告警。

## 5. 每天要看的三块

### 总览

工位在线数、今日周期、合格率、平均节拍、异常周期，下面是工位画面和最近告警。一眼判断"今天这条线正不正常"。

### 实时监控

![实时监控](assets/ui-live.png)

左边画面叠加区域和手部框，右边是步骤轨迹（每步耗时、状态）、违规、区域状态、观测质量（模糊度、亮度、背景可用像素、有效设备信号）。

画面卡住或质量门禁不过时，步骤会停在原地而不是误判完成——这是有意的。

### 告警中心

按状态（待处理 / 处理中 / 已处理 / 误报）、来源、工位筛选。每条告警可处置、可标误报、可加备注。

底部「影子真值标注」用来人工复核系统判定：标 PASS / NG，样本进影子库，后续算漏检率和误杀率。

右上「治理设置」可以在线调同键冷却时间（0–3600 秒），保存后立刻生效并落盘，重启还在。页脚的预算拦截、冷却拦截、风暴抑制计数只在有值时显示。

## 6. 追溯与录像

![追溯](assets/ui-trace.png)

「追溯」按周期列：开始时间、工位、周期号、结果、耗时、违规类型。上方是节拍时序和合格分布，能看出"从几点开始变慢"。

点任一周期进详情，逐步看证据：哪一步、什么证据、什么时候满足、当时的画面。

录像回放需要配置 Monibuca 录像目录：

```powershell
$env:MONIBUCA_RECORD_DIR = "D:\monibuca\recordings"
```

启动后「视频巡防 → 录像」按时间检索，浏览器直接播放（支持 Range 拖动）。

## 7. 质量工具

都在「视觉应用 → 评测与质量工具」里。

### 拍照即检

上传或触发拍照，VLM 按场景模板判定。场景模板在 `configs/inspection.yaml`，模板库见 `configs/templates/inspection-templates.yaml`（手套、PPE、烟火等）。

需要先绑 VLM：设置 → AI 服务里填服务地址和密钥，或 `.env` 里配 `ZHIPU_API_KEY`。没配时页面显示加载失败，并提示缺哪一项。

判定结果只有 PASS / NG / UNCERTAIN 三态，UNCERTAIN 表示画面不足以下结论——这个不算合格，也不算不合格。

### 读码与 OCR

「评测与质量工具 → 分析任务」里选条码或 OCR，上传图片直接出结果。条码/二维码用 zxing-cpp，字符用 RapidOCR，本地 CPU，不联网。

OCR 仍有字符错识，结果需要人工复核，不要直接当 SN 或合格依据。

### 画面标定与表盘

「评测与质量工具 → 画面标定」标定工件平面（mm）或地面平面（m）：在图上点几个已知坐标的点，填实际值，再加验证点，误差在容差内才允许保存。标定版本只对那个平面和覆盖区有效。保存后可以在这个版本上量尺寸：上传同一画面的图片，拖两个只含一条直边的选区，输出两点间的物理距离（只测标定平面内的垂直间距）。

表盘读数在「指针表盘试测」，先框表盘中心和量程。

### SPC

「分析任务」里选 SPC / 过程能力，上传 CSV（表头 `value`，按采集顺序）出 I-MR 控制图和 Cp/Cpk。缺陷柏拉图用表头 `defect,count`，GR&R 用 `part,operator,trial,value`，复判一致性用 `part,operator,trial,label`。控制限按数据算，不预设。

### 设备结果复判

扭力枪、称重、测试机的结果通过 Modbus 或 HTTP 推进来，和视觉判定并排显示。两边不一致的周期会单独标出来，供工艺复核。

### 盘点对账

三步：

1. 导入期望库存 JSON：`[{"zone":"A1","item":"box","count":12}]`，整批替换并留版本；
2. 录入当前可见计数（来自单机位计数快照）；
3. 看差异表：缺 / 多 / 账外 / 未观测。

报告不可变、可导出、留历史。这是单机位可见计数对账，跨机位去重没做。

## 8. 模型：从试推到上线

### 试推

![视觉应用](assets/ui-models.png)

「质量视觉 → 视觉应用」左边选模型，右边传图，点「试一张」。结果给检测框、类别、置信度，以及本次端到端耗时（含解码和后处理）。没检出会直说"未检出目标"，不猜。

当前登记 18 个模型，覆盖检测、分类、分割。想接自己的模型，把 ONNX 放进 `models/public/`，在对应 manifest 里登记 id、类别、SHA256、适配器类型。

### 标注

- 样本库：导入图片或 YOLO ZIP，人工复核后导出数据集版本。
- 时间轴标注：视频按帧标，适合动作和时序场景。
- 从试推直接建项目：模型和类别自动带入。

数据集按来源组隔离（同一段视频的帧不会同时出现在训练和验证），导出时算 SHA 指纹，冻结后不可改。

### 评测

「评测与质量工具 → 固定模型评测」：选模型 + 选冻结数据集版本，跑逐图 FP/FN，出 Precision / Recall / F1 和 COCO 12 项 AP/AR。CPU 跑，报告不可变、可导出。

报告会标明数据来源，比如"导出 val 上的验证"，看结论时先看这一行。

### 发布

评测通过不等于上线，发布是独立流程：

1. 从评测报告创建候选；
2. admin 审批；
3. 激活到目标工位，工位重载后生效；
4. 需要时回滚到上一个已激活版本。

模型文件带签名，激活时校验 SHA，对不上拒绝生效。

### 精度监测

激活后的模型按批次持续比对，退化超过阈值会在「发布管理」里提示。自动回滚没做，回滚要人点。

## 9. 机理实验室

从设备信号出发，不靠画面：

- **轴承**：振动信号包络谱 + 谐波证据，判断内圈/外圈/滚动体/保持架。
- **电机**：电流特征 + 随机森林，10 类故障。
- **执行器**：ONNX 集成，正常 / 背隙 / 缺润滑 / 剥落。
- **阀门**：冻结融合模型，信号 + 动作双证据。
- **剩余寿命**：C-MAPSS 与 FEMTO 退化相似性，趋势级。

所有报告不可变，可查历史、导出 JSON。证据不足时输出 unsupported，不给凑出来的分数。

## 10. 文档与问答

「数据知识 → 知识与文档」上传 SOP、作业指导书、设备手册，支持版本管理。文档可以编译成步骤草稿，人工确认后写进工位 YAML。文档改动不会自动改工位。

「文档中心」是全部技术与使用文档的入口，viewer 也能看。

## 11. 设置与运维

「设置」分六块：

| 面板 | 内容 |
|---|---|
| 通用 | 站点名、时区、界面偏好 |
| 用户 | 账号、角色、启用/停用 |
| AI 服务 | VLM / 大模型地址与密钥，测试连通 |
| 通知 | 邮件、飞书、企业微信、钉钉渠道与模板 |
| 操作日志 | 业务写请求的账号、路由、响应状态（UTC） |
| 运行状态 | 健康接口、已配置工位 |

存储配额用环境变量配，键是目录名、值是字节上限：

```powershell
$env:STEPEYE_STORAGE_LIMITS_JSON = '{"labeling": 21474836480, "documents": 5368709120}'
```

超限前会在页面上提示。磁盘水位统计包含标注图片、文档、证据，不含外部录像目录。

### 升级

```powershell
python -m visual upgrade --manifest release.manifest.json `
  --current C:\StepEye --staging C:\StepEye-new --backup C:\StepEye-bak
```

升级器拒绝降级和同版本、拒绝绝对路径和符号链接、解压上限 8GB、SHA256 不符即中止。切换分两步：current → backup，staged → current；第二步失败自动回滚。

Windows 安装包直接覆盖安装也行，`data\` 会保留。

## 12. 命令速查

```powershell
python -m visual serve configs/a.yaml configs/b.yaml --port 8000 --data data   # 启动
python -m visual serve configs/a.yaml --shadow                                 # 影子模式
python -m visual validate configs/a.yaml                                       # 校验规则
python -m visual run configs/a.yaml --video test.mp4 --data out                # 离线回放
python -m visual calibrate configs/a.yaml --zones fixture                      # 标参考图
python -m visual passwd operator --role operator --data data                   # 建账号
python -m visual shadow-report --data data --since 2026-11-01                  # 影子报告
python -m visual dataset-import --src raw/ --out dataset/ --classes classes.txt  # 导入标注
python -m visual regress manifest.yaml --against last.json                     # 评测回归
python -m visual upgrade --manifest m.json --current . --staging s --backup b   # 升级
```

## 13. 故障排查

**页面打不开**：先看 `http://127.0.0.1:8000/api/health` 是否 200；端口被占就换 `--port`。

**画面黑屏**：RTSP 地址在本机用 ffplay 试一次，确认账号密码和子码流路径；相机改过密码要同步改 YAML 再重启。

**步骤不推进**：看右侧「观测质量」——模糊度、亮度、背景可用像素。质量门禁不过时步骤会停，先解决画面。

**告警太多**：先开同键冷却（告警页治理设置），再看规则阈值是不是按演示参数配的；不同工位要分开调。

**模型试推报错**：确认 ONNX 文件在 `models/public/` 且 SHA 和 manifest 一致；不一致会被拒绝加载。

**拍照即检加载失败**：没绑 VLM。设置 → AI 服务里配，或 `.env` 写 `ZHIPU_API_KEY`。

**升级失败**：旧版本仍在原目录，备份目录是 `<安装目录>.bak-<时间戳>`，停服务后改回原名即可。

**换了机器**：拷 `data/` 目录 + 工位 YAML + `models/`，重新标定参考图（相机和光照变了）。
