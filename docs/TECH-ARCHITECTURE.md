# StepEye 技术架构

面向接手开发的工程师。事实以代码为准，本文是导览。

## 1. 总体形态

- **单服务单体**：FastAPI（Python 3.10+）同时承载 REST API、WebSocket 实时推送、静态前端；无强制外部依赖（SQLite + 文件系统），可选组件（模型运行库、流媒体、采集桥）缺失时能力自动降级并如实上报，不悄悄坏。
- **前端零构建**：原生 ES Module + 单页哈希路由（`web/js/`），无 npm/打包链；改文件即生效。
- **部署**：源码 `python -m visual serve configs/*.yaml`，或 PyInstaller 打包单目录 exe（`deploy/build_windows.bat`，服务+前端+文档打进二进制）。

## 2. 目录导览

```
sop-vision/
├── visual/            # 后端全部业务
│   ├── server.py      # FastAPI 组装 + StationManager(工位生命周期/热加岗/重载)
│   ├── runtime.py     # 单工位运行时(采集→感知→规则→事件)
│   ├── dsl.py         # 配置模型:StationConfig/Step/Cond/Evidence(校验的唯一事实源)
│   ├── labels.py      # 标注库:确认制、冻结导出、组隔离、版本指纹(LV-)
│   ├── detection_holdout.py  # 固定 SHA 评测(模型×数据版本,逐图 FP/FN + AP)
│   ├── model_release.py      # 发布账本(candidate→approve→activate→rollback,不可变)
│   ├── alert_governance.py   # 风暴抑制/冷却/预算/误报分析
│   ├── ehs.py         # 安全规则引擎(入侵/烟火/计数/占道/睡岗)
│   ├── stock_reconciliation.py  # 盘点账实核对(期望版本化+不可变报告)
│   ├── sop_templates.py      # 行业工步模板(3C/锂电/汽车电子/仓储)
│   ├── action_reference_check.py # 参考卡×动作影子对账
│   ├── holdout_worker.py     # 评测子进程(私有文件请求,stdin 关闭)
│   ├── media.py / recordings.py # Monibuca v6 WHEP 信令桥 / MP4 调阅
│   └── *_routes.py    # 各领域 FastAPI 路由(权限双保险模式)
├── web/               # 前端(index.html + js/pages/* + css/*)
├── tools/             # CLI(评测/训练/备份恢复/数据准备)
└── tests/             # 1200+ pytest;浏览器验收走 Playwright
```

## 3. 关键设计约束

1. **不可变证据**：质量报告、机理报告、评测报告、盘点报告、发布账本一律 SQLite WAL + `RAISE(ABORT)` 触发器拒绝 UPDATE/DELETE；只追加修订行。
2. **发布生效路径**：`_apply_active_release`（server.py）在工位加载/重载/热加岗时读取账本——按**候选时存档的模型文件路径** + SHA 校验，不一致即跳过并广播 `release_skipped` 事件，绝不半套生效；回滚因此不依赖引擎登记的当前状态。
3. **评测隔离**：评测在子进程执行，请求走私有文件、stdin 关闭（吸取阀门回放大 stdin 阻塞的教训）；模型/数据 ZIP 先快照再校验 SHA。
4. **配置单一事实源**：工位 YAML；配方、向导都是它的生成器。程序化改配置走原子写 + 版本历史（recipes.py 模式）。
5. **告警治理链**：create → 风暴抑制（同键滑动窗口，超阈值聚合摘要）→ 冷却（`STEPEYE_ALERT_COOLDOWN_S`，默认关）→ 预算（每键每小时上限）→ 入库。治理计数全部如实进 `/api/alerts/stats`。
6. **权限**：viewer < operator < admin 三级；写操作按中间件 + 路由内双保险；MCP 令牌可带 project_ids 白名单做数据范围过滤。

## 4. 模型生命周期

登记（LabelEngines，仅本机文件路径）→ 试测（一次性 CPU 子进程）→ 标注项目 → 冻结数据版本 → 固定评测（AP/FP/FN，发布前必须）→ 发布账本审批激活 → 工位重载生效 → 回滚。评测报告 scope 恒为「导出 val 验证」，不产生生产许可。

## 5. 媒体与接入

- **流媒体**：Monibuca v6（外部部署，WHEP `/webrtc/whep`，兼容 v5 `/webrtc/play`）；信令桥持凭据，浏览器只访问 StepEye；录像按 `MONIBUCA_RECORD_DIR` 共享目录 MP4 调阅。
- **采集**：工业相机可选 Harvester/GenTL；信号源 Modbus 只读桥（未配置不运行）。
- **降级**：无 Monibuca 时用分析画面（503 + 明确指引），无权重时能力接口标不可用——不假死。

## 6. 测试与验收

- `python -m pytest -q`（从 sop-vision 目录）：全量 1200+，CI（Windows/Linux + MCP 四 job）守护。
- 浏览器验收：Playwright 真实交互脚本放 `.v0/`（不入 Git），截图人工复核；UI 改动必过一遍真实交互。
- 诚实条款：测试替身/合成信号/公开数据效果分开记录，不冒充现场验收。

## 7. 相关文档

- [使用指南](USAGE.md)
- [任务覆盖清单](TASK-COVERAGE.md)（B/S/Q/V 逐项状态）
- [检测验证方法](DETECTION-VALIDATION.md)、[模型资产审计](MODEL-ASSET-AUDIT.md)
- [机理实验室](MECHANISM-LAB.md)、[weihu 复用边界](WEIHU-REUSE.md)
- [MCP 接入](MCP.md)、[AI 文档问答](AI-DOCUMENTS.md)
