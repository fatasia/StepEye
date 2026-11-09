# StepEye v0.9.0

预发布版本，聚焦“治理与交互收尾 + 可量化跟踪评测 + 可回滚升级”。**不是生产验收版本**，模型精度与现场效果仍以各专项文档的边界为准。

## 本版重点

### 1. 稳定性与告警治理
- 修复影子库首次并发访问 500：`ShadowStore` 初始化加进程锁 + 退避重试。12 并发首访由 9×500 变为 12×200。
- 告警治理计数（预算/冷却/风暴）落盘，重启不再归零。
- 管理员可在告警页“治理设置”在线配置同键冷却 0–3600 秒，保存后立即生效并在重启后保留。

### 2. UI 与交互
- SOP 配置页头部在 1280–1600 宽度不再逐字竖排（1366 头部 138px→75px）。
- 视觉应用窄屏选中模型后自动滚到详情。
- 助手面板支持 Esc / 点击外部关闭。
- 12+ 处原生英文文件控件统一为“选择文件 / 未选择文件”。
- 录像无选中片段时隐藏空播放器；追溯无序列号时隐藏空列；文档页命名与导航统一。

### 3. 评测与证据
- MOT17：7 条 FRCNN 序列，GT 纯关联 **IDF1 97.5 / 96.0**（IoU 0.3/0.5），MOTA 99.6 / 98.8；检测轮 IDF1 53.0 / 51.6，FN 主要来自 FRCNN 供给。
- 动作对账新增设备证据列：每条影子对账可携带窗口内最近 30 秒设备信号（8KB 上限），只供复核、不参与判定。

### 4. 安装与升级
- 新增离线升级器：版本比较、SHA256 校验、路径穿越/符号链接拒绝、8GB 解压上限、原子切换与失败回滚。
- 新增 Windows 安装/升级脚本与发布清单生成工具。
- 运行包构建支持 `--platform-label`（Linux 产物需在带模型资产的 Linux 主机上构建）。

## 资产

| 文件 | 大小 | SHA256 |
|---|---|---|
| `StepEye-Setup-0.9.0.exe` | 488,920,895 字节 | `f92cf1e10d160a0590f9254fae20269615eb6f3adf3a823e5844c52b0dadcb29` |
| `stepeye-runtime-windows.zip` | 586,916,643 字节 | `6a297e2a281986b62cd30fdbb2fb5b4246429420a03219c60713b1dd27dff7c8` |
| `stepeye-source-0.9.0.zip` | 19,526,654 字节 | `24b73a54da8abc123b78203b0cc1dad7383e1ef79d4cbd0cb32d12b11eaaa232` |
| `stepeye-runtime-windows.manifest.json` | 升级清单 | 见文件内 |

安装包为 NSIS 自解压程序，默认装到 `%LOCALAPPDATA%\StepEye`，升级保留 `data\`。运行包是 Windows x64 便携目录包（onedir），含评估模型与 Monibuca v6 beta 原生二进制；评估资产的商用再分发许可仍需逐项审核。

## 安装 / 升级

```text
安装：双击 StepEye-Setup-0.9.0.exe，按向导安装
便携：解压 stepeye-runtime-windows.zip，运行 start_windows.ps1 或 stepeye-runtime.exe
源码：解压 stepeye-source-0.9.0.zip，双击 START-WINDOWS.bat

离线升级（带 SHA256 校验与回滚）：
python -m visual upgrade --manifest stepeye-runtime-windows.manifest.json ^
  --current <安装目录> --staging <空目录> --backup <新目录>
```

## 验证

- 全量测试：1501 passed / 2 skipped。
- UI 验收：17 项全过；16 路由 × 1920/1366/768 无页面级横向溢出、无 pageerror（登录前 401 除外）。
- 运行包：308 项资产校验通过（562,267,649 字节），ZIP CRC 由构建脚本校验。
- MOT17：结果与复现命令见 `docs/MOT17-EVAL-2026-11-09.md`。

## 已知边界

- 未做现场精度验收；所有模型指标均为公开数据/固定 holdout 口径。
- 未提供 EXE 代码签名、单文件安装包、系统服务注册、在线自动更新检查。
- 运行包 542–568MB，未达 100MB 目标。
- Monibuca v6 beta 的 MP4 录像触发 API 返回 404、AAC→Opus 不转码，均为上游限制。
- 跨机位仅地面几何候选，不做 ReID 身份合并。
- 完整剩余任务见 `docs/REMAINING-2026-11-09.md`。

## 变更

- `dfd32dd` 影子竞态 + UI/告警治理
- `cf428d7` MOT17 + 动作设备证据 + 离线安装/升级
- 本 Release 说明与剩余任务清单
