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
