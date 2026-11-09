# StepEye · 工业视觉平台

用摄像头和设备信号看住一条产线：SOP 有没有做全、顺序对不对、安全是否合规、质量是否受控。不接 PLC、不改产线，每个工件周期留下可追溯证据。

当前版本 **v0.9.0**，Windows x64 安装包、便携运行包、可启动源码包都在 [Releases](https://github.com/fatasia/StepEye/releases/tag/v0.9.0)。

## 三种用法

| 方式 | 适合 | 入口 |
|---|---|---|
| Windows 安装包 | 直接装上就用 | `StepEye-Setup-0.9.0.exe` |
| 便携运行包 | 不写注册表，解压即用 | `stepeye-runtime-windows.zip` |
| 源码包 | 改代码、接自己的模型 | `stepeye-source-0.9.0.zip` |

安装包默认装到 `%LOCALAPPDATA%\StepEye`，创建开始菜单快捷方式；升级覆盖安装，`data\` 目录原地保留。卸载在“设置 → 应用 → StepEye”，同样保留数据。

### 安装包

双击 `StepEye-Setup-0.9.0.exe`，一路下一步。装完从开始菜单打开 StepEye，浏览器访问 <http://127.0.0.1:8000>。演示工位是仿真画面，不需要摄像头。

需要登录时：

```powershell
cd "$env:LOCALAPPDATA\StepEye"
.\stepeye-runtime.exe passwd admin --role admin --data data
.\stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --port 8000 --auth --data data
```

### 源码包

解压后双击 `START-WINDOWS.bat`（Linux/macOS 用 `START-LINUX.sh`）。脚本会建虚拟环境、装依赖、启动演示工位。

手动启动：

```powershell
cd sop-vision
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m visual serve configs/demo_sim.yaml --port 8000
```

源码包带演示所需的 MediaPipe 手部/姿态/目标检测模型；训练和评测用的大模型不在包里，需要时从 `docs/MODEL-*.md` 记录的来源获取。

## 能做什么

| 领域 | 已落地 |
|---|---|
| 实时监控 | 多工位画面、SOP 状态机（步骤/顺序/节拍）、WebSocket 推送 |
| 安全合规 | 区域入侵、烟火、PPE 逐人关联、人车距离/限速、跌倒、睡岗、温场信号、Modbus 只读桥 |
| 仓储盘点 | 区域计数上下限、通道占用、货箱计数、账实核对与不可变对账报告 |
| 质量视觉 | 拍照即检、读码/OCR、SPC/GR&R/柏拉图、尺寸测量、设备结果复判、电芯六面汇总、连接器针脚 |
| 模型生命周期 | 登记 → 试测 → 标注 → 冻结数据 → 固定 SHA 评测（逐图 FP/FN + COCO 12 项 AP/AR）→ 审批/激活/回滚 |
| 告警治理 | 风暴抑制、同键冷却、规则预算、误报分析；计数重启不归零 |
| 机理实验室 | 轴承包络谱、阀门双证据、电机/执行器分类、趋势与 RUL 报告 |
| 知识与文档 | SOP 文档问答、动作参考卡、文档中心 |
| 运维 | 数据范围权限、备份恢复、存储配额、通知渠道、离线升级与回滚 |

![二级导航](docs/assets/ui-navigation.png)

| | |
|---|---|
| ![固定模型评测](docs/assets/ui-holdout-eval.png) | ![模型发布管理](docs/assets/ui-release-manage.png) |
| 固定 SHA 评测：逐图 FP/FN + AP | 发布管理：审批 → 激活 → 回滚 |
| ![盘点对账](docs/assets/ui-stock-recon.png) | ![告警中心](docs/assets/ui-alerts-governance.png) |
| 盘点对账：三步出差异 | 告警中心：治理计数按需显示 |

## 这一版做了什么

- 修掉告警中心首次访问的 500：影子库并发建库加了锁和重试。
- 告警治理计数落盘，冷却时间可以在告警页直接改，重启后还在。
- SOP 配置页在 1366/1280 不再把标题和按钮挤成竖排。
- 视觉应用窄屏选中模型自动滚到详情；助手支持 Esc 和点外部关闭。
- 文件选择、录像空态、追溯空列、文档命名等一批交互细节统一。
- MOT17 跟踪评测：GT 纯关联 IDF1 97.5 / 96.0（IoU 0.3/0.5）。
- 动作对账可以带设备信号证据，离线升级支持 SHA256 校验和失败回滚。

完整变更见 [v0.9.0 Release Notes](docs/RELEASE-NOTES-v0.9.0.md)。

## 架构

FastAPI 单服务 + 零构建原生 JS 前端 + SQLite 证据库。模型、流媒体、采集都是可选组件，缺了就降级，不影响主流程。

```
sop-vision/
  visual/      规则引擎、感知、安全、质量、模型治理、路由
  web/         原生 JS 页面与样式，改完刷新即生效
  configs/     工位 YAML
  deploy/      打包、安装、升级、Monibuca
  tools/       训练、导出、评测、巡检脚本
  tests/       pytest 全量用例
```

更细的导览见 [TECH-ARCHITECTURE](docs/TECH-ARCHITECTURE.md)，逐项状态见 [STATUS-2026-11-09](docs/STATUS-2026-11-09.md)，剩余任务见 [REMAINING-2026-11-09](docs/REMAINING-2026-11-09.md)。

## 开发

```powershell
cd sop-vision
python -m pytest -q                              # 全量 1501 passed / 2 skipped
python -m visual validate configs/demo_sim.yaml  # 校验规则编译
node --check web/js/main.js                      # 前端零构建，语法单独检查
```

- 前端不用打包器，`web/` 改完刷新即可。
- 模型、代码、数据许可分开登记在 `models/public/*.json` 与 `docs/`。
- 工位上线：文档 → 步骤草稿 → 人工确认 → YAML → 热加岗。

## 文档

- [使用说明](docs/USAGE.md)
- [技术架构](docs/TECH-ARCHITECTURE.md)
- [功能与状态](docs/STATUS-2026-11-09.md)
- [剩余任务](docs/REMAINING-2026-11-09.md)
- [MOT17 评测](docs/MOT17-EVAL-2026-11-09.md)
- [运行包与安装升级](docs/RUNTIME-PACKAGING.md)

## 范围

代码和模型在仿真、公开数据与固定 holdout 上验证过；现场精度以实际部署验收为准。运行包内含评估模型与 Monibuca v6 beta，商用再分发前按 `docs/` 里的许可记录逐项确认。

代码许可见仓库内 LICENSE 与各文件头；第三方组件许可见 `docs/WEIHU-REUSE.md`、`docs/PUBLIC-*.md`。
