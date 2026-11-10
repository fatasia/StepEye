# Monibuca 原生预览部署

StepEye 在 Windows、Linux 上运行同一套 Python 服务与 Web 页面；Monibuca 是独立原生进程，不要求 Docker。

- 浏览器通过 StepEye 的认证接口协商 WHEP，媒体经 WebRTC 直连服务器。
- 连接失败自动回到 MJPEG 分析画面；可手动切换、重试。WebRTC 原始画面不包含检测叠加。
- 切换工位、离开页面会关闭 PeerConnection；上游提供会话 Location 时同时 DELETE 释放。
- 局域网默认不使用公网 STUN；跨网段、NAT 与企业防火墙需另配 ICE/TURN 并实测。

## 1. 安装与启动

在仓库根目录执行：

```text
python deploy/install_monibuca.py
```

安装器仅下载[官网 v6 原生包](https://monibuca.com/guides/download/)，失败即退出，不自动换版本。可传 `--sha256` 校验可信渠道提供的摘要；未提供时 manifest 的 SHA256 只是本次下载记录。

在安装目录启动（Windows）：

```powershell
cd data/monibuca
.\monibuca.exe -c config.yml
```

Linux：

```sh
cd data/monibuca
./monibuca -c config.yml
```

模板使用[官网 v6 WebRTC](https://monibuca.com/protocols/webrtc/)、[MP4](https://monibuca.com/plugins/mp4/)的配置字段。官网总配置页与插件页存在部分字段命名差异，取得实际 v6 包后必须检查加载日志与生效配置。

## 2. 接线

在 `.env` 配置（地址只留在服务端）：

```dotenv
MONIBUCA_URL=http://127.0.0.1:8180
MONIBUCA_WHEP_PREFIX=/webrtc/whep
```

工位 YAML 对应同一条流：

```yaml
source:
  type: rtsp
  uri: rtsp://127.0.0.1:8554/live/camera01
  preview_stream: live/camera01
```

先通过 Monibuca 的摄像头拉流配置/API 建立 `live/camera01`，再启动工位；StepEye 不会根据任意浏览器 URL 自动拉取外部流。

HTTP 管理端口 8180 仅监听回环；StepEye Web 端口对局域网开放，并启用 `--auth`。另向浏览器所在网络放行配置的 ICE 媒体端口。HTTPS 由可信反向代理终止；不要把 Monibuca 管理端口直接暴露到公网。

Windows 平台服务：`deploy/start_windows.ps1`；Linux：`sh deploy/start_linux.sh`。先用 `python -m visual passwd admin --role admin --data data` 建用户，再启动。Linux 应在独立 venv 安装依赖，MediaPipe wheel 是否支持目标 Python/CPU 需在目标机验证。

## 3. 验证记录（2026-11-03）

| 项目 | 结果 |
|---|---|
| 官方 v6 Windows URL | HTTP 404；beta URL HTTP 403，未安装 v6 |
| 原生协议实验环境 | 官方 GitHub Release v5.1.6 Windows 已下载并启动，无 Docker |
| v5 协议兼容 | 显式设置 `/webrtc/play`；FFmpeg H.264→RTSP→Monibuca→StepEye→Chromium，实际 640×480 视频持续播放 |
| UI | 已验证 WebRTC→分析画面→重试→切页，零 pageerror；截图 `.v0/ui-t21/webrtc.png` |
| v6／Linux／跨机器 | 尚未运行验证，不据此宣称完成 |

v5.1.6 源码采用 AGPL-3.0，仅作为本次独立协议实验环境，不是 v6 的生产替代或授权结论。v6 官网声明预编译包可免费用于生产，但安装文件、许可和行为仍需按实际取得的版本登记。

录像 UI 与真实录像服务联调结果另列；协议模拟器不替代相机真机验收。

ONVIF 使用可选的 `requirements-camera.txt`（已核对 onvif-zeep-async 4.3.0）。自动发现另由 `requirements-discovery.txt` 启用；WSDiscovery 2.1.2 声明 LGPLv3+，不纳入默认平台或 EXE 依赖，分发前单独登记。默认仅访问 RFC1918/IPv6 ULA 管理地址；实验回环地址需显式配置 `ONVIF_ALLOWED_CIDRS`。

本机独立 ONVIF Simulator v0.4.0 已跑通 SOAP 取流与 RTSP 实际 640×480 帧，GeoLocation 未提供，返回 unknown；多播发现本机未发现设备，不能据此标为真机发现通过。WVP 已按官方接口校正 form/MD5 登录、分页及坐标校验，但没有可用的授权 WVP/SIP 实例，仍待端到端联调。

## 4. 原生录像调阅

Monibuca 与 StepEye 共机时，在 `.env` 配置 `MONIBUCA_RECORD_DIR` 为录像目录的绝对路径。分机时挂载只读共享目录；平台进程只需读取权限。Windows/Linux 接口一致，不需要复制录像进应用数据库。

使用模板的 `{stream}_{date}_{time}.mp4` 平铺命名；运行 Monibuca 的系统时区设为 UTC+08:00（Linux 可设 `TZ=Asia/Shanghai`），页面起止时间统一标注北京时间。只索引已完成且可读取时长的 MP4；临时/损坏文件不显示为可播放录像。

页面「录像回放」按工位与时间窗口检索重叠片段，支持原生 HTML5 播放和 HTTP Range 拖动。浏览器通常需要 H.264 视频；H.265 是否能播放取决于浏览器，平台不会静默转码。当前单次结果最多 200 段、时间窗口最多 31 天。

此实现从实际目录读取文件，不依赖不同 Monibuca 版本的录像 REST 字段。没有录制文件、目录未配置、工位未绑定流均明确显示原因。真实 v6 录像启停、跨机目录和存储性能仍需使用实际发行包验证。
