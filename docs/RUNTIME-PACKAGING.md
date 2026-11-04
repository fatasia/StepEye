# Windows便携运行包

这是StepEye当前Web与CPU推理功能的Windows x64便携包，不是安装器。

- 实际ZIP **567,555,113字节（567.56MB）**；解压 **998,355,022字节**，1423个文件。
- 原本机目录6,619,214,515字节；本轮不带训练torch/CUDA、嵌套导出venv、缓存、退役模型和凭据，解压体积减少约84.9%。
- 保留MediaPipe、ORT、OpenCV/FFmpeg、SciPy、OCR/读码、MCP、通知和机理入口；当前注册的本机模型按白名单保留。
- **未达到100MB**：模型文件在同一ZIP内就占335,715,960字节，不含Python、页面或推理库。

本次是显式 `--allow-evaluation-assets` 的本机评估构建，不表示模型或Monibuca已经获准商用再分发。

## 本次产物与证据

产物：`.v0/runtime-build-v1/stepeye-runtime-windows.zip`。

```text
SHA256 16b1fae029ccaa6de537d1fd36bb946b23ae037094bf7bc432843a820ffe095d
```

仅做一次ZIP/Deflate level9压缩，85.51秒；完整CRC通过、重复归档路径0。
未用联网安装器体积替代运行包体积，没有用UPX或重导出模型追求100MB。

| 组成 | 解压字节 | 同一ZIP中压缩字节 |
|---|---:|---:|
| 当前模型与模型元数据 | 441,201,637 | 335,715,960 |
| OpenCV与FFmpeg | 143,789,497 | 53,377,527 |
| Monibuca及配置/说明 | 74,178,915 | 29,992,331 |
| MediaPipe原生库 | 66,744,832 | 见归档分组报告 |
| SciPy模块，不含其BLAS DLL | 60,187,136 | 见归档分组报告 |
| ORT | 35,319,248 | 见归档分组报告 |

旧包的主要问题是整目录收集 `models/`，其中 `community/.venv-export` 约1.2GB；
又叠加宿主torch/CUDA和重复DLL。本次只收集声明资产，不删除旧包或用户资源。
OpenCV两种distribution使用同一命名空间，最终归档只保留一份cv2.pyd和一份FFmpeg DLL，没有叠放两套。

检查记录位于同一构建目录：
`assets-before.json`、`assets-after.json`、`assets-supplement-before/after.json`、
`size-report.json`、`archive-verification.json`、`installed-runtime.txt`、`build.log`。
259项运行资产SHA一致；其中2项补充为已核v6配置和便携使用说明，保留独立前后记录。

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

21项入口/资源清单测试通过。真实冻结EXE验证：help、文档解析的module与legacy脚本派发、
gauge/RUL真实模型子进程、匿名401、随机本机管理员登录、health和静态HTML全部通过。
另用3帧协议视频加载手/姿态/通用目标三个MediaPipe模型并正常退出；不属于识别准确率测试。
仿真配置的loop已在有限视频验证中明确关闭；首次保留loop导致的超时记录在报告中。
所有测试只停止自己启动的进程，未接管已有媒体服务。
