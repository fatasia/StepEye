# 本机 Qwen3-VL 辅助复核

此选项给需要内网图片复核的 StepEye 部署使用：YOLO 与规则继续负责实时判定，Qwen 只处理显式请求的辅助任务。默认不启动模型，不修改现有 `.env`，无需 Docker 或向主 Python 环境安装 torch。

## 固定资产

- 官方模型：[Qwen/Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/tree/52d6c8ffea26cc873ac5ad116f8631268d7eb503)，提交 `52d6c8ffea26cc873ac5ad116f8631268d7eb503`，模型卡 Apache-2.0。
- 语言模型 `Qwen3VL-2B-Instruct-Q4_K_M.gguf`：1,107,409,952字节。
- 官方视觉投影 `mmproj-Qwen3VL-2B-Instruct-F16.gguf`：819,394,848字节；不是第三方替代投影。
- 原生运行时：[llama.cpp b12064](https://github.com/ggml-org/llama.cpp/releases/tag/b12064)。Windows CUDA12.4与Ubuntu CUDA12.8资产及官方SHA256固定在 `sop-vision/deploy/qwen-local-manifest.json`，运行时与CUDA库分别下载。

Q4_K_M 是量化模型，不能称“精度无损”或“最快”。F16 投影也不能消除语言模型量化与场景差异的影响。

## 本轮实际状态（2026-11-03）

- 本机 RTX4060 Laptop，8188MiB显存；开始前整卡已用约1480MiB。该数是系统当前占用，不是Qwen占用。
- 检查了现有 LM Studio/Ollama 模型目录，没有发现可复用的Qwen GGUF。LM Studio存在 `llama-server.exe`，但 `--version` 直接返回 `0xC0000135`，缺失运行依赖；没有修改这套安装。
- 官方API与系统curl获取固定元数据成功；Python requests 曾出现TLS错误，下载工具保留证书校验，改用系统curl。
- 大文件下载未完成，`.part` 保存已取得字节，可继续下载；以 `.v0/qwen-local/download-status.json` 为最终本次下载状态。未完成SHA256校验的文件不改名为正式GGUF、不加载。本轮已结束：语言模型41,951,359字节、投影34,568,842字节、llama运行包20,166,751字节；CUDA DLL包未取得数据。curl分别返回56/28/28/35，未继续循环重试。
- **尚未取得Qwen完整单图响应，冷/热延迟、任务准确率与Qwen显存占用均未实测。** CLI参数已按官方文档核对，尚未在下载完整的本机二进制上运行验证。

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
