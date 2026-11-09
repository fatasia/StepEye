# Windows便携运行包

这是StepEye当前Web与CPU推理功能的Windows x64便携包，不是安装器。

- 最终v2 ZIP **567,844,000字节（567.84MB）**；解压 **998,660,260字节**，1432个文件。
- 原本机目录6,619,214,515字节；本轮不带训练torch/CUDA、嵌套导出venv、缓存、退役模型和凭据，解压体积减少约84.9%。
- 保留MediaPipe、ORT、OpenCV/FFmpeg、SciPy、OCR/读码、MCP、通知和机理入口；当前注册的本机模型按白名单保留。
- **未达到100MB**：模型文件在同一ZIP内就占335,715,960字节，不含Python、页面或推理库。

本次是显式 `--allow-evaluation-assets` 的本机评估构建，不表示模型或Monibuca已经获准商用再分发。

## 本次产物与证据

产物：`.v0/runtime-build-v2/stepeye-runtime-windows.zip`。v1保留供追踪，不作最终交付。

```text
SHA256 cb6b3c4b345d8741a9f617f19aaaa223c0d4328a43107b62bd32159ec6162f61
```

v2做一次ZIP/Deflate level9压缩，含CRC核验89.31秒；完整CRC通过、重复归档路径0。
v1发现MCP stdio入口和WebSocket依赖缺项后，必要重建为v2；没有反复调压缩参数试压。
未用联网安装器体积替代运行包体积，没有用UPX或重导出模型追求100MB。

| 组成 | 解压字节 | 同一ZIP中压缩字节 |
|---|---:|---:|
| 当前模型与模型元数据 | 441,201,637 | 335,715,960 |
| OpenCV与FFmpeg | 143,789,497 | 53,377,527 |
| Monibuca及配置/说明 | 74,179,343 | 约30MB |
| MediaPipe原生库 | 66,744,832 | 见归档分组报告 |
| SciPy模块，不含其BLAS DLL | 60,187,136 | 见归档分组报告 |
| ORT | 35,319,248 | 见归档分组报告 |

旧包的主要问题是整目录收集 `models/`，其中 `community/.venv-export` 约1.2GB；
又叠加宿主torch/CUDA和重复DLL。本次只收集声明资产，不删除旧包或用户资源。
OpenCV两种distribution使用同一命名空间，最终归档只保留一份cv2.pyd和一份FFmpeg DLL，没有叠放两套。

检查记录位于同一构建目录：
`assets-before.json`、`assets-after.json`、`size-report.json`、`installed-runtime.txt`、
`smoke-result.json`、`build-final.log`。v2的259项运行资产SHA一致，包含已核v6配置和便携使用说明；
v1的数据补充前后记录仍保留在v1目录。

## 构建与启动

不改原品牌打包bat。新入口与spec独立，默认不覆盖已有产物：

```powershell
python -m venv .v0/runtime-build-env
.v0/runtime-build-env/Scripts/python.exe -m pip install -r sop-vision/deploy/requirements-runtime.txt
.v0/runtime-build-env/Scripts/python.exe sop-vision/deploy/build_runtime.py --output .v0/runtime-build-new --allow-evaluation-assets
```

`deploy/runtime-assets.json` 是资源白名单；`runtime_build.spec` 保留必要推理依赖，排除训练/开发栈。
未清许可资产需要明确opt-in，构建不自动下载权重、不复制 `.env`、认证DB或私钥。
Windows本机已构建；Linux原生包本轮未构建或验证，不把Windows EXE当Linux产物。

解压后在包目录创建自己的管理员并启动；`$Data`不放在程序临时目录：

```powershell
$Data = Join-Path $env:LOCALAPPDATA 'StepEye/data'
.\stepeye-runtime.exe passwd admin --role admin --data $Data
.\stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --host 127.0.0.1 --port 8000 --auth --data $Data --inspection .\_internal\configs\inspection.yaml
```

浏览器打开 `http://127.0.0.1:8000`。不要只移动EXE；`_internal`含Python运行库、页面和模型。
包里包含只读visual源码，配置了外部训练/读码Python时仍可通过 `-m visual.*` 导入；主EXE走冻结模块。

MCP stdio无需另装Python，可直接运行：

```powershell
.\stepeye-runtime.exe -m visual.mcp_server --transport stdio
```

在本机MCP客户端环境中配置 `STEPEYE_MCP_API_URL` 和管理员签发的 `STEPEYE_MCP_TOKEN`。
令牌不放进命令参数。v2已完成真实stdio initialize/tools/list，返回6个只读工具，验证期间业务请求数为0。

## 原生媒体另行启动

包内Monibuca实测为6.0.0，74,176,000字节；binary SHA256
`d9106e891ea1c5588b76cf348511959a86d571b3472b00367463b2b9d5cff60e`。
`runtime-v6.yaml`使用真实WHEP验证配置的 **listenaddr/portrange**，不是早期未复核模板的拼写。
独立启动，不由StepEye主服务常驻拉起：

```powershell
cd .\_internal\deploy\monibuca
$env:M7S_ADMIN_DEV_MODE = '1'
.\monibuca_windows_amd64.exe -c .\runtime-v6.yaml
```

这是loopback本机评估模式；对外部署另行配置认证，不沿用开发模式。
启动StepEye的终端设置 `MONIBUCA_URL=http://127.0.0.1:18773`、
`MONIBUCA_WHEP_PREFIX=/webrtc/whep`；工位还需绑定存在的流。
没有媒体进程/实际流时不会自动出现原生预览。
包内binary的help/version、临时配置HTTP启动200已通过，测试进程已停止；本轮不重复摄像机拉流。

## 外部组件与未完成项

本地Qwen不混入最小本体：两份GGUF合计 **1,926,804,800字节**，当前llama-server及CUDA DLL
另 **1,232,615,424字节**，合计 **3,159,420,224字节**，不含重复下载ZIP。
构建器提供 `--include-local-vlm`，但本轮未构建/压缩该变体，不能将当前567MB称为包含本地Qwen的全离线AI包。

训练沿用外部 `STEPEYE_TRAIN_PYTHON`；DINO特征、训练torch/CUDA不属于主运行库，默认良品异常检测仍用现有grid路径。
工业相机需要厂商CTI/驱动。云VLM、通知需要自己的服务配置和凭据，均不随包复制。
目前没有安装向导、自动升级、系统服务注册或EXE代码签名；许可未清的评估模型/媒体binary不能直接标作商用发布包。

## 已验证

24项入口/资源清单/MCP验包工具测试通过。真实v2冻结EXE验证：help、MCP help与stdio握手、
文档解析的module与legacy脚本派发、gauge/RUL真实模型子进程、匿名401、随机本机管理员登录、
登录Cookie建立 `/ws` 并收到hello、health和静态HTML全部通过。运行包显式保留websockets 15.0.1。
另用3帧协议视频加载手/姿态/通用目标三个MediaPipe模型并正常退出；不属于识别准确率测试。
仿真配置的loop已在有限视频验证中明确关闭；首次保留loop导致的超时记录在报告中。
所有测试只停止自己启动的进程，未接管已有媒体服务。

## 安装与升级（2026-11-09）

- 安装：`deploy/install_windows.ps1 -Zip <发布ZIP> -Version <版本> [-Sha256 <摘要>]`。默认装到 `%LOCALAPPDATA%\StepEye`，已有目录先改名备份，识别 ZIP 单顶层目录，创建开始菜单快捷方式；不写系统目录、不注册服务、不改防火墙。
- 升级：`deploy/make_release_manifest.py` 从 ZIP 生成 `{version,archive,sha256,size,notes}` 清单；`deploy/upgrade_windows.ps1 -Manifest <清单>` 停进程后调用 `python -m visual upgrade`。升级器拒绝降级/同版本、拒绝绝对路径/`..`/符号链接、解压上限 8GB、SHA256 不符即中止；目录切换为 current→backup、staged→current，第二步失败自动回滚。
- 回滚：旧版本保留在 `<安装目录>.bak-<时间戳>`；人工回滚即停进程后把备份目录改回原名。
- Linux 构建：`deploy/build_runtime.py --platform-label linux --monibuca <linux二进制>` 生成 `stepeye-runtime-linux.zip`；模型权重不入 Git，需在带资产的主机上构建，CI 不能单独凑齐。
- 仍未提供：单文件 EXE 安装包、EXE 代码签名、系统服务注册、自动更新检查（需证书/Inno 或 NSIS 工具链与发布服务器）。
