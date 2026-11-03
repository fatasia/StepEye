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
