# 真景重建与动作理解选型

2026-11-03：回答 NeRF、3DGS、VideoMAE、TimeSformer 对 StepEye 的用途。空间重建仍是候选；T32 已接动作分类 ONNX 异步影子旁路，尚无合格动作权重与模型性能验收。

## 建议

| 方向 | 候选 | StepEye 的用途 | 顺序 |
|---|---|---|---|
| 真景空间 | 3D Gaussian Splatting | 可漫游的真实车间背景，叠加设备、区域、告警与巡更点 | 优先一个工位的小范围 PoC |
| 真景空间 | NeRF | 多视角新视角合成、真景重建研究 | 备选，不与3DGS同时铺开 |
| 时序理解 | VideoMAE V2 小模型 | 取料/装配/翻包等动作特征与分类 | 有现场片段和真值后验证 |
| 时序理解 | TimeSformer | 时空注意力动作分类的对照方案 | 不作为新默认底座 |

这些优先级是针对本项目目标的工程判断，不是四种技术的通用排名。

## 两条互不替代的链

**空间展示**：多视角采集 → 离线重建 → Web查看器 → 显式坐标配准 → 设备与区域锚点 → 实况/告警跳转。

重建资产记录采集日期、坐标变换、已知尺寸/控制点与版本。场景是采集时的外观，实时状态来自 StepEye 的独立数据层。3DGS/NeRF 不会自动产生设备语义、工程尺寸、碰撞网格或实时孪生；测量/BIM需求仍使用经标定的几何资产。玻璃、反光、遮挡和移动目标的重建效果需用本车间数据确认。

Windows/Linux 继续共用 Web 前端；CUDA 重建放隔离的可选离线 worker，不把 torch/CUDA 塞进默认视觉服务。浏览器渲染器、文件体积、显存和加载预算要另做真实资产测试；失败时回到现有二维/立体示意。

**动作判定**：现有人员/物体定位 → 有界视频片段 → 动作模型 → 带时间窗/版本/置信与有效期的事件 → 状态机+设备信号。

VideoMAE主要是视频自监督预训练方法，TimeSformer主要是时空注意力架构；名字不能直接当工厂动作能力。预训练分类表不等于我们的工步表；需要动作片段、类别定义、时序边界、困难负样本和跨班次独立验证。动作分类与动作起止定位是两个验收项。

模型仅提供动作证据：顺序/超时仍由状态机判，扭矩、扫码结果仍来自对应设备。实时路径不得使用未来帧；结果到达时必须校验窗口、画质与工位代际，旧结果不能推进当前周期。

首个动作试点只选3–5个定义清楚的工步，按独立拍摄批次/人员/班次划分数据，报告逐类漏检、误报、UNKNOWN和端到端延迟。光照增强不替代真实昼夜验证。

## 许可核查

截至本次检查，仓库代码许可如下；模型权重、依赖和训练数据需另立条目，不能用代码许可代替。

| 实现 | 仓库许可/状态 | 处理 |
|---|---|---|
| [原版VideoMAE](https://github.com/MCG-NJU/VideoMAE/blob/main/LICENSE) | CC BY-NC 4.0 | 不直接并入商用默认交付 |
| [TimeSformer](https://github.com/facebookresearch/TimeSformer/blob/main/LICENSE) | CC BY-NC 4.0；[仓库](https://github.com/facebookresearch/TimeSformer)2025-01-01归档 | 技术对照，不直接并入默认交付 |
| [VideoMAE V2](https://github.com/OpenGVLab/VideoMAEv2/blob/master/LICENSE) | MIT | 可优先核查具体小模型及其权重/数据来源，不视为整条链已获授权 |
| [原始3DGS实现](https://github.com/graphdeco-inria/gaussian-splatting/blob/main/LICENSE.md) | 研究/评估非商用条件 | 不直接选作商用默认实现 |
| [gsplat](https://github.com/nerfstudio-project/gsplat/blob/main/LICENSE) | Apache-2.0 | 作为3DGS候选，依赖、查看器和资产分别核查 |

原理来源：[NeRF](https://www.matthewtancik.com/nerf)、[VideoMAE](https://github.com/MCG-NJU/VideoMAE)、[VideoMAE V2](https://github.com/OpenGVLab/VideoMAEv2)、[TimeSformer](https://github.com/facebookresearch/TimeSformer)、[gsplat](https://github.com/nerfstudio-project/gsplat)。本轮未为这些候选新增运行依赖或下载权重。

## T32 动作识别旁路（默认关闭）

排在 T28 的片段真值准备、T30 信号融合和 T31 画质门禁之后，不阻塞预览、告警、自动标注或 MCP。T28 已确认的单图框不能当动作标签；复用时间轴另标开始、结束、动作、人员和原视频分组。

1. 工位常驻轻量定位与画质检查。只为启用动作的工位和固定操作区保留近16张缩小帧；只存过去的帧，记录单调采集时间、工位代际、周期与跟踪对象。先缓存再触发，避免开始判别时才从零攒帧。
2. 只在相关工步窗口或人员进入操作区时调度。首次触发才启动模型，短时复用以避免每个窗口重复加载，空闲超时释放模型和工作进程；每工位最多一个待处理窗口，新窗替换旧窗，跨工位轮流取任务。采集线程不等待推理、队列容量或GPU恢复。
3. MVP只测蒸馏 Small、16帧、224×224、batch=1；8Hz采样、每500ms最多提交一次作为初始配置，不是性能实测值。固定ROI必须同时看得到手、零件和工装。周期、人员身份、画质或帧间隔失效时清空窗口；未取得足够连续证据则未知。
4. 输出动作、置信度、片段起止时间、工位/周期/对象/代际、模型版本。采集线程在批次求值前核验身份、TTL与当前画质；过期、遮挡、模型退出或低置信输出未知，禁止沿用上一次结论。重叠窗口去重，视觉不能代替PLC/扭矩枪真值。
5. 第一阶段仅影子记录。对同一段回放比较开/关旁路，分别记录采集循环P95、丢帧、队列等待、末帧至结果P95、动作开始至首次正确识别延迟、吞吐和峰值显存。初拟门槛：单路末帧至结果P95≤500ms，采集循环P95增幅≤5%；同时验收按事件的漏报、误报、未知率及现场节拍。超预算时降低调度频率或停用旁路，不阻塞主链。多路容量按目标工位数实测，不从单路FPS推算。

## 配置与按需运行

工位 YAML 的 `action_shadow` 默认关闭。管理员配置本机分类 ONNX、类别顺序和模型 SHA256 后才能试运行：

```yaml
action_shadow:
  enabled: true
  model: models/action.onnx
  class_names: [take, place, other]
  model_sha256: "<批准模型的64位SHA256>"
  roi: [0.1, 0.1, 0.9, 0.9]
  min_conf: 0.7
  idle_s: 60
  providers: [CPUExecutionProvider]
```

`trigger_zone` 可限定已定义区域，`active_steps` 可限定已有工步；缺省要求画面中只有一个可连续关联的人员框。IoU 连续性令牌不是身份识别。状态通过工位 `action_shadow` 字段查看，不产生 SOP 证据或生产判定。

关闭功能不创建动作线程或加载模型。启用后按需启动共享串行 worker，空闲默认 60 秒退出并释放模型；再次触发计入冷启动。拍照即检、自动标注、VLM 请求的工作线程在队列空时退出；预览租约回收线程只在存在会话时运行。实时工位采集和主 Web 服务仍按服务生命周期运行，外部 Monibuca 的进程生命周期由部署管理。

实际输入契约、单次固定形状测量方法与缺项见 [动作性能检查](ACTION-BENCH.md)。
