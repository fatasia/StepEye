# StepEye Windows x64 便携运行包

完整解压后运行，不要单独移动EXE或删除 `_internal`。这是CPU推理评估包，不是安装器；其中评估模型和Monibuca的商用再分发仍需许可审核。

在解压目录打开PowerShell，创建自己的管理员（交互输入密码），再启动：

```powershell
$Data = Join-Path $env:LOCALAPPDATA 'StepEye/data'
.\stepeye-runtime.exe passwd admin --role admin --data $Data
.\stepeye-runtime.exe serve .\_internal\configs\demo_sim.yaml --host 127.0.0.1 --port 8000 --auth --data $Data --inspection .\_internal\configs\inspection.yaml
```

浏览器访问 `http://127.0.0.1:8000`。仿真画面只用于功能演示，接入真实摄像机后再建立工位和质量规则。数据保存在上面的独立目录，升级前先备份。

## 可选原生媒体服务

StepEye不自动启动Monibuca。需要WHEP预览时，在另一个终端明确启动：

```powershell
cd .\_internal\deploy\monibuca
$env:M7S_ADMIN_DEV_MODE = '1'
.\monibuca_windows_amd64.exe -c .\runtime-v6.yaml
```

此模式只供本机评估，管理/RTMP端口绑定loopback；对外部署另行配置访问认证，不沿用开发模式。
启动StepEye的终端中配置 `MONIBUCA_URL=http://127.0.0.1:18773`、`MONIBUCA_WHEP_PREFIX=/webrtc/whep` 后重启StepEye；还需给工位绑定实际存在的流。没有流或没启动媒体服务时不会得到原生预览。

## 外部组件

训练仍使用管理员配置的 `STEPEYE_TRAIN_PYTHON` 隔离环境；包里不带torch/CUDA训练库。工业相机需要设备厂商的CTI/驱动。
本地Qwen需要单独的GGUF、mmproj及llama-server运行库；云VLM需要自己的API配置。主程序包含这些入口，但没有把未配置服务当作已可用模型。
不要把本机 `.env`、认证数据库、私钥或训练缓存复制给其他使用者。
