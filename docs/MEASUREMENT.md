# Q6 双 ROI 平面边缘间距

自动提取两个明确 ROI 内的单条强直边，计算标定平面上的垂直间距。适用近水平/竖直、近似平行的清晰边缘；不是任意物体尺寸识别。

## API

`POST /api/calibrations/{id}/measure-edges`，multipart `file` + `spec` JSON：

```json
{"scope_id":"camera-a","reference":"fixed","rois":[[45,15,75,145],[145,15,175,145]],"edge_direction":"vertical"}
```

ROI 使用 EXIF 正向后的图片像素坐标。`edge_direction` 为 vertical 或 horizontal，指待拟合边缘方向。图片限制单帧 PNG/JPEG/WebP/BMP、10MB、400万像素、最大边4096；仅一个并发测量。

成功返回 `status=measured`、distance、unit、calibration_id/version、measurement_line（原图像素）、edges（线端点/抽样点/残差/覆盖率）。保留 `review_required=true`，供人工核对边缘选择与测量平面。

弱边缘、多重边缘、直线残差大、物理平面不平行或无共同支持范围，返回 `status=review`、distance=null、简短 reason。来源/参考/尺寸不符、标定未验证或超覆盖范围直接拒绝，不外推。

## 方法与证据

Sobel梯度逐扫描行提取峰值，排除多峰；Huber稳健直线拟合。将两段线通过既有 CalibrationStore.measure 变换到物理平面，计算垂足，再用同一 measure 接口校验并返回最终距离。

已知几何图：100像素间隙、0.5mm/像素，自动结果50mm；水平80像素得到40mm。测试包含实际 multipart 图片调用、无边缘/歧义/非平行待复核、错误参考与越界拒绝。仅单一测试图尺寸，无多分辨率矩阵。

不包含镜头畸变校正、三维高度补偿或工业计量溯源。实际公差判定仍需测量系统分析与实物标准件验证；不从拟合残差推算计量精度。
