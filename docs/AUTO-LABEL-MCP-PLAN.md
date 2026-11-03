# 自动标注与 MCP 接入方案

给 StepEye 的现场用户做一个“上传、检查、确认”的标注工作台；给外部智能体提供受控工具，不替换现有平台和检测模型。

- 自动预标注先试两张，用户认可后再批量；无目标、失败和待复核分开显示。
- 机器建议与人工真值分开存，已确认标注不被重跑覆盖。
- 图片、视频抽帧、样本库和影子误报共用一个标注入口。
- Windows / Linux 原生服务与统一 Web 界面；GPU 推理可选，不强制 Docker。
- MCP 先让外部智能体读数据、建草稿；确认真值、训练花费、发布模型仍需明确授权。

**实施顺序：T28 标注工作台 → T29 MCP；多源融合 T30、光照治理 T31 提供数据质量与上线准入。**

> 2026-11-03。本文是已核查代码后的实施规划，不是功能完成声明。YOLO 按用户决定保持不换；未安装 SAM 3、未启动训练、未消耗云 GPU。任务编号待并入 ROADMAP-v2 的追加批次。

## 1. 从 MaskFlow 学什么

### 核查基线

审查仓库：[ZhangEmo28/maskflow](https://github.com/ZhangEmo28/maskflow)，固定提交 [`2460c1bd3a9c4ae7daab940485c3016660437752`](https://github.com/ZhangEmo28/maskflow/tree/2460c1bd3a9c4ae7daab940485c3016660437752)。已读 README、LICENSE、第三方声明、批量任务、预标注服务、标注 MCP 工具、推理入口和训练数据检查代码；未运行其 GPU 模型或完整部署。

| 发现 | StepEye 的处理 |
|---|---|
| 后台按图片处理任务，分别记录成功、未检出、失败，支持取消与恢复 | 借鉴任务语义，接入现有 Python 服务，不搬运 .NET 实现 |
| 标注编辑器有待确认状态、撤销重做、低置信度优先复核 | 借鉴交互；首版只做框，分割轮廓后置 |
| MCP 不只返回文字，还能返回画有编号标注的图 | 建议采用同样的可视化工具输出，便于用户与智能体复核 |
| 导出关注人工确认、逐类分布与近重复分组 | 加入 StepEye 数据版本层，避免相邻视频帧跨训练/测试集 |
| 源码 AGPL-3.0，推理入口实际调用 Ultralytics，训练也依赖它 | 不把源码或推理服务直接并入 StepEye 闭源交付路线 |

以上行为可在固定提交的 [BatchAnnotationWorker](https://github.com/ZhangEmo28/maskflow/blob/2460c1bd3a9c4ae7daab940485c3016660437752/src/backend/maskflow-api/Application/Services/BatchAnnotationWorker.cs)、[AnnotationTools](https://github.com/ZhangEmo28/maskflow/blob/2460c1bd3a9c4ae7daab940485c3016660437752/src/backend/maskflow-api/Mcp/AnnotationTools.cs)、[TrainingReadiness](https://github.com/ZhangEmo28/maskflow/blob/2460c1bd3a9c4ae7daab940485c3016660437752/src/backend/maskflow-api/Infrastructure/TrainingReadiness.cs) 中核对。

**取舍：复用 MCP 官方 SDK、ONNX Runtime 等许可合适的上游组件；借鉴 MaskFlow 的流程和数据状态；不复制其业务源码，不引入 Vue + .NET + MySQL + MinIO 的整套运行栈。** 若客户主动选择独立部署 MaskFlow，可后续提供导入/导出适配；独立进程或 HTTP 连接不等于自动免除其许可证义务。

### 许可分三本账

1. **应用代码**：MaskFlow 本身是 AGPL-3.0；“能看源码”不等于适合直接并入当前交付方式。[LICENSE](https://github.com/ZhangEmo28/maskflow/blob/2460c1bd3a9c4ae7daab940485c3016660437752/LICENSE)
2. **模型、推理实现、基座权重**：SAM 3 访问条件、Ultralytics 实现及权重分别核查；不能把 MaskFlow 许可或 ONNX 格式当作权重授权。其 [THIRD_PARTY_NOTICES](https://github.com/ZhangEmo28/maskflow/blob/2460c1bd3a9c4ae7daab940485c3016660437752/THIRD_PARTY_NOTICES.md) 已明确区分这些组件。
3. **数据**：图片、标注、合成图分别登记来源、使用范围及导出许可。自动标注不会改变原始数据的使用条件。

不根据仓库宣传推断准确率，也不照搬它的最低样本数作为 StepEye 工业场景的合格线。

## 2. StepEye 已有资产与实际缺口

| 现有模块 | 可复用 | 需要补的层 |
|---|---|---|
| `visual/datasets.py` | X-AnyLabeling / YOLO 格式导入与打包 | 版本化、同名文件冲突、未知类别/越界校验、按视频与近重复组隔离划分 |
| `visual/timeline.py`、时间轴页 | 视频上传、区间、抽帧、回归清单 | 代表帧抽样、近重复去重、帧到标注项目的来源关系 |
| `visual/shadow.py`、告警页 | 整图判定、真值与误报/漏检统计 | 不能拿整图 PASS/NG 代替框级标注；增加“转为标注样本”入口 |
| `visual/samplegen.py` | 样本增强与来源清单 | 几何增强须同步变换框；合成图不能混进独立验收集 |
| `visual/onnx_detector.py` | 当前 YOLO v8/v5 类输出与统一对象事件 | 提取不带工位去抖的单图预测接口；不能宣称任意 ONNX 都兼容 |
| `visual/wizard.py` | 草稿→确认→验证→上线的用户习惯 | 标注项目与工位/场景关联，不把训练按钮直接连上线 |
| `visual/regress.py` | 判定回归、指纹、边界卡 | 框级检测评测需另外实现 IoU、逐类召回/误报及分桶报告 |
| `visual/auth.py`、FastAPI、SQLite、现有 Web | 登录角色、服务与部署方式 | MCP 专用授权、项目范围、操作审计；不让 MCP 绕过业务服务直接写库 |

这些是对本次工作树的代码核查，不是对原路线图中“已接入/已完成”描述的照单接受。

## 3. 傻瓜式工作流

入口放在现有「样本库」中，增加「待标注 / 待复核 / 已确认」视图，不再添加一套独立门户。

1. **放资料**：拖入照片/短视频，或从影子样本、告警证据选图。选择场景；视频自动选代表帧，预览抽帧数量。
2. **告诉系统找什么**：选已登记类别，默认沿用场景类别。已有匹配检测器时自动预标注；模型不认识的类别明确提示“需人工框选或安装相应标注引擎”。
3. **先看两张**：叠加编号框，用户可改类别、拖框、删除误框、补漏框；点“效果可用，再处理其余图片”。引擎、阈值、最大处理量等藏在高级选项。
4. **集中检查问题图**：优先显示低置信、类别冲突、质量差、未检出和模型分歧。键盘切图、确认、跳过；保存后可自动下一张。
5. **确认并导出**：展示还缺哪些类别/光照/背景负样本；仅将已人工确认的结果组成不可变数据版本。达到场景策略后再允许发起训练任务。

“没检出”默认是 **待复核**，不是背景真值；背景图由人明确确认。批量确认前显示图片数、目标数、筛选条件和可撤销版本，不使用“置信度高=已人工确认”的规则。

### 首版与后续分界

**首版必须有**：框级编辑、现有匹配 ONNX 检测器预标注、持久化批量任务、人工确认与回滚、YOLO 导出、固定数据版本、CPU 路径、完整错误/空态。

**后续可选**：点/框提示分割、开放词汇找新类别、视频目标传播。SAM 系/开放词汇模型独立 worker 运行，权重与运行许可核清且本机实测后再开放。不把 SAM 3、GPU 或外网下载变成首版启动前置。

视频传播结果仍是机器建议；遮挡、切镜、光照变化或低相似度时停止传播并排入复核，不用一个确认动作把整段视频变成真值。

## 4. 数据与任务契约

### 核心实体（建议）

```text
dataset_project: id, scene_id, label_schema_revision, label_map, policy_id
asset: id, source_kind, source_ref, sha256, width, height,
       camera_id?, session_id?, video_id?, frame_ts?, captured_at?,
       illumination_bucket, source_asset_id?, synthetic, license_ref
annotation_revision: id, asset_id, parent_revision, schema_revision,
       producer(human/model/agent), engine_id?, model_sha256?,
       prompt_revision?, job_id?, state(draft/reviewed/confirmed/background),
       targets[{id,label_id,xyxy_norm,confidence?,provenance}], reviewer?, reviewed_at?
dataset_version: id, asset_hashes, annotation_revision_ids,
       label_map, group_assignment, split_seed, policy_snapshot, sha256
label_job: id, project_id, input_snapshot, engine_snapshot, idempotency_key,
       status, cursor, total, processed, suggested, no_targets, skipped, failed
```

说明：`reviewed` 可表示机器/智能体检查过；只有带真实人类审核记录的 `confirmed` 才进入人工真值。几何坐标统一使用正向图像上的归一化 xyxy；EXIF 旋转在导入时固定，导出再统一转换为 YOLO cxcywh。

### REST 与任务行为（拟新增）

| 接口组 | 职责 |
|---|---|
| `POST/GET /api/label-projects` | 建项目、绑定场景与标签表 |
| `/api/label-projects/{id}/assets` | 上传/导入已存在资源 ID，保留来源，不接受任意服务器文件路径 |
| `POST /api/label-jobs`、`GET /{id}`、`POST /{id}/cancel` | 提交持久化任务、看进度、取消未开始项 |
| `GET/PUT /api/label-assets/{id}/annotations` | 读写草稿；提交 expected_revision，冲突返回 409 |
| `POST /api/label-assets/{id}/confirm` | 当前用户有审核权限且明确确认后生成新版本 |
| `/api/label-projects/{id}/quality`、`/versions`、`/export` | 质量报告、不可变快照、可复现导出 |

任务状态：`queued → running → completed / partial / failed / canceled`。每张图落账后才更新 cursor；幂等键绑定输入快照、模型指纹与选项，重启可续跑。取消保留已完成建议；超时/重试有上限；单图失败不吞掉整批进度。已确认版本不可覆盖，重跑只能产生新草稿分支。

图片原件不原地修改；编辑与撤销都记录版本、操作者、差异、来源模型。自动清除只作用未确认建议，删除原始数据需额外确认。

### 数据集准入

- 来源视频/连续拍摄批次、同一原图的增强版本及近重复组不能跨 train/val/test；使用稳定 seed 并固化分组映射，禁止重新随机划分抬高分数。
- 导出仅选场景策略允许的已确认结果；空标签文件必须区分“人已确认无目标”与“缺标签”。类别表和几何不合法时拒绝导出。
- 场景策略定义最低样本量、独立相机/时段覆盖和验证集要求，保留既有 TRAINING.md 的场景要求；张数达标不是准确率达标。
- 独立测试集禁止混入生成图、同源增强、用于调整提示词/阈值的图片；模型伪标注不可直接成为独立测试真值。
- 上岗仍走回归、边界卡、影子与发布闸门；自动标注完成不等于模型可上线。

## 5. MCP 外接分两个方向

### A. 外部智能体使用 StepEye（先做）

```text
Codex / Claude Code / 其他 MCP 客户端
             ↓ 官方 MCP Python SDK：stdio 或 Streamable HTTP
        StepEye MCP 适配层
             ↓ 同一业务服务、鉴权、审计、任务队列
        StepEye 数据 / 标注 / 回归 / 告警
```

先提供 **stdio** 原生入口供本机客户端启动，日志只写 stderr；内网 Web 集成用 `/mcp` Streamable HTTP。无需 Docker。MCP 是适配层，不能另写一套业务规则或绕过 REST 的权限。传输与 JSON-RPC 用 [MCP 官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk) 实现，不手写不完整的协议。[传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

| 首批工具 | 权限与副作用 |
|---|---|
| `list_scenes`、`list_assets`、`get_annotations`、`get_job`、`get_quality_report` | 默认只读；受用户、项目/场景范围限制 |
| `view_asset` | 返回有编号叠加图和图例；图像读取单独授权，不能把“只读”解释为可向任意外部模型传图 |
| `create_annotation_draft`、`start_label_job`、`cancel_label_job` | operator + 显式写 scope；输出仍为待确认草稿，限制图片数/并发/资源预算 |
| `prepare_dataset_export`、`prepare_training`、`prepare_model_release` | 只生成计划与影响摘要，不在首次调用直接导出/花费/发布 |

**首版不向智能体开放直接“确认人工真值”工具。** 人在 Web 看图确认。训练、破坏性清除、发布等需要：准备 action → Web 展示影响并确认 → 单次授权绑定 action ID、参数哈希、资源版本、用户与有效期 → 执行。仅在工具 description 写“先征得同意”不能作为后端权限控制。

MCP tool annotations 只作客户端提示；服务端每次执行仍校验权限。官方规范同样要求客户端谨慎信任这些提示，并建议保留人类拒绝调用的能力。[工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

### B. StepEye 调用外部 MCP（后做）

用于接已有的标注服务、MES、测量/设备数据或知识服务。管理员登记命名连接器，选择允许的服务器、工具和数据范围；业务只引用连接器 ID，不接受用户输入的任意 URL 或 shell command。

- 默认只启用只读工具；写工具按逐项批准，不把外部服务自报的只读标记视为可信事实。
- MCP 返回值进入统一 schema 校验；外部文字、工具描述及文档不能自动变成执行命令。
- 每次调用有超时、大小上限、并发与重试上限、熔断和可取消任务；相同调用的重试不得重复写入。
- 外部标注结果一律记录来源、版本、模型指纹和连接器 ID，先入草稿，不直接入真值或生产规则。
- 生产网络默认禁外传图片；管理员若启用外部图像工具，明确显示目的地和数据范围。

两方向都要有连接测试、权限预览、最近调用结果及停用入口；不要把 MCP 接入做成一个无法解释的聊天黑箱。

### 认证与网络约定

本机 stdio 从环境变量取得 MCP 专用凭据；不复用浏览器 Cookie，不把凭据写进命令示例、日志或仓库。HTTP 默认仅回环/受控内网，启用认证和 Origin 校验；远程接入用 HTTPS，token 绑定受众/资源范围、可过期可撤销。不要透传第三方 token。[授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)、[安全实践](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices)

后续云端 ChatGPT/Claude 连接器若需要公网 OAuth，作为独立部署选项设计，不为默认私有化客户开放公网端口。是否传出原图与是否运行在云端必须分开告知。

## 6. 分阶段交付与验收

工作量是熟悉现有代码后的人天估算，不含数据标注、模型采购/下载审批、现场验证。可并行的是只读 MCP 与标注数据模型；写工具必须等标注权限/版本层完成。

| 编号建议 | 最小交付 | 估算 | 依赖与验收 |
|---|---|---:|---|
| T28.1 标注数据层 | 项目/资源/类别表/草稿/人工确认/版本导出 | 2 | 几何和标签校验；并发版本冲突；同名图不覆盖；未检出不能变背景；group split 无泄漏；迁移测试 |
| T28.2 Web 傻瓜工作台 | 上传、试两张、批量、问题图复核、矩形编辑、撤销、导出 | 3 | Playwright 完成整条用户路径；480/980/1920 宽度无裁切；viewer 不能写；断网不丢已保存版本 |
| T28.3 预标注任务 | 当前匹配检测器复用、CPU worker、进度/取消/恢复 | 2 | 关闭浏览器继续；进程重启续跑；幂等；单图超时；确认结果不覆盖；实际样本回归与 CPU 延迟记录 |
| T28.4 可选提示/视频传播 | 可替换模型服务，点/框辅助与视频建议 | 3–5 | 模型许可/硬件实测后启动；遮挡和切镜停止；传播结果不自动确认；缺模型能手工完成任务 |
| T29.1 MCP 只读 | stdio + Streamable HTTP、查询与可视化、权限/审计 | 2 | 官方 SDK 客户端握手、tools/list/call；越权/撤销拒绝；非法 Origin 拒绝；密钥不落日志；Windows/Linux 启动冒烟 |
| T29.2 MCP 草稿写入 | 写 scope、job、prepare/approve/execute | 2 | 过期/重复/参数改变/版本改变的授权拒绝；无授权不确认真值、不训练、不发布；端到端客户端与 Web 审计一致 |
| T29.3 外部连接器 | 服务器/工具白名单、schema、超时/预算/停用 | 2–3 | 本地可控 MCP 测试服务器；恶意描述不执行；外部故障可解释；只读账户不能借连接器越权 |

建议先交付 T28.1–3 + T29.1，共约 9 人天。模型能力不够时，先交付可用人工复核工作台与现有类别预标注；不要靠假预测撑完整流程。

## 7. 与多源融合、光照治理的衔接

### 自动标注数据必须保留的现场条件

除类别外，记录 `camera_id / session_id / frame_ts / captured_at`；照明分桶至少可表示白班、夜班、开灯、关灯、强反光/背光和未知。自动推断桶可作建议，人可修正；无法可靠取得曝光/增益等相机参数时记 unknown，不能伪造“曝光已锁定”。

每个 scene 的准入策略声明哪些光照桶必须有独立真值、每桶最低覆盖与最差桶指标；多光照增强只是补训练，不能代替真实夜班/反光验收。光照漂移、模糊或相机位移期间生成的建议带质量标志，默认优先复核，不混成正常样本。

传感器/PLC 标签须记录 `signal_name / value / unit / source_device / event_time / receive_time / quality / calibration_version`。扭矩枪测得的扭矩可以作为对应信号真值，不能推出“视觉已识别锁付合格”；扫码、工装到位与视觉对象标签也不互相冒充。对齐容差与信号新鲜度由场景策略定义。

### T30 / T31 最小验收建议

| 编号 | 交付与最小验证 | 尚不能据此宣称 |
|---|---|---|
| T30 多源信号融合 | 统一带来源/时间/质量的事件；视觉+PLC/扫码等至少两路可回放；处理乱序、重复、超时、冲突、设备掉线；每条输出可追溯用过哪些证据；关键缺证据时 UNKNOWN/人工，而非默认通过；正常与故障回放有测试 | 只接了一个信号 API 或加了加权平均就称工业级；没有现场传感器标定就保证判定有效 |
| T31 光照治理 | 固定参考/背景 ROI、有效像素与遮挡掩码；区分亮度/色偏、运动遮挡和几何位移；突变/过暗/过曝降级与恢复滞回可回放测试；无静默把坏参考适应成正常；昼夜/开关灯/反光真实数据分桶报告 | 单一全图均值阈值、合成亮度测试全绿就称现场适用；软件未读取相机参数却声称锁曝光成功 |
| 联合发布准入 | 在固定多源/多光照 holdout 上按场景报告漏检、误报、UNKNOWN 占比、延迟与恢复时间；阈值版本、模型、规则、相机/传感器标定纳入指纹；现场影子通过后才能发布 | 把 UNKNOWN 排除后给出偏高“准确率”；把机器伪标注当独立真值 |

工业可用的完成口径是：工程故障路径通过、独立数据回放通过、目标工位现场验证通过。三类证据分别记录，不用一个“已完成”掩盖缺项。

## 8. 本次检查结果

- 已完成：固定 MaskFlow 提交的源码/许可审查；核对 StepEye 相关本地模块；核对 MCP 官方传输、工具与授权规范；形成以上实现与验收方案。
- 未执行：MaskFlow 整套运行、SAM 3/开放词汇模型性能评测、真实数据自动标注、训练、MCP 功能开发、目标现场工业验收。
- 本文未复制 MaskFlow 源码，未修改检测器选型，未修改现有生产配置。
