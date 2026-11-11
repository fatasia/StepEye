# DINOv2 良品外观检测

视觉应用 → 良品外观检测：为新场景选择网格基线或 DINOv2，登记 1–8 张良品，再上传图片试测。同一场景固定特征方式；切换方式使用新场景并重新建库。已有场景仍使用网格基线。

## 准备模型

使用 Meta 官方 DINOv2 源码及 `dinov2_vits14_pretrain.pth` 权重，在独立 Python 环境安装其依赖、PyTorch 和 ONNX，然后运行：

```powershell
python tools/export_dinov2.py --repo <官方源码目录> --weights <权重文件> --out models/dinov2-vits14-patches.onnx
$env:STEPEYE_DINOV2_ONNX = (Resolve-Path models/dinov2-vits14-patches.onnx).Path
```

启动 StepEye 的环境还需要安装 `onnxruntime`。默认读取 `models/dinov2-vits14-patches.onnx`，也可用环境变量指定其他位置。模型不会自动下载，导出工具不载入任意对象；模型权重不随仓库分发。源码和权重入口：[Meta DINOv2](https://github.com/facebookresearch/dinov2)。

运行契约固定为 RGB、224×224、ImageNet 均值方差、ViT-S/14 的 `[1,256,384]` 归一化 patch tokens。模型 SHA256 随场景保存，文件发生变化后拒绝继续使用该场景，避免混用特征库。旧库没有后端字段时按网格基线读取。

阈值针对场景和特征方式单独评测；不要复用网格阈值作为 DINOv2 验收阈值。当前图片缩放到 224×224，不提供高分辨率切块。

## 本机验证

使用官方源码提交 `7764ea0` 与 ViT-S/14 官方权重导出本机模型；ONNX SHA256 为 `c94f25a67ee27eaa45c96d57897b811914c020cf6daa8b06626dcf6a386b6450`。一组固定输入的 PyTorch 与 ONNX Runtime patch tokens 最大绝对差为 `6.97e-5`。真实浏览器已完成选择 DINOv2、登记图片、试测的流程；尚未进行工业缺陷独立评测。

场景可选择自动曝光校正：以亮度中位数估计增益，并限制在 0.5–2 倍；登记和检测应用相同处理，原图不变。更换模式时参考库按原始图片重建并保留旧版本，阈值需重新核对。
