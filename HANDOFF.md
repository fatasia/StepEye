# 交接清单(2026-11-03)——新会话接手必读

> 本项目从 Copilot 断点抢救至今:**30 个提交,130/130 测试,公开仓库 https://github.com/fatasia/StepEye**。
> 新会话开始时:先读本文件 + `docs/ROADMAP-v2.md`(终版清算)+ `git log --oneline`(30 条即全部历史)。

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
| **服务** | `visual/server.py` | 130 端点;**铁律:pydantic body 类只允许模块级**(守卫测试 test_server_hygiene 已建) |
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

## 五、剩余任务(新会话按此开工,全部有验收标准在 ROADMAP-v2 追加批次)

| # | 任务 | 量 | 备注 |
|---|---|---|---|
| T21 | **Monibuca 集成**:下载 v6(Rust,<20MB 单二进制,永久免费)→ config 模板已备(`deploy/monibuca/config.yml`,注意 GB28181 字段名以 v6 仓为准)→ 实时页接 WebRTC 预览(WHEP) | 2 | 插件生态直接用 |
| T22 | **GIS 厂区图前端收尾**:`sitemap.js` 已写(平面图打点/伪 3D/告警闪红),**未接路由**(main.js 未注册 sitemap)且未视觉验收;css 已加 | 0.5 | 需 Playwright 交互验收 |
| T23 | **影子真值标注 UI**:alerts 页已加面板(上轮未验证渲染),或独立页;report 闭环端点已备 | 0.5 | |
| T24 | ONVIF/GB28181 真机联调 | 2 | 需现场相机/wvp 实例 |
| T25 | 烟火/PPE 自训上岗 | 5 | 需标注数据+云 GPU |
| T26 | 录像回放调阅 UI(R3) | 3 | Monibuca record 插件 |
| T27 | 巡更到位 UI | 2 | |
| T5/T6 尾 | 登录后前端全量交互回归(认证模式) | 0.5 | |

**环境注意**:本机 conda 的 torch 曾被 ultralytics 安装搞坏(shm.dll),已用 `pip install --force-reinstall --no-deps torch==2.12.0+cu130` 恢复;**cv2 与 torch 同进程仍有 DLL 冲突**(DINO 特征路径隔离为 `extract_features_dino`,网格特征兜底不受影响)。爬坑记录详见各测试文件 docstring 与 commit message。

## 六、纪律(不可协商,全 session 实践过)

1. 每步:先读后写 → 130+ 测试全绿 → 提交 → 推送
2. UI 改动必须 Playwright 交互验收(项目自带 tools/shoot.py + .v0/interact_*.py 模式)
3. VLM/免训练判定:**评测集未建不承诺准确率**;UNCERTAIN 转人工;能力边界卡交付
4. pydantic body 类只允许模块级(守卫测试会拦)
5. 密钥只进 .env(gitignore);lk888 key 已在对话泄露,**需轮换**
