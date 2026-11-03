# EHS：轨迹驱动的区域入侵与持续烟火

区域入侵、持续烟火已接实时检测与现有安全告警通道。默认关闭，不改变旧工位规则；仅交付这两种执行规则。

## 配置

工位已有 `detector` 必须启用 ONNX，并在 `classes` 中包含所需模型原始类别。区域 ID 使用现有 zones。

```yaml
ehs:
  enabled: true
  max_gap_s: 0.5
  rules:
    - id: restricted-area
      kind: intrusion
      zone: danger
      labels: [person]
      min_s: 1.0
      min_frames: 3
      min_confidence: 0.5
      severity: error
    - id: smoke-fire
      kind: fire_smoke
      zone: workshop
      labels: [fire, smoke]
      min_s: 1.0
      min_frames: 3
      min_confidence: 0.5
      severity: error
```

`intrusion` 用人体框脚底中心落区；`fire_smoke` 用烟火框中心落区。同一轨迹持续满足帧数与时长才告警，区域内持续有目标时只发一次，清空后重新武装。更换个体 ID 不累计前一目标时长。

运行时通过既有 `safety` listener 与安全告警列表发送，附 `source=ehs_tracks`、track_ids、zone、duration_s。状态快照 `ehs.rules` 提供 unknown / clear / pending / alert 与当前帧轨迹 ID。clear 仅表示当前可用检测器没有命中，不是现场安全保证。

## 新鲜度与能力

- 不沿用丢失轨迹。只有 `last_seen == 当前帧时间` 的框可计时；缺一个观测帧即中断该个体持续条件。
- 重复时间戳不累计，时钟倒退或帧间隔超过 max_gap_s 重置。断流/画质暂停使用 runtime 既有 reset hook，状态回到 unknown。
- 未启用跟踪检测器、规则类别不在模型和启用类别交集内，状态为 unknown。MediaPipe 暂无这一轨迹消费入口。
- 烟火告警中的 `area_change_ratio` 是检测框面积相对初始框的变化，只是图像趋势记录，不是火势、温度、物理传播速度。

## 验证

2026-11-03，EHS + 检测/跟踪/质量门禁 41 项测试通过，包含真实 StationRuntime 经 safety listener 发出告警、清缓存、过期框拒绝、不同 ID 不串计时、缺失类别 unknown。

复用已有 `test-videos/FpTmB9IQdVo.mp4` 前 60 帧与仓储 ONNX：整帧演示区域在 0.2 秒产生一次 person 入侵告警，烟火未命中。证据 `.v0/public-models/ehs-replay.json`。这是实际推理接线证据；没有安全真值，不能作为误报/漏报验收。

## 尚未交付

- S2：PPE 人体部位关联与可见性证据。未检测到头盔/手套不等于未佩戴；当前不生成 PPE 缺失告警。
- 人车距离、车速、越线方向：需要校准版本与业务阈值消费者，未用像素替代米或 km/h。
- 前端配置编辑、规则现场验收、独立烟火持续视频真值评测。
- 本次复用的仓储公开权重许可仍未知，保持评估用途；不因规则可运行就改变生产授权状态。

## S2 逐人 PPE 关联

`PPEMonitor` 消费本帧 person 与用品轨迹，按头部/躯干/手部近似区域分配；一件用品只关联一人，多人歧义、正负冲突和小目标均为 unknown。输出按人、按 helmet/vest/gloves 分组：`observed`（正类关联）、`pending`（负类计时）、`missing`、`unknown`，附简短 reason 与 evidence_track_ids。

- `PPECfg.gloves_required=1|2` 按工序选择；重复手套框不计作双手。
- 现仓储模型的正类支持逐人 observed/unknown；未检出用品不产生缺失告警。
- `Without Helmet` 是当前唯一支持的明确负类；必须覆盖足够头部区域、达到置信度、同人持续帧数和时长，才发 `kind=ppe_missing` 的既有 safety 告警。
- 旧 helmet 模型没有 person 类；可开启二阶段头部检测，复用仓储人框提供负类证据。鞋/眼镜没有权重，不提供状态。

6 项专项测试覆盖逐人分配、歧义/冲突、双手计数、明确负类持续、过期证据与重置。复用仓储公开示例实测 1 人，用品证据为空时全部 unknown、缺失告警 0；证据 `.v0/public-models/ppe-example.json`，无佩戴真值准确率评价。

### 头部二阶段探针（默认关闭）

`HelmetROIProbe` 复用旧 `helmet.onnx`：单输入 `[1,3,640,640]`、输出 `[1,6,8400]`、With Helmet / Without Helmet；SHA256 `fa30e26e86c6cc1a1c78678aca272b4ce06bba85b38e3d08cceb1e590c0cc5ab`。加载时检查哈希、metadata 类别和形状，模型许可状态不变。

`process(frame, ts, person_tracks)` 返回 observations、sampled_person_ids、原 frame_ts、status。默认关闭，默认每 0.2 秒最多 2 个 ROI，轮转覆盖人选；重叠头部跳过，输出绑定 parent_track_id。会话首次需要时加载、连续调用复用，空闲 10 秒后在 process/tick 调用中关闭，close/reset 立即释放；没有常驻线程。

此接口是同步底层，不直接在采集循环运行。runtime 使用下述有界异步 worker，按原时间戳、相机 epoch、person ID 与 TTL 消费。

实际仓储示例采到 1 个头部 ROI，冷加载+推理 117.6 ms，没有返回帽子正/负框；只证明真实模型通路可运行，没有未戴帽准确率证据。记录 `.v0/public-models/helmet-roi-example.json`。

### 有界异步接口（已接 runtime）

`HelmetShadow` 默认随探针配置关闭；submit只复制有界图像并替换容量1的最新pending，不等待ORT。结果槽容量1、只取一次；worker首次提交启动，空闲退出并释放session。`drain` 双时钟TTL检查，并校验原epoch、person ID及当前人框重叠，返回原frame_ts/原人框，不写当前帧Context。

```python
shadow = HelmetShadow(helmet_cfg, base_dir, ttl_s=.5, idle_s=10)
samples = HelmetSampleMonitor(ppe_cfg, geoms, ttl_s=.5)
# 质量门禁通过后（epoch 使用已有 _vision_generation）：
shadow.submit(frame, ts, ctx.object_tracks, epoch, captured_mono=capture_mono)
result = shadow.drain(epoch, ts, ctx.object_tracks)
safety_alerts += samples.update(result, epoch, ts, ctx.object_tracks)
# 断流/画质失效：
shadow.invalidate()
samples.reset()
# 停机：检查close返回值，未完成的原生调用不能由Python线程强杀。
closed = shadow.close(timeout_s=.5)
```

独立采样计时器最多64人，只按真实推理样本计数；未采样帧不加次数，其他人轮转不会清掉本人的有效计时，超TTL/人物消失/epoch变化则清空。不要把这个计时器与每视频帧的基础PPE状态混用。

真实仓储示例：提交0.66ms，约121ms后取回原frame_ts=0、person ID=2，帽子正负框仍为0；close成功。证据 `.v0/public-models/helmet-shadow-example.json`。异步协议不等于缺失识别准确率验收。

开启方式：站点配置 `ppe.enabled: true`、`ppe.helmet_probe.enabled: true`；两项默认均关闭。使用站点现有 base_dir 解析模型路径。状态 `helmet_probe` 提供原采样时间、候选状态与独立逐人样本。画质阻断/断流清缓存；stop/EOF 关闭 worker，原生调用仍在运行时报告 `closing`、`close_complete: false`。

运行时测试使用真实队列与模拟检测输出：连续三个真实采样通过既有 safety listener 发出一条原时间戳未戴帽告警；当前 Context 不混入旧帽子框。另覆盖默认关闭、EOF 释放、画质清空和关闭超时。

### PPE 14类的实时消费

PPEMonitor已识别新权重的hardhat/no-hardhat、no-safety vest、gloves/no-gloves、goggles/no-goggles、safety shoes原标签，并按人员相对头/躯干/手/眼/脚区域关联。护目镜和劳保鞋进入gear快照；shoes_required默认2，可设1。小目标、归属歧义、正负冲突和未检出均保留unknown；只对明确负类持续证据报警。该14类模型没有no-shoes，不能靠鞋未检出判断缺鞋，也不推断ESD性能。

使用新模型时，detector.class_names须为清单完整14类顺序，detector.classes须保留相关原名；max_results应覆盖人员及其用品（默认5可能截断用品）。平台未自动替换原生产模型。逐人关联的软件测试不代替护目镜/手套小目标实景评测。
