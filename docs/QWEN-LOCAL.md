# 本机 Qwen3-VL 辅助复核

此选项给需要内网图片复核的 StepEye 部署使用：YOLO 与规则继续负责实时判定，Qwen 只处理显式请求的辅助任务。默认不启动模型，不修改现有 `.env`，无需 Docker 或向主 Python 环境安装 torch。

## 固定资产

- 官方模型：[Qwen/Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/tree/52d6c8ffea26cc873ac5ad116f8631268d7eb503)，提交 `52d6c8ffea26cc873ac5ad116f8631268d7eb503`，模型卡 Apache-2.0。
- 语言模型 `Qwen3VL-2B-Instruct-Q4_K_M.gguf`：1,107,409,952字节。
- 官方视觉投影 `mmproj-Qwen3VL-2B-Instruct-F16.gguf`：819,394,848字节；不是第三方替代投影。
- 原生运行时：[llama.cpp b12064](https://github.com/ggml-org/llama.cpp/releases/tag/b12064)。Windows CUDA12.4与Ubuntu CUDA12.8资产及官方SHA256固定在 `sop-vision/deploy/qwen-local-manifest.json`，运行时与CUDA库分别下载。

Q4_K_M 是量化模型，不能称“精度无损”或“最快”。F16 投影也不能消除语言模型量化与场景差异的影响。

## 本轮实际状态（2026-11-03）

四份固定资产全部完成 SHA256 校验，Windows 原生 CUDA 运行包已解压。TLS1.2 兼容下载仍验证证书；镜像仅传输同一固定哈希的模型字节。

RTX4060 Laptop 实际单图探针（1024×576 电子装配照片，Q4_K_M + F16 投影）：

| 项目 | 实测 |
|---|---|
| 服务启动到就绪 | 2.537秒 |
| 首次请求 / 热请求 | 0.740秒 / 0.431秒 |
| 冷启动到首个完整响应 | 3.277秒 |
| 整卡已用显存：基线 / 峰值 / 停服后 | 1697 / 4595 / 1697 MiB |

两次输出分别为53和25 token，均正常结束；不是固定输出长度吞吐对比。图片未显示人员头部，模型却两次给出不同的安全帽判断：**本次响应速度通过，安全帽事实判断失败**。因此仍仅作辅助，不接管实时安全判定，也不能据此声称量化精度合格。服务已停止，原有云端配置未改变。

记录：`.v0/qwen-local/probe-complete-20261103.json` 及同名 server.log；图片 SHA256 `bdfc9c585f978345416fa1591d1b6c167b91f5a6a1be39de5e544d3715f9b30a`。Linux 原生推理尚未实测。

## 最短安装与探针

在仓库根目录运行。每个资产最多等待300秒，最多2个下载并发；超时保留部分文件，不循环重试：

```powershell
python sop-vision/tools/qwen_download.py --timeout 300 --extract
```

再次运行同一命令继续下载。已有正式文件先核SHA256，通过就跳过；哈希不符保留文件并报错，不覆盖。压缩包只有校验后才解压；失败状态写入下载报告，不标安装完成。完整通过后：

```powershell
python sop-vision/tools/qwen_probe.py --image sop-vision/.v0/test-helmet.png --out .v0/qwen-local/probe-01.json
```

探针只监听 `127.0.0.1`，使用随机临时API令牌，固定4096上下文、单槽、256输出token。一次冷请求与一次热请求均保留完整OpenAI兼容JSON，记录加载时间、请求时间和整卡GPU占用；结束或失败都会终止自己启动的服务。整卡峰值包含其他进程，不能冒充Qwen独占显存。仅一次图片探针也不是准确率或稳态吞吐基准。

官方参数来源：[多模态支持](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)、[server接口](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)。`/health` 无需API key，加载中503、就绪200；`/v1/chat/completions`使用临时授权。GPU探针使用 `--n-gpu-layers 99`；0代表语言层CPU路径，不能标为GPU推理。

## StepEye 按需托管配置

文件齐全并试测后，由部署者在启动环境设置绝对路径；不是本轮自动写入配置：

```text
STEPEYE_LOCAL_VLM_BIN=<llama-server绝对路径>
STEPEYE_LOCAL_VLM_MODEL=<Q4_K_M GGUF绝对路径>
STEPEYE_LOCAL_VLM_MMPROJ=<官方F16 mmproj绝对路径>
STEPEYE_LOCAL_VLM_PORT=18081
STEPEYE_LOCAL_VLM_IDLE_S=60
```

托管模式首次请求才启动，空闲默认60秒释放；Provider自动使用本地别名、最多256输出token、0次重试、15秒请求超时和120秒冷启动预算。不需要伪造云API key，不改变现有云端配置文件。返回因长度被截断时不接受部分PASS结果。

已有独立本地服务的手动Provider配置可使用 `ZHIPU_MAX_TOKENS=256`、`ZHIPU_MAX_RETRIES=0` 与 `ZHIPU_TIMEOUT=15`；这些预算控制不能当作实际响应速度保证。

工具离线检查：`python sop-vision/tools/qwen_selftest.py`。Windows实机运行与Linux目标机运行分别验收；提供Linux下载清单不等于Linux已实测。
