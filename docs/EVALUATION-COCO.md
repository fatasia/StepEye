# 固定评测与精度对比

在「视觉应用 → 质量工具 → 固定模型评测」选择已登记模型和冻结数据版本，完成一次评测后即可查看、导出和比较报告。

- 工作点指标：指定置信度、IoU下的逐类TP/FP/FN、Precision、Recall及逐图明细。
- AP：推理分数下限独立设为`min(0.001, 工作点置信度)`，不再先用0.25截掉低分框；每图最多100框。
- 官方COCO bbox：12项AP/AR，包含small/medium/large及maxDets=1/10/100；面积按原图像素计算。没有对应真值返回null，页面显示「—」。
- 精度对比：同模型的两份已复核报告比较逐类P/R；数据量不足单列，不自动回滚。见[精度对比](ACCURACY-COMPARISON.md)。

## 可选依赖

```powershell
cd sop-vision
python -m pip install -r requirements-evaluation.txt
```

COCO模块按需加载。未安装时报告`coco_bbox.status=unavailable`，保留现有工作点/AP功能；不向实时检测环境强加评测库。开发/CI依赖已包含该库。

实现复用[官方COCOeval](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py)，不另写一套完整COCO算法。输入是项目确认的矩形框，不含crowd/ignore标注；预测经过当前适配器的NMS、分数下限及100框上限，报告同时记录这一协议。既有报告保留原值，不追改历史AP。

## 本轮检查

真实微型ONNX与冻结图像经已有Web评测生成报告，COCO12项、历史报告读取、同模型基准选择、样本不足展示和JSON下载通过。单一1920×1080浏览器截图位于`.v0/output/playwright/accuracy-coco/report_1920.png`，页面错误0。该夹具验证软件行为，不用于宣称真实模型准确率。

修复了旧页面从`evaluation.average_precision`读取AP的问题；当前报告的真实字段位于根节点`average_precision`，两种历史格式均兼容。
