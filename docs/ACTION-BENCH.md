# 动作旁路性能检查

`tools/bench_action.py` 测量本机动作 ONNX 子进程的单路延迟，固定 16 帧、224×224、batch=1，不扫描分辨率。

## 当前结果（2026-11-03）

- 已通过适配器的预处理、模型指纹、类别维度、真实 ONNX 子进程往返、退出与异常释放测试。
- 测试模型只有 RGB 均值运算，用于检查协议；不是 VideoMAE，不报告它的速度作为模型性能。
- 本机只有 VideoMAEv2 源码审查副本和候选权重来源记录，没有合格动作分类 ONNX。VideoMAE 的 GPU 延迟、显存与动作准确率尚未测得。
- 候选 `OpenGVLab/VideoMAE2` 仓库模型卡标注 `feature-extraction`。特征向量不能直接当工步分类 logits；须确认分类头、输出类别及顺序、预处理、权重来源后导出。当前适配器不加载 PTH/pickle、不下载权重，也不自动给特征维度编造类别。

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
