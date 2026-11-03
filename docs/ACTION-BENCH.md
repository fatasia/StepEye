# 动作旁路性能检查

`tools/bench_action.py` 测量本机动作 ONNX 子进程的单路延迟，固定 16 帧、224×224、batch=1，不扫描分辨率。

## 当前结果：VideoMAEv2 Small（2026-11-03）

已恢复官方完整 K710 蒸馏分类权重并导出 ONNX，真实公开片段在 PyTorch、ONNX CPU 与 CUDA 上均输出 **eating spaghetti**。未修改 runtime，不自动启用工业工步判定。

- 权重：`OpenGVLab/VideoMAE2` revision `706cc172d65ebd4dedbee3f9c0183a93df9fa125`，`distill/vit_s_k710_dl_from_giant.pth`，44334609 字节，SHA256 `24fb71687fa3671b8387cadfbcbab0f72af695692e93cf1ecc82caa888626172`。
- 原 44303649 字节残片缺 30960 字节；以相同 revision 断点补齐，完整 SHA 与官方 LFS 一致。官方/镜像普通 TLS 握手失败后，镜像 HTTP/1.1 + TLS1.2 成功，证书校验保持开启。下载进程已退出。
- `torch.load(weights_only=True)` 确认 module 内 162 个张量，head.weight `[710,384]`、head.bias `[710]`，不是纯 backbone。HF 仓库总标签 feature-extraction 不能代表此文件；[官方 Model Zoo](https://github.com/OpenGVLab/VideoMAEv2/blob/29eab1e8a588d1b3ec0cdec7b03a86cca491b74b/docs/MODEL_ZOO.md) 将它列为 K710 蒸馏分类模型。
- 官方代码 [MIT](https://github.com/OpenGVLab/VideoMAEv2/blob/29eab1e8a588d1b3ec0cdec7b03a86cca491b74b/LICENSE)；[官方 HF 权重卡](https://huggingface.co/OpenGVLab/VideoMAE2/tree/706cc172d65ebd4dedbee3f9c0183a93df9fa125) 声明 Apache-2.0。类别顺序直接采用同一官方代码 revision 的 `misc/label_map_k710.txt`，不自编类别。

### 实际导出与性能

ONNX：`sop-vision/models/action/videomaev2-small-k710.onnx`，91103149 字节，SHA256 `6748dbc33dfb4f83c581bfdd695c99c4f83654d63e87867d8b289f7fb40558a2`。同目录 `.classes.json`、`.manifest.json`、`.export.json` 可供动作旁路配置；权重仍不入 Git。

固定16×224、batch1、同一公开片段，仅5个热态样本：

| 链路 | 启动/加载 | 首推 | 热态 P50 / P95 |
|---|---:|---:|---:|
| 现有 ActionPredictor / ONNX CPU（2线程） | 子进程638 ms | 往返652 ms | 往返682 / 838 ms；纯推理650 / 811 ms |
| RTX4060 Laptop / PyTorch FP32 CUDA | 模型加载与传输2856 ms | 推理368 ms | 纯推理30.06 / 31.16 ms |

CPU 首次从启动到结果约1291 ms；GPU加载至首结果约3224 ms（不含Python导入）。GPU PyTorch峰值 allocated 229 MiB、reserved 258 MiB，不是整卡显存。GPU结果不等同于 ONNX Runtime CUDA 部署速度，ORT CUDA尚未验证。CPU当前未达到500ms末帧至结果目标。

公开 eating_spaghetti 片段：CPU class_id30、置信度0.71830；CUDA0.71844。导出时该片段 PyTorch/ORT logits最大绝对误差 `1.43e-5`。只使用单crop和一个均匀16帧片段，不等于作者多clip/multicrop评测，未测工业步骤准确率。

原始证据：`.v0/videomaev2-cpu-bench.json`、`.v0/videomaev2-gpu-bench.json`；所有推理子进程已结束，无常驻模型。

### 复现

复用 `.v0/action-venv` 与已审查的官方源码 `.v0/videomaev2-review`（commit `29eab1e8a588d1b3ec0cdec7b03a86cca491b74b`）。新导出工具同时锁定权重、源码文件、类别文件哈希；严格加载完整分类头：

```powershell
..\.v0\action-venv\Scripts\python.exe tools/action_export_v2.py --checkpoint ..\.v0\videomaev2-small.pth --source ..\.v0\videomaev2-review --clip ..\.v0\action-base\clip-normalized.npy --out models/action/videomaev2-small-k710.onnx
```

已有输出会拒绝覆盖。GPU评测工具是 `tools/bench_action_v2_torch.py`，CPU部署链仍用下方 `tools/bench_action.py`。默认CPU服务不导入torch。

## 输入契约

ONNX 必须只有一个 `float32 [1,3,16,224,224]` 输入和一个 `[1,类别数]` logits 输出；类别 JSON 是训练/导出时确定的唯一字符串数组。RGB 图像短边缩放到 256、中心裁剪 224，再 `/255` 和 ImageNet mean/std 归一化。模型必须与这套预处理一致；接口形状通过不代表模型语义通过。

推理在按需子进程中运行；调度器空闲释放时调用 `close()`，进程、结果线程与临时文件一起回收。运行默认不用 torch，CPU 使用已有 ONNX Runtime。CUDA 环境独立配置，不能把默认 CPU 结果标成 GPU。工具记录实际启用的 provider；CUDA provider 存在也不等于每个算子都在 GPU 执行。

## 单次检查

在 `sop-vision` 目录准备批准的 ONNX、类别 JSON、RGB uint8 NPY `[16,224,224,3]`，使用该 ONNX 的 SHA256：

```powershell
python tools/bench_action.py --model models/action.onnx --classes models/action-classes.json --clip data/action-clip.npy --sha256 <SHA256> --provider CPUExecutionProvider --runs 30 --out data/action-bench.json
```

隔离环境用 `--python <该环境的python路径>`。结果文件不覆盖已有证据；记录启动耗时、首次往返、热态推理与含 NPY/IPC 的往返 P50/P95。结束即退出，不留下服务。

这项检查不含采样历史、排队、采集循环、动作准确率和多工位负载。500ms 的末帧至结果目标须另在真实运行链路测量；现场验收还需动作真值与独立班次回放。

## 历史：V1 候选已停止（不再续传）

已固定作者组织发布的 [MCG-NJU/videomae-base-finetuned-kinetics](https://huggingface.co/MCG-NJU/videomae-base-finetuned-kinetics/tree/488eb9a0565f257b32866000305c8178965eb9f6)，revision `488eb9a0565f257b32866000305c8178965eb9f6`。config 明确为 VideoMAEForVideoClassification，16 帧输入、Kinetics-400 类别表，不是特征提取器。

**许可分别记录：** [VideoMAE 原作者代码](https://github.com/MCG-NJU/VideoMAE/blob/main/LICENSE) 与该官方分类权重模型卡均标注 **CC-BY-NC-4.0**；本次导出所用 Transformers 库为 Apache-2.0，仅研发评估候选，不登记为商用生产模型。原 VideoMAEv2 特征候选没有被替换或误作分类头。

- `model.safetensors`：346161616 字节，官方 SHA256 `065cfde56f97671da4d196e1a3a6d3cf9304dcad92eb671e3632a6c6cdd04f73`。
- 官方主机 TLS 握手失败后，使用公开 `hf-mirror.com` 的相同 revision；镜像元数据 SHA256 与官方文件页面一致，完整下载后仍须本地复核。TLS 校验未关闭。
- 当前下载状态：600 秒有界下载已结束（curl 28），`.v0/action-base/model.safetensors.part` 保留 120254313 / 346161616 字节可续传；尚未完成哈希校验、导出、真实模型推理或性能测量。没有继续后台下载。
- 已复用 `.v0/action-venv`，torch 2.12.0+cu130 / transformers 5.12.0 / onnxruntime 1.27.0；此环境 ORT 当前只有 CPU/Azure provider，不能声称 CUDA ONNX 已验证。

### 可复用导出工具

`tools/action_export.py` 只接受上述固定 SHA 的 safetensors；检查 trained classifier.weight 为 400×768、完整类别顺序、strict 加载权重。输出固定 `[1,3,16,224,224] → [1,400]` ONNX，并在 CPU 对照零输入 PyTorch/ORT logits 后写导出证据。导出脚本的 6 项静态/契约测试已通过；实际导出仍取决于完整权重。

```powershell
..\.v0\action-venv\Scripts\python.exe tools/action_export.py --source ..\.v0\action-base --out models/action/videomae-k400-eval.onnx
```

预处理差异必须保留：本模型原官方 processor `size=224`；StepEye 当前是短边 256 再中心裁剪 224。归一化 mean/std 一致，但不声称裁剪策略等价或保持论文精度。

已下载 [HF 官方文档贡献者的公开动作片段](https://huggingface.co/datasets/nielsr/video-demo/tree/main) eating_spaghetti.mp4（300 帧、30 FPS、1013655 字节，SHA256 `252f63d13748f08acf56765c295506bfdb8bb73b822e93a33a57d73988814a71`）。均匀选取16个不同帧并生成 `.v0/action-base/clip.npy`，采样证据 `clip.json`。片段仅用于本地动作接线评估，不作工业工步数据；视频仓库未声明独立许可证，不进入发行包。

`tools/bench_action.py` 现在将首次预测类别、置信度、top_k 一并写入性能报告，避免只有耗时而看不到输出内容。
