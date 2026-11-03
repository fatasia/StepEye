# YOLO 训练执行器

训练执行器把已固定的数据版本和本机基座权重变成候选检测模型。它在单独 Python 进程中运行，不加载到实时采集服务，也不自动发布。

## 当前范围

- 本机 YOLO 检测 `.pt` 基座 → 训练 → 独立验证集评测 → 固定 640、batch=1、float32 原始输出 ONNX。
- 图像与标签、类别顺序、版本清单、基座都有 SHA256；训练前后重新核对，内容改变则不接纳结果。
- 每类必须在 train 和 val 中有正样本；相同图像哈希和清单中的同源组不能跨集合，合成样本不能进入验证集。
- 成功只得到 `candidate`，不写生产配置。单测使用假训练器验证流程，没有完成真实模型训练。

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
