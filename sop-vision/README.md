# 视觉实验室 · SOP 合规

纯视觉的工位作业合规监测：不接 PLC、不改产线，用工位摄像头判断**步骤是否做全、顺序是否正确、节拍是否达标**，并为每个工件周期生成可追溯的证据包。

> 状态：**P0 原型**。规则引擎、结果核验、证据追溯、Web 控制台已跑通，并在仿真工位上端到端验证；手部检测与文件视频源已在两段真实视频上冒烟验证（见“已知限制”第 2 条）。**但尚未在真实产线工位上验证 SOP 判定的准确率**，也没有人工标注真值。

## 快速开始

```powershell
pip install -r requirements.txt
python -m visual serve configs/demo_sim.yaml configs/demo_sim_2.yaml --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

演示配置使用**仿真工位**（合成画面 + 脚本化操作员，循环播放“正常 / 漏步 / 错序”周期），用于不接摄像头时体验完整链路。

手部检测模型 `models/hand_landmarker.task` 需要单独放置（真实视频源才需要，仿真不需要）：
<https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task>

### 命令行

| 命令 | 作用 |
|---|---|
| `python -m visual serve <配置...> [--host --port --data]` | 启动服务与 Web 界面（可同时加载多个工位） |
| `python -m visual validate <配置...>` | 校验规则并展示编译后的步骤、前驱与证据说明 |
| `python -m visual run <配置> [--video 文件] [--data 目录]` | 离线回放，逐周期输出 JSON，证据写入目录 |
| `python -m visual calibrate <配置> --zones a,b` | 从当前画面采集结果核验的参考图（先把区域摆到参考状态） |

## 工作原理

```
画面 ─┬─ 手部检测（MediaPipe）──→ hand_enter / hand_exit（含去抖）
      └─ 结果核验（OpenCV，无需训练）──→ obj_state（工件在位 / 连接器插合 / 螺丝在孔 / 标签已贴 …）
                         ↓  原子事件
                SOP 状态机引擎（规则 DSL）
                         ↓
        步骤完成 / 漏步 / 错序 / 超时 / 周期结果（合格·不合格·待复核）
                         ↓
              证据包（关键帧 + 判定依据 + 规则指纹 + 哈希）→ SQLite → Web 界面
```

- **动作证据**（手在区域停留）只统计步骤被激活**之后**的动作，决定顺序与节拍。
- **结果证据**（区域状态）读取当前稳定状态，决定“做对没有”。手遮挡该区域时不更新状态，避免误判。
- **看不清就不判错**：模糊 / 过曝 / 相机位移期间出现的违规，周期结果降级为“待复核”，而不是“不合格”。
- 时间完全由事件时间戳驱动，可回放、可测试。

## 规则 DSL（YAML）

完整示例见 [configs/demo_sim.yaml](configs/demo_sim.yaml)。

```yaml
zones:                      # 归一化坐标 0~1，与分辨率无关
  - {id: connector_J1, polygon: [[0.40,0.28],[0.52,0.28],[0.52,0.42],[0.40,0.42]]}

roi_states:                 # 结果核验：现场标定一次参考图即可
  - {zone: connector_J1, mode: template, positive_state: mated, negative_state: unmated}
  # mode: diff_empty（与空区域做差，判断有无工件）| template（与“正确状态”做模板匹配）

cycle:
  start: {obj_state: {zone: fixture, state: present, min_ms: 1000}}
  end:   {obj_state: {zone: fixture, state: absent,  min_ms: 800}}
  takt_s: 14

steps:
  - id: S1
    name: 取线束
    timeout_s: 8
    evidence: {all: [{hand_dwell: {zone: bin_harness, min_ms: 300}}]}
  - id: S3                  # foreach 展开为 S3.B1、S3.B2；order: strict 表示必须按序
    name: 锁付螺丝
    foreach: {var: hole, values: [B1, B2], order: strict}
    evidence: {all: [{obj_state: {zone: "${hole}", state: in, min_ms: 300}}]}
```

- 条件：`hand_dwell`（手停留）、`obj_state`（区域状态）、`signal`（外部信号，如 `sn` 序列号）。
- 组合：`all`（全部满足）/ `any`（任一满足）/ `seq`（依次满足）。
- 步骤：`after` 指定前驱（默认紧跟上一步，`[]` 表示可并行起步）；`optional` 可选步骤不阻塞后继。
- 违规严重度默认：漏步、错序为 error（不合格）；步骤超时、周期超节拍为 warn（不影响结果）。

## 证据与追溯

每个周期写入 `data/evidence/<周期号>/`：关键帧（周期开始 / 每步完成 / 违规 / 周期结束）与 `evidence.json`（结果、步骤耗时、违规、规则指纹、证据哈希），同时入库 `data/sop.db`。界面“追溯”页可按工位 / 结果 / 序列号检索，查看步骤甘特图与证据图。

> 证据哈希只能发现篡改，尚无数字签名；长期保存与清理策略未实现。

## HTTP / WebSocket 接口

| 路径 | 说明 |
|---|---|
| `GET /api/stations` · `/api/stations/{id}/state` · `/config` | 工位列表、实时状态、规则与区域 |
| `GET /api/stations/{id}/stream.mjpg` · `/snapshot.jpg` | 实时视频流 / 单帧 |
| `POST /api/stations/{id}/calibrate` | 采集参考图（仿真工位返回 400 并说明原因） |
| `GET /api/cycles` · `/api/cycles/{id}` · `/frames/{name}` | 周期记录、详情、证据图（含路径穿越防护） |
| `GET /api/stats` · `/api/trend` | 今日统计、按小时分布 |
| `WS /ws` | 事件与状态推送 |

服务默认只监听 `127.0.0.1`，**当前没有登录与权限**，对外开放前需要先补认证。

## 开发与测试

```powershell
pip install -r requirements-dev.txt
python -m pytest                       # 引擎 / 仿真端到端 / API / 手部冒烟
python tools/shoot.py http://127.0.0.1:8000 out_dir      # 多断点截图（需先启动服务）
python tools/interact.py http://127.0.0.1:8000 out_dir   # 抽屉 / Esc / 主题 / 复制 等交互验收
python tools/hand_probe.py 视频.mp4 out_dir 1280 640    # 真实视频上的手部检出统计 + 标注抽帧
```

测试素材（不在仓库内，均为 YouTube 的 CC BY 视频，自行下载复现）：
`vD88EyXss6Y`（Urban Hush，Electronics assembly line）、`FpTmB9IQdVo`（Sounds of Changes，Gigaset 工位）。
下载需要 Node 作为 JS 运行时：`yt-dlp --js-runtimes node --remote-components ejs:github -f "bv*[height<=720]" <视频地址>`；使用时请遵守署名要求。

界面规范见 [docs/UI-SPEC.md](docs/UI-SPEC.md)（令牌、状态词汇、组件、动效、页面线框）。

```
visual/            Python 包：dsl 规则 · engine 状态机 · perception 感知 · evidence 证据 · server / cli
web/               无构建的静态前端（ES modules + CSS 令牌）
configs/           工位配置
tests/  tools/     测试与界面验收脚本
```

## 已知限制（请在评估前阅读）

1. **没有在真实产线视频上验证准确率。** 仿真里的结果不能外推。上线前必须按“影子模式”先只记录不动作，统计漏检率与误报率。
2. **手部检测在真实视频上大体可用，但会漏检“拿着物体的大手”。** 在两段 CC BY 授权的真实视频上实测（电子装配线 720p 33 秒；人机协作工位 720p 13 秒；均不在仓库内，来源见下）：
   - 至少检出一只手的帧占比约 83–88%，CPU 上约 17 ms/帧；
   - 画面宽度从 1280 缩到 320，结果几乎不变（平均手数 1.28–1.39），所以**分辨率不是主因**；
   - 看标注抽帧：背景里较小的手大多框对了，但**前景里握着电路板的大手、失焦的手有漏检**——手被物体遮挡时尤其明显。
   - 注意：这里的百分比是“检出了至少一只手的帧占比”，**不是召回率或准确率**（没有人工标注真值）。手被漏掉会影响“手停留”类步骤证据；去抖已容忍 4 帧以内的短暂漏检，更长的需要靠结果核验或手物交互特征补充。
3. 结果核验是**模板 / 差分**方法：对光照剧变、反光件、外观差异大的来料敏感，需要现场标定参考图；标定向导、区域编辑尚未提供（界面中已禁用并说明）。
4. 视觉只管“动作 / 顺序 / 外观”，**测不了扭矩、压入力、封闭件内部、导通**。这类关键特性仍需要拧紧系统等设备数据。
5. 没有登录权限、证据签名、长期保存策略、多相机融合、外部信号（扫码 / 扭矩枪 / PLC）接入。

## 许可说明

- 本项目代码许可待定。
- MediaPipe 及其手部模型为 Apache-2.0。
- 视频与图像数据集的许可需单独确认，**商用前请核对每个模型与数据集的许可**（例如 Ultralytics YOLO 为 AGPL-3.0，MVTec AD 数据集禁止商用）。当前依赖不含这两者。

## 部署与运维(工业级)

```powershell
# 认证模式(生产必开):先建管理员,再启动
python -m visual passwd admin --role admin
python -m visual serve configs\工位.yaml --auth --inspection configs\inspection.yaml
# 影子模式(上线验收强制):判定照跑,结果只进 shadow.db,不进正式统计
python -m visual serve ... --shadow
python -m visual shadow-report --data data     # 漏检/误杀/待标注
# 定点相机自动上传:文件名前缀=场景id
python tools/cam_uploader.py --server http://127.0.0.1:8000 --watch D:\camdrop
# Windows 打包:deploy\build_windows.bat(产物 dist\visual-server\)
```

- **认证**:`--auth` 或 `VISUAL_AUTH=1`;三级角色 viewer(只读)< operator(操作)< admin(用户管理);
  REST 全量拦截,WS 未认证拒连;会话 12h,Cookie HttpOnly。
- **WS 鉴权**:认证启用后 `/ws` 校验 Cookie 或 `?token=`(脚本用 Bearer 登录换取)。
- **飞书推送**:环境变量 `FEISHU_WEBHOOK_URL` 配群机器人 webhook,告警创建即推、回写标记防重。
- **影子模式纪律**:真值标注完成前不向客户报任何准确率数字(README 已知限制 #1 的制度化解法)。
- **微调管线**:见 docs/TRAINING.md(样本闭环→YOLO 格式数据集→D-FINE/RF-DETR→回归闸门→上岗)。

## 流媒体接入(选型:Monibuca 优先)

| 形态 | 何时用 |
|---|---|
| 直连 RTSP(现状) | 单工位/试点,CvSource 直接拉流分析 |
| **Monibuca**(推荐) | 多路预览(WebRTC ~0.5s)+ 录像(R3)+ ONVIF 发现取流 + GB28181——一个 Go 二进制全包,与 visual-server.exe 并排部署 |
| ZLMediaKit + wvp-pro | 国标生产重栈兜底(设备目录经纬度经 `geo.sync_from_device_list` 进 GIS) |

ONVIF/GB28181 的发现与坐标读取已平台化:`/api/cameras/onvif/*`、`/api/cameras/gb28181/sync`。
Monibuca 插件许可(MIT/商业混合)启用前核实现仓;ONVIF 真机联调待验证(已标注)。
