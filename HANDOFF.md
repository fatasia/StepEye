> **最新交接（2026-11-03，本轮结束后停止）：先读 [本轮交接](docs/HANDOFF-2026-11-03.md)。下文早期状态是历史记录，勿据此重复下载/训练。**

# 交接清单(2026-11-03)——新会话接手必读

> 本轮继续完成原生媒体、空间态势、巡更、质量门禁与设备信号融合。最终验证结果见下方；公开仓库 https://github.com/fatasia/StepEye 。
> 新会话开始时:先读本文件 + `docs/ROADMAP-v2.md`(终版清算)+ `git log --oneline`（以实际提交历史为准）。

## 一、项目是什么

**StepEye = 工业视觉 AI 平台**(原 sop-vision),三腿架构:
- **实时规则腿**:SOP 状态机(漏步/错序/超时)+ 常驻安全规则(S4 跌倒/S16 无人值守/S17 聚集,免训练姿态/区域规则)
- **拍照即检腿**:VLM 判定(glm-5.3-flash,温度锁 0.1)——手套 14/14 零漏报实测;首件两步判定 5/6(幻觉过杀实证,已如实记录)
- **训练腿**:样本生成(augment+lk888 生成式)→ 影子模式攒真值 → 回归闸门 → 微调 D-FINE/RF-DETR → ONNX 上岗
- **平台件**:认证 RBAC、告警闭环+飞书、GIS 厂区图、看板、向导、exe 打包、时间轴标注器、评测回归+能力边界卡

## 二、代码地图(全在 sop-vision/)

| 模块 | 文件 | 说明 |
|---|---|---|
| 引擎 | `visual/engine.py` `dsl.py` | 条件:hand_dwell/obj_state/signal/person_present/person_absent/person_count/vlm_check;纯安全岗 cycle 可空 |
| 感知 | `perception/hands.py` `persons.py`(含 is_fall_posture) `objects.py` `quality.py`(含光照突变) | MediaPipe 全家,Apache-2.0 |
| **安全** | `visual/safety.py` | SafetyMonitor 常驻规则:person_absent/count/fall/object_present,**已接运行时** |
| **拍照即检** | `visual/inspection.py` `llm_provider.py` `samplegen.py` | 队列+场景矩阵+augment/cogview/lk888 三引擎;Provider 配置化(.env 三行换模型) |
| **协议接入** | `visual/onvif.py` `gb28181.py` `geo.py` | ONVIF 发现/读坐标;GB28181=wvp-pro REST 客户端(**不自研 SIP**);GIS 同步 |
| **平面图** | `visual/plans.py` | 图片底图+打点(CAD 用户自导图片) |
| **评测/训练** | `visual/regress.py` `datasets.py` `shadow.py` | 回归跑分+边界卡;X-AnyLabeling/YOLO 导入;影子库+真值 |
| **服务** | `visual/server.py` | REST/WS 服务;**铁律:pydantic body 类只允许模块级**(守卫测试 test_server_hygiene 已建) |
| 前端 | `web/js/pages/` 9 页 | 总览/实时/追溯/配置(区域拖框)/拍照即检/告警中心/样本库/时间轴/厂区图 |
| 工具 | `tools/` | eval_gloves/probe_first_article/make_sim_refs/make_eval_set/cam_uploader/probe_vlm/shoot/interact |
| 部署 | `deploy/` | build_windows.bat+entry.py(exe **已构建验证**);monibuca/config.yml |

## 三、模型清单(models/,*.task/*.tflite/*.pt/*.onnx 均不入库,按 README 下载)

| 模型 | 状态 |
|---|---|
| hand_landmarker.task / pose_landmarker_lite.task / efficientdet_lite0.tflite | ✅ 在本机 models/;GitHub 不含 |
| community/helmet.onnx | ✅ **实测可用**(With/Without Helmet) |
| community/fire-smoke.onnx | ❌ **权重不合格已淘汰**(ultralytics 原生 4 张火图全未检出),待自训替换 |

## 四、关键实测数字(写死在文档里,别推翻)

- 手套 14/14 零漏报(P50 7s);首件 5/6 幻觉过杀(P50 137s);温度锁 0.1 后 3/3 一致
- VLM API:智谱 coding 端点 glm-5.3-flash(原生多模态);本地 Qwen3-VL-2B 三行 .env 可切(未实测)
- 训练环境:本机 4060 只够 2B QLoRA;检测器微调建议租 4090(¥2/时)

## 五、当前任务状态（2026-11-03 本轮更新）

本表取代早期“剩余任务”清单。YOLO 与已有权重按用户要求保持不换；不引入 Docker 运行依赖。

| # | 实际进展 | 还差什么 |
|---|---|---|
| T21 Monibuca | 同源 WHEP、回退/重试/释放/租约；原生 v5.1.6 实际 640×480 播放；Win/Linux v6 安装器与模板 | 官方 v6 Windows 包 404、beta 403；v6 实包/配置/ICE 与 Linux 联调未完成 |
| T22 厂区图 | 已完成空间态势、真实点位立体示意、风险图层、详情/实况联动、二维编辑；最终1920截图验收 | 真实厂区底图/绑定数据由现场提供；不冒称BIM模型 |
| T23 影子 UI | 证据详情、PASS/NG、筛选、报告、只读权限；历史FAIL→NG，UNCERTAIN单列 | 真实人工真值持续积累 |
| T24 相机协议 | ONVIF Simulator v0.4.0 SOAP+RTSP实际640×480帧；地址固定/禁重定向；WVP登录/分页/坐标契约修正 | 多播发现本机0设备；ONVIF真机、WVP/SIP注册点播仍未验收 |
| T25 自训上岗 | training_preflight工具及资源审计完成；本机无合格框标注集 | 数据、实际训练、专用导出适配、独立检测器评测、现场影子；未训练/未租GPU |
| T26 录像调阅 | 按工位/时间索引本机共享目录、HTML5播放、Range拖动；H.264测试片段实测 | 真实v6录制生产端、跨机目录/保存策略/负载联调 |
| T27 巡更到位 | 持久计划+真实人员观测hook+跨午夜报表；未观测=未知；四状态与只读交互已验收 | 现场人员感知标定、真实计划与长期记录 |
| T5/T6尾 | 11页认证路由冒烟、新增功能交互/写权限通过，页面异常0 | 本轮不重复旧页面多尺寸矩阵 |
| T28 自动标注 | 已接真实CPU ONNX单图建议、后台任务、框编辑/确认、不可变YOLO ZIP | 真实标注集与现场复核；分割、视频传播未做 |
| T29 MCP | 官方SDK本机stdio/HTTP、项目令牌/吊销、只读和可选草稿、命名外接只读工具已接 | 未开放训练/发布、公网OAuth及连接器Web管理 |
| T30 多源融合 | 有界入箱、TTL/序号/审计、批次求值、source限定、非锁存实时all、冲突/未知诊断已接 | PLC/扫码/扭矩驱动、单位/量程与SN关联、现场故障回放与影子 |
| T31 光照治理 | 固定参考、局部饱和/漂移、即时门禁+恢复滞回、安全重新观察、旧VLM失效、断流恢复清旧证据 | 昼夜独立回放集、曝光锁定读回、真实相机/现场影子 |

### 验证与入口

- 最终后端结果：310 项后端检查已通过、2 项缺手部模型跳过（隔离全量306通过/2跳过；修复本机.env依赖的2项定点通过；新增断流2项及受影响14项定点通过），JS出帧新鲜度1项/全部JS语法/Python编译通过；JS语法、编译与前端出帧新鲜度测试纳入统一检查。
- 最终UI仅1920×1080主桌面：`.v0/output/playwright/twin/sitemap_1920.png`、`trace-report/trace_1920.png`、`final-desktop/`；11页认证冒烟与录像播放记录见 `final-desktop/report.json`。
- `docs/MONIBUCA.md`：原生部署、版本差异、录像目录；`docs/FUSION-LIGHTING.md`：设备信号/光照接线。
- `docs/AUTO-LABEL-MCP-PLAN.md`：MaskFlow审查与T28/T29；`docs/TRAINING-READINESS.md`：数据/训练实际缺口。
- `docs/INDUSTRIAL-ACCEPTANCE.md`：工程、独立回放、现场影子分开验收。当前只有本机工程/模拟器证据，不签署“工业级已验收”。
- Linux/Windows CI已配置，目标Linux机器和现场运行结果需实际确认；Web界面共用，不依赖本机EXE。

**环境注意**:本机 conda 的 torch 曾被 ultralytics 安装搞坏(shm.dll),已用 `pip install --force-reinstall --no-deps torch==2.12.0+cu130` 恢复;**cv2 与 torch 同进程仍有 DLL 冲突**(DINO 特征路径隔离为 `extract_features_dino`,网格特征兜底不受影响)。爬坑记录详见各测试文件 docstring 与 commit message。本轮另发现Conda缺失 libiomp5md.dll，按已安装intel-openmp包缓存的paths.json核对SHA256后补回缺失运行库；未替换torch或删除.back。

## 六、纪律(不可协商,全 session 实践过)

1. 每步:先读后写 → 130+ 测试全绿 → 提交 → 推送
2. UI 改动必须 Playwright 交互验收(项目自带 tools/shoot.py + .v0/interact_*.py 模式)
3. VLM/免训练判定:**评测集未建不承诺准确率**;UNCERTAIN 转人工;能力边界卡交付
4. pydantic body 类只允许模块级(守卫测试会拦)
5. 密钥只进 .env(gitignore);lk888 key 已在对话泄露,**需轮换**


### 2026-11-03 续接：动作旁路与按需运行

- T28 自动标注及 T29 MCP 已提交 `8bfe285`，Windows/Ubuntu 主测试及可选 MCP 四个 CI job 均通过。
- T32 已接默认关闭的动作分类 ONNX 影子旁路：固定16帧224、最新窗口有界队列、跨工位串行、身份/周期/画质/TTL失效；不推进 SOP 状态机。配置与边界见 [动作选型](docs/SPATIAL-ACTION-OPTIONS.md)及 [性能检查](docs/ACTION-BENCH.md)。
- 按需执行：即检/标注/VLM 队列空即退，动作模型空闲释放，预览无租约不留回收线程。主 Web 与已启用实时工位仍运行；不自动接管外部 Monibuca 服务。
- T32 尚缺合格工厂动作分类权重、独立片段真值和目标硬件实测；当前只有真实 ONNX 协议测试，不含 VideoMAE 性能或动作准确率结论。T21/T24/T25及现场验收缺项保持原表。

本轮本机全量：475 passed、2 skipped（含工作区独立品牌测试2项，品牌改动未纳入本次提交）；Python编译、JS新鲜度1项通过。此次未改UI，不重复截图矩阵。


### 用户导入与实时平台路线（2026-11-03）

YOLO ZIP 导入已接Web：名称映射、逐项问题、草稿复核、合成标记、来源分组；旧CLI修复嵌套标签配对、同名覆盖、越界/非法框，缺标签不生成假负样本。63项聚焦及整批497 passed/2 skipped（含独立品牌测试2项）通过；1920 Playwright真实导入、错误报告、复核和viewer403通过，页面异常0，测试服务已停止。截图 `.v0/output/playwright/dataset-import/dataset_import_1920.png`。

后续按 docs/YOLO-PLATFORM-PLAN.md 的T33–T38：YOLO实时/VLM辅助、行业专用识别包、常用SOP、统一模型中心、评测发布、精密视觉和工程模块约束。已登记公开PPE/烟火来源，但本批未下载新模型或完成训练；数据导入之外的路线不算已实现。


### 视觉应用、设置与通知（2026-11-03）

新增统一视觉应用首版（登记模型→真实单图试推→自动带类别与引擎建标注项目/打开旧项目补样本），不是已实现自动训练发布。新增设置（浏览器外观、用户角色/密码、通知状态、操作日志、运行摘要）。用户最后admin保护、重置立即撤旧会话/MCP；操作日志只存路由模板与状态，不存请求体。通知复用Apprise2.1.0可选隔离SDK，邮件/飞书/企业微信/钉钉按需子进程，未配置无worker；未做真实外部投递，状态历史进程内有界。配置见docs/NOTIFICATIONS.md。

验证：全量551通过/2跳过/1个旧测试fixture因新增最后admin保护失败，补备用管理员后相关28项全部通过；不重复整套。视觉应用与设置各一次1920真实Playwright通过，页面异常0；真实安全帽单图请求920.79ms，不作持续FPS结论。Python编译/JS语法/JS新鲜度通过。新模块行数限制已纳入测试；旧server模块逐步迁移，不全量重写。

用户要求先审计旧模型和zcode资产，禁止重复训练。下一批仍需一键训练/增强、检测评测、版本发布回滚和行业模型包；已有文件不自动等于平台自训或现场合格。

### 公开行业模型与训练入口（2026-11-03）

- 已下载并实推仓储/PPE20类和新烟火ONNX；视觉应用登记固定指纹，试跑/预标注支持模型原生320或640输入。新烟火生产试跑单次冷请求648ms，不能作为持续FPS；旧不合格烟火保持禁用。两项仅供评估，许可待审核。来源见docs/PUBLIC-SAFETY-MODELS.md。
- 已下载并校验极片CoatingVision完整数据（2227图、581有框，两类），PCB候选ONNX为未支持的端到端输出，未上岗；完整许可/数据缺口见docs/PUBLIC-QUALITY-MODELS.md。
- 一键训练/增强接Web与持久队列：固定确认版本与基座、隔离执行、取消/超时/幂等、候选回登记；默认不常驻，不自动发布。隔离训练环境仍需管理员准备，尚未执行真实训练。见docs/YOLO-TRAINING.md。
- 本批全量592通过/2跳过，Python编译与JS新鲜度通过；训练页面真实API交互验收使用受控runner验证提交/取消，不算真实训练。训练页面与公开模型列表1920截图在.v0/output/playwright/training/和models/public_models_1920.png。
- 本地Qwen低延迟与按需生命周期接入进行中；上述全量数字不覆盖后续Qwen新增代码，完成后另列定点结果。

### 本地 Qwen 按需接入（2026-11-03）

已接官方Qwen3-VL-2B-Instruct Q4_K_M候选的原生配置、按需llama-server单例、GPU层请求、启动/请求预算、忙时拒绝、空闲60秒退出与停服清理；保留官方F16视觉投影。仅回环地址，临时本机令牌，云密钥不转发；截断输出判失败。详见docs/QWEN-LOCAL.md。

本轮官方大文件下载因连接中断/超时未完成（Q4约42MB、投影约35MB，均为.part），未加载或实测Qwen速度/准确率，不改变现有.env。下载器固定版本/SHA、断点续传与错误报告已实现，无遗留下载或推理服务。

后续定点：本地VLM/Provider/运行时/即检61项通过（随后生命周期再补2项）；训练队列/路由/本地VLM/配置/模块规范最终49项通过，下载器离线5项通过。全量592/2为接入Qwen前批次，后续由Windows/Ubuntu CI覆盖。模型安装完成与实际冷/热响应验收仍未完成。

### 基础与质量能力批次（2026-11-03）

用户补齐B0–B6、S1–S14/S3b、Q1–Q12、V4/V5任务名，并明确忽略旧表中的选型和工作量。全清单统一见docs/TASK-COVERAGE.md；自行寻找公开数据，不再要求用户提供资料才开工。VideoMAE明确纳入后续T32。

本批新增：中立检测后端/个体IoU跟踪、S1入侵/S5持续烟火运行时消费、不可变配方版本与回滚、m/mm平面标定和距离、工业GenTL可选采集/软件触发、框级评测API、SPC/GRR/一致性报告、QR/Code128/DataMatrix与实验OCR。质量/评测/标定/读码集中进入视觉应用工具抽屉。已修多图NG被PASS覆盖、首件差异失败被当无差异、跨图差异混用和跨工位告警误去重。

现有未提交品牌资产保持不动；原生模块按需加载。工业相机目前无CTI/硬件，只有协议替身检查；读码在隔离环境真实运行，OCR存在误字。跟踪公开视频60帧回放与入侵演示有证据但没有跟踪真值指标。真实训练、完整行业识别/模型发布与VideoMAE模型尚未完成。

本批整套验证：778 passed、2 skipped（本机工作区含2项独立品牌测试，品牌改动不在此提交）；Python编译、JS语法与JS新鲜度通过。1920真实Web检查：CSV→SPC图表/报告历史、标定JSON→100mm软件几何测量、越界拒绝、viewer只读/403；另实际QR图片经页面读取成功。截图及结果在.v0/output/playwright/quality-tools/，页面异常0，测试服务已停止。上述几何数据为软件测试，不是相机实测精度。

提交前复核补修：质量JSON数组/分析对象上传不再误按CSV解析；首件OK/NG参考图为空、缺失或超过3张拒绝判定，绝对参考路径解析修复。相关27项定点通过，实际Web JSON导入通过；全量778/2是这些末尾补修前结果，后续由跨平台CI再覆盖。


### 2026-11-03 续接：安全信号、AI 设置与文档

- 用户优先级调整：实时安全仓储、工业专用质量、特殊设备信号优先；其余按总清单顺延，不删除任务。
- 人车米制距离/速度已在所有工位构造路径绑定标定。PPE逐人用品观测已接，旧安全帽ROI探针独立可运行但尚未异步接入；不能把未检出当未佩戴。
- 温度、温升、热点面积消费现有设备信号，接入运行时和告警库；黑屏不阻止温场告警。安全配置抽屉复用原保存/重载路径，真实Web保存与权限检查通过。
- AI服务设置与动态VLM绑定、全局只读助手、文档原件/不可变版本/发布/检索已接主服务。真实Web上传→发布→viewer检索→助手来源引用通过；图像生成通用绑定未接。详见 docs/AI-DOCUMENTS.md。
- 修复WebSocket空闲断连导致停服等待，并补关闭自有告警数据库。集成全量曾877通过/2跳过、1处WS取消失败；修复后API/WS相关15项通过。其他专项结果见各模块测试，后续以CI最终结果为准。
- 极片针孔已实际三轮训练/导出；固定阈值ONNX验证P=.846/R=.524，候选未发布。PCB六类独立输出适配与真实试跑已完成，固定图0框不是质量合格。
- VideoMAE已按用户指定改V2：Small K710完整权重校验通过并导出，公开吃意面片段真实分类正确；CPU/GPU后端不同，不能用PyTorch GPU耗时宣称ONNX部署性能。性能和许可见 docs/ACTION-BENCH.md。
- SOP关键参考帧动作卡、设备结果导入/复判正在接续；机理实验室等其余未完成项保留 docs/TASK-COVERAGE.md。Qwen大文件仍断点续传，未产生本地VLM准确率或速度结论。

本批最终后端全量：915 passed、2 skipped；JS语法23文件、JS新鲜度测试、Python编译通过。新增：设备原判/图片原子导入及独立人工/模型复判、SOP开始/关键/完成参考卡、双ROI标定边缘间距API。生产规则不会因参考卡创建而变化。界面真实验收记录见 action-references、safety-config、documents-live 目录。

整合复查修复：温场改由已验收信号入站驱动，不依赖相机出帧，不增加轮询线程；队列满时明确标注未进入SOP。HTTP实际无摄像头帧高温报警/TTL、动态SOP VLM绑定与相关54项检查通过。补齐geo/自有alerts数据库关闭。

提交 f2044c8 已推送；GitHub Actions 41604051799 的 Windows/Linux 主测试及 MCP 四个 job 全部通过。Qwen投影与两份原生运行包已完成哈希校验，主模型仍续传；未启动推理服务。

### 2026-11-03 续接：下载完成与安全帽异步接线

- Qwen Q4_K_M、F16投影和两份Windows运行包全部SHA256校验/解压完成；4060 Laptop原生冷启动到完整响应3.277秒，热请求0.431秒。固定图人员头部不可见，两次回答均推测安全帽，保持辅助用途。服务已停止，未修改现有云端配置。详见docs/QWEN-LOCAL.md。
- 安全帽探针默认关闭，按需异步执行、最新帧有界队列、原时间戳/身份/TTL校验，接入既有PPE告警。高级开关真实保存重载通过；画质/断流清证据，空闲释放，原生线程关闭未完成会明确返回false。
- 质量工具新增双ROI框选边缘测量；真实Web软件几何50mm、重新框选状态、页面异常0。未作相机光学精度结论。
- 本批相关169项pytest、下载器5项离线检查及3份JS语法检查通过。UI记录在.v0/output/playwright/helmet-config与edge-measurement。其余任务继续以docs/TASK-COVERAGE.md为准。

提交980bd6a已推送，GitHub Actions 41606725007的Windows/Linux主测试与MCP四个job全部通过。新增地面湿干分类和胶路候选下载实测记录见docs/PUBLIC-SPECIALIZED-MODELS.md；均未自动注册或生产启用。

### 2026-11-03 专用模型优先续作

极片针孔以原三轮权重再训练30轮（274秒），ONNX独立运行验证固定21图TP17/FP6/FN4，召回.810/精确率.739；R7仍是选模验证集，非独立现场测试。新增仓储可见数量/通道规则、安全抽屉配置、单排针脚参考检查、Modbus只读桥、weihu周期偏离/趋势机理实验室。真实Web仓储保存/权限、针脚缺第2针及机理CSV/历史/导出通过；未将规则/传统视觉计作专用模型。新增PPE/焊缝候选固定SHA与许可记录，当前仅评估。

本批全量1006 passed、2 skipped（含未提交独立品牌测试），JS语法检查通过；PPE14类/焊缝3类/增强针孔评估入口已接。新增电池焊缝6张实际样本，发现尺寸与类别潜在偏差；密封钉专用模型和六面SN关联仍未完成，见docs/BATTERY-INSPECTION.md。

提交078bf1d已推送，GitHub Actions 41622571248的Windows/Linux主测试和MCP四项全部通过。PPE与焊缝评估目录专项24项及两模型实际目录试跑通过；针孔增强也经同一ModelTrials入口实推。

继续批：六面证据按产品/SN/检测批次隔离，原判可UNCERTAIN；缺面、冲突、重复图不complete，任一NG不被后记录覆盖。PPE新增护目镜/安全鞋逐人消费，实时页显示五类与仓储计数、断流未知清旧值。LAN HTTP的设备/六面幂等键改为getRandomValues，不依赖secure-context randomUUID。后台相关57项、随后含下载工具53项通过；真实Web上传/切SN/重复保存/断流5秒与WS关闭恢复通过。电池数据续传进行中，未完成训练。

提交0dc2fae的Windows/Linux主测试及MCP四项CI已通过（41629987591）。电池焊缝原包1.153GB已官方MD5校验，61670图全量审计通过；首批6000/2000/2000真实3轮CNN训练+ONNX完成（KO召回90.58%、误放9.42%、精确率46.19%，非放行模型）。分类与框检测独立登记，同一视觉应用试跑只显示概率、不产生伪框或检测项目；真实两模型API/UI验收通过。实时DetectorCfg显式yolo_e2e适配已接，PCB实图0框不是PASS。原训练小样本候选保留，后续完整43169图扩量训练在新目录进行，不自动覆盖。

电池完整43169训练图扩量10轮已完成，val选第4轮；新5000 test排除pilot已见2000，旧/新权重同图对照FP1045→618、FN100→104。v2独立SHA和模型ID并列登记，不替换v1；平台真实分类试跑365.79ms（含进程/加载），不以0.695ms纯推理冒充端到端。所有下载/训练进程已退出。

续作整合检查：本机1059 passed、2 skipped（含独立品牌测试）；新增curl无requests隔离检查及配方快照相关20项通过。修复97573c9跨平台CI的下载器强制依赖requests失败：curl路径独立，requests仅选用该传输时导入。配方版本API新增snapshot_current，显式快照当前业务规则且保留旧patch语义；不修改设备/模型路径、不自动切换。配方与点选标定界面继续验收。

配方与标定UI完成：真实Web配方创建v1→修改规则→快照v2→应用→回滚v1、viewer403通过；图上点选4个标定点及2个独立验证点，实际保存/验证失败/共线422通过，LAN HTTP无subtle仍可手填参考编号保存。相关35项检查、LAN追加专项21项通过；1920截图位于.v0/output/playwright/recipes与calibration-editor。

807f7b2的Windows/Linux主测试及MCP整轮CI通过（41638773647）。新增固定SHA模型→确认标注版本val自动评测CLI，报告逐图FP/FN且不发布；新增weihu轴承包络谱按需分析与机理UI，复用原报告SQLite。主线整合相关58项通过，真实Web波形→报告/导出/viewer403通过，细节见docs/DETECTION-VALIDATION.md及WEIHU-REUSE.md。

通用图片生成绑定已接设置与样本库，复用原生成流程；只支持inline b64文生图，真实本机协议服务UI通过。原图按SHA持久保存，稳定source/group、候选标签待复核；响应/图片限额、单并发与超时定点通过，无云请求或.env修改。

本轮收尾：阀门固定ONNX双证据影子、同步声明校验与真实Web导入/报告已接；按需隔离推理、无常驻worker。完整1142 passed/2 skipped，最后机理/结构83项及JS检查通过；CI be11d81的Windows/Linux主测试和MCP四项均success（41644377134）。本轮新提交CI以GitHub最终状态为准。用户要求到此停止，续接详见docs/HANDOFF-2026-11-03.md。

2026-11-04恢复：用户明确无真机/生产数据，先以公开模型/数据补齐，无法完成统一汇总；其他平台依原顺序推进，并同步weihu更多电机/执行器、真正RUL和签名。Kairos积水RT-DETR已接独立双输出适配；官方预处理为直接PIL缩放而非旧探针letterbox。同一公开图conf .862，旧.882作废。相关33项及真实Web试跑/评测自动选adapter通过，不代表厂区精度。原品牌改动继续保留。

### 2026-11-04 续作：weihu 专域寿命与执行器

- RUL 已接机理实验室、不可变报告与导出；复用 FD001 ONNX，真实公开 parity 窗预测111.94605周期。仅 NASA 模拟涡扇域，不是工厂电机通用寿命模型。
- 执行器运动残差复用 weihu 内核；显式健康参考、工况一致、参考/评测记录隔离；公开 Cranfield 数据已实际推理并在1920 Web验收，viewer写入403。输出残差，不判故障类型或RUL。
- Ed25519目录验签工具已接RUL可选强制验证；`STEPEYE_RUL_TRUST_KEYS`为外部PEM公钥路径JSON数组。当前研究权重未签名。不是全平台发布签名门禁。
- 用户最新要求：完整功能最小化打包，100MB为争取目标，不以删除必要依赖/功能或把下载器体积当完整包来满足。现有品牌文件修改继续保留。

### 2026-11-04 页面简化与整合回归

机理实验室已改为“新建分析→选类型→抽屉导入”，主区只展示记录、关键值与结果图，参数/追溯折叠。真实CSV报告、RUL/执行器公开窗口、表盘照片的Web/API/导出/只读权限已分别验证。全量回归1269 passed / 2 skipped，194.44秒（尚不包含后续新增打包脚本）。页面提交e5fb46a，相关后端c3eba7c，表盘入口1e73a2d。

完整功能最小打包正在独立目录构建，不覆盖旧dist。旧dist约6.62GB，models/community/.venv-export嵌套开发环境约1.2GB，旧脚本整models加入是体积主因之一。新构建最终大小与exe运行验收尚待完成；不得把脚本完成写成安装包完成。

### 2026-11-04 最小运行包实测完成

发布脚本已提交9d1909c，详见docs/RUNTIME-PACKAGING.md。Windows便携ZIP为.v0/runtime-build-v1/stepeye-runtime-windows.zip，567,555,113字节，解压998,355,022字节；未达到100MB，模型压缩项本身约335.72MB。259项资产SHA一致，ZIP CRC通过。独立EXE实际登录/静态页/文档worker/表盘/RUL通过；包内Monibuca配置启动HTTP200、MediaPipe三模型3帧通过；自建进程均停止。21项打包专项检查通过。训练环境、Qwen本地附加组件、厂商CTI不含在主包；含评估资产，不代表商用再分发许可已获批。安装器、升级、程序签名、Linux独立构建仍待完成。用户原有品牌六文件与未跟踪图标/测试继续保留。

### 最新优先级：模型与核心功能

用户明确后续优先专用模型与核心功能：可用公开权重接入/公开数据训练、实时检测与告警处置、样本导入标注增强评测发布、SOP动作识别。界面美化与运维扩展降级，仅修阻塞项。当前打包收尾修复frozen MCP入口与WebSocket依赖，v2独立EXE的MCP六只读工具握手、带登录Cookie的WS hello及原有真实推理检查均通过；24项打包专项通过。最终体积和资产路径以docs/RUNTIME-PACKAGING.md的v2条目为准，不再使用v1作为当前推荐包。
