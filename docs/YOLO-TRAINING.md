# YOLO 训练执行器

训练执行器把已固定的数据版本和本机基座权重变成候选检测模型。它在单独 Python 进程中运行，不加载到实时采集服务，也不自动发布。

## 当前范围

- 本机 YOLO 检测 `.pt` 基座 → 训练 → 独立验证集评测 → 固定 640、batch=1、float32 原始输出 ONNX。
- 图像与标签、类别顺序、版本清单、基座都有 SHA256；训练前后重新核对，内容改变则不接纳结果。
- 每类必须在 train 和 val 中有正样本；相同图像哈希和清单中的同源组不能跨集合，合成样本不能进入验证集。
- 成功只得到 `candidate`，不写生产配置。单测使用假训练器验证流程；本机另完成了文末的单针孔真实训练实验。

## 输入规格

后台使用 `visual.training_worker.inspect_training_dataset(dataset_yaml)` 计算解压后的数据目录指纹。ZIP 自身的 SHA256 是另一个字段，不能替代这里的 `dataset_sha256`。

```json
{
  "dataset_yaml": "D:/StepEye/jobs/J001/data/dataset.yaml",
  "base_model": "D:/StepEye/models/approved.pt",
  "output_dir": "D:/StepEye/jobs/J001/output",
  "epochs": 30,
  "batch": 4,
  "device": "cpu",
  "imgsz": 640,
  "base_sha256": "管理员登记基座的64位SHA256",
  "dataset_version": "LV-固定版本ID",
  "dataset_sha256": "inspect_training_dataset返回的64位SHA256"
}
```

`epochs` 为 1–300，`batch` 为 1–64，`device` 仅 `cpu` 或单 GPU `0`，输入固定 640。输出目录必须不存在，历史结果不覆盖。后台只从已登记模型和已生成数据版本构造规格，不接受网页上传任意本机路径。

数据 YAML 只允许 `path/train/val/nc/names`：`path: .`、`train: images/train`、`val: images/val`。不接受 URL、下载指令、外部路径、图片列表、符号链接或分割标签。空标签必须在 `metadata.json.reviewed_negatives` 中登记为人工确认背景。平台导出版本的 `manifest.json` 同时校验图像、分组和确认状态。

## 原生运行

先由管理员准备独立环境及本机权重，环境需要兼容的 Ultralytics、PyTorch、ONNX、Pillow 和 PyYAML。执行器不安装依赖、不下载权重或数据、不修改实时服务环境。

在 `sop-vision` 目录运行：

```powershell
python tools/train_yolo.py --python D:/StepEye/train-env/Scripts/python.exe --spec D:/StepEye/jobs/J001/spec.json
```

Linux 使用对应环境的 `bin/python`。后台可直接调用该解释器运行 `visual/training_worker.py --spec ...`；使用 `-m visual.training_worker` 时需设置项目工作目录或模块搜索路径。命令行退出即释放进程；Ctrl+C 终止当前子进程。任务队列的总超时、取消和持久化由后台任务服务负责。

训练 worker 禁用第三方追踪集成、自动安装和 Python socket 出网，关闭可能触发额外模型下载的 AMP 检查。`workers=0`，不启动数据加载进程池。这是本机可信训练作业，不是执行任意不可信 `.pt` 文件的沙箱；基座必须经管理员批准并固定指纹。

## 输出与验收

`output/result.json` 与 `metrics.json` 包含候选 ONNX / checkpoint 路径及哈希、类别、基座与数据指纹、数据版本、train/val 数量、验证指标和总用时。训练参数保存在 `spec.json`，`train/weights/best.pt` 与 `best.onnx` 同目录，支持以该 checkpoint 为后续增强基座。

ONNX 要求单输入 `[1,3,640,640]`、单输出 `[1,4+类别数,候选点数]`，禁止动态维度、外置权重和内嵌 NMS。输出形状验证通过仍需平台实际推理回归，不等于自动获得准确率保证。

结构准入只检查可训练性；训练规模、光照/材质覆盖、近重复帧、标签正确性与许可审核仍按项目策略检查。完整预检工具为 `tools/training_preflight.py`。验证集用于模型开发评估，不替代独立测试集与目标工位影子验收。

## Web 一键训练 / 增强

管理员在本机 `.env` 设置 `STEPEYE_TRAIN_PYTHON` 为隔离环境解释器绝对路径，GPU 环境可设置 `STEPEYE_TRAIN_DEVICE=0`，默认 CPU。平台不会在启动时加载训练框架或创建训练线程。

用户在视觉应用选择模型 → 打开标注项目 → 导入样本并复核 → 点击“训练/增强”。后台固定当前确认版本，默认30轮、batch 4、输入640；同一次提交使用幂等键，避免网络重试重复训练。模型必须有同名、同目录的管理员批准 `.pt` 基座，ONNX 本身不能继续训练；新下载的仅ONNX评估模型不具备增强基座。

一个训练进程串行处理，最多4个活动任务，4小时超时停止；可取消，队列空后线程退出。服务重启将中断任务标为 `interrupted`，不自动重新花费GPU。成功候选回到视觉应用试跑，后续还需独立测试与人工发布审批，当前不自动改生产模型。

## 2026-11-03：实际训练记录

已完成 **CoatingVision 单针孔**实验，不是完整两类模型，也没有发布。数据来自[作者 Figshare v1](https://doi.org/10.6084/m9.figshare.29260121.v1)，CC BY 4.0；YOLOv8n 基座取自[Ultralytics 官方 v8.3.0](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt)，资产 ID 195719301，6,549,796 字节，SHA256 `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`。代码／权重 AGPL 条款与数据 CC BY 分开记录，商用发布尚未审批。

### 源批次与任务范围

581 张有框图中，原文件名 R1 前缀为 560 张（针孔 543 框、未分类 85 框），R7 为 21 张（针孔 21 框、未分类 0）。两类若按 R1/R7 隔离，验证集缺少未分类正样本，状态为 `blocked_missing_class`；未随机拆 R1 相邻帧／同帧 patch 来补验证。

实验收窄为 `pinhole`：R1 中 498 张有针孔框的图片训练，R7 的 21 张验证；62 张仅含其他类框的图被排除，不补空背景。另 1,646 张缺标签图片全部排除。标签注明“作者发布、未在 StepEye 人工复核”，未生成假的人工确认 manifest。R 前缀是保守源组隔离，不等于已核实物理生产卷独立；只有两组，不能组成独立 train/val/test 三批，R7 是开发验证集而非最终盲测集。

可复现转换（仓库根目录，输出目录必须不存在）：

```powershell
python sop-vision/tools/prepare_coating_dataset.py .v0/quality-models/CoatingVision-v1.zip .v0/train-run/coating-two-class --mode two-class
python sop-vision/tools/prepare_coating_dataset.py .v0/quality-models/CoatingVision-v1.zip .v0/train-run/coating-pinhole --mode pinhole
```

第一条正常返回退出码 2 并保存缺类报告；第二条生成实验数据。每图／转换标签的哈希、原文件名、源组与集合写入 `metadata.json`，`preparation.json` 记录排除数与限制；原 ZIP 不变。默认 500 图／类门槛仍未满足，实验准入不是生产准入。

### 环境

新建 `.v0/train-venv`，本地 NumPy 2.5.4 使用 OpenBLAS；显式通过 `.pth` **只读借用**已有 GPU 包，不修改主环境或 action-venv。这不是自包含发行环境；主依赖变化后必须重新检查。

```powershell
python sop-vision/tools/train_setup.py --venv .v0/train-venv --borrow-site C:/Users/rain/anaconda3/Lib/site-packages --report .v0/train-run/environment.json
```

新机器可不借包，先按[官方 PyTorch 入口](https://pytorch.org/get-started/locally/)在该虚拟环境安装匹配 GPU 的固定 torch/torchvision，再用 `--install` 安装 `requirements-training.txt`；不要在主环境安装。`--check-only` 只核验，报告含解释器、包版本／路径、METADATA/RECORD 指纹和实际 GPU。

本次实际：Python 3.13.9、torch 2.12.0+cu130、torchvision 0.27.0+cu130、Ultralytics 8.4.198、ONNX 1.23.2，RTX 4060 Laptop 8188MiB。旧 `helmet.pt` 只检查 SHA、ZIP CRC 与静态 pickle globals，没有作为训练基座加载。

### 运行结果

规格在 `.v0/train-run/pinhole-3epoch-spec.json`：3 epochs、batch 4、640、GPU 0、seed 42、workers 0、AMP 关闭。实际经现有 `visual.training_worker` 完成 train → val → export，退出码 0，总计 32.96 秒。

| 验证口径 | 结果 |
|---|---|
| R7，21图／21框；PyTorch验证 | Precision 0.51765、Recall 0.56247、mAP50 0.47407、mAP50–95 0.25225 |
| 导出 | float32 `[1,3,640,640]` → `[1,5,8400]`，opset 17，ONNX checker通过 |
| 同一真实R7图，PyTorch/ONNX输出差 | 最大绝对差 0.00048828125 |
| 平台CPU解码，conf 0.25／IoU 0.5 | TP 11、FP 2、FN 10；Precision 0.84615、Recall 0.52381 |
| CPU ONNX，21图、2线程 | P50 40.82ms、P95 46.98ms，非相机端到端延迟 |

PyTorch 验证与固定 conf 的 ONNX 工作点不同，不能互相替代。第一次 CPU 检查脚本因严格评测器拒绝 NumPy scalar 失败，显式转 Python float 后重跑通过；失败保留在 `onnx-check-first-attempt.json`，不是训练／导出失败。

数据指纹 `4408d2946c38979b2090fb5a269bce7c0279cb84c3ff65eff30cc2952bc3f5d5`；数据版本 `CV1-04dd29cac2124ce0-pinhole-ffdebf6bbaad`。

- ONNX：`.v0/train-run/pinhole-pilot-3epoch/train/weights/best.onnx`，SHA256 `c2a5e9af8e1311a7d3392b6503ffda3142c9eea0929316a53e7804d928c594d0`。
- 同目录 `best.pt`：SHA256 `240793e1744ffd9073f5f88a3f27629b0b6479ca349cb4518bb7b4310b12fa4a`。
- 原始证据：`pinhole-3epoch.log`、`train/results.csv`、`result.json`、`metrics.json`、`onnx-platform-check.json`。

未验证未分类、裂纹、分层，没有现场盲测／长期影子／生产合格结论。另查[官方 COCO8](https://docs.ultralytics.com/datasets/detect/coco8)小型 CC BY 数据：只适合链路冒烟，且来自 COCO、与通用预训练来源重叠，未用它冒充质量模型独立效果。完整两类实验仍需在独立源组补足未分类正样本，或选另一个类别／分组完整的质量数据集。
