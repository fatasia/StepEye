# 管道可见泄漏候选复核

2026-11-04。取得一个此前未登记的五类管道YOLO11n权重，完成CPU安全导出；两张作者公开预览图在固定阈值下均为0框，暂不加入模型中心或生产告警。

## 新资产

来源：[MSunusi/pipeline-leak-detection](https://github.com/MSunusi/pipeline-leak-detection/tree/3fe0a8ed679edc7b4c1cd25ef63a66e882fa72cc)，作者文件`pipeline leak detection.pt`。

| 资产 | 字节 | SHA256 |
|---|---:|---|
| 原PT | 5,468,691 | `8670e5cef00b77e891cc48ce60c16c46b1d59e220c8f49a68db7f399680838ba` |
| 导出ONNX | 10,568,380 | `273d6f86a0a04a0526defd6b9fc514fd04ebe36c6974e7f22813412d178b6b92` |

真实类别顺序是`crack / gas / leak / no leak / water`，与README的“两类Gas Leak、Water Leak”不一致。固定输入`[1,3,640,640]`、输出`[1,9,8400]`，可直接使用现有`yolo_raw`，没有新建检测后端。

工具`tools/export_pipeline_candidate.py`锁定原PT SHA和29个序列化全局名，只允许已安装的PyTorch/Ultralytics类，使用`weights_only=True`。不执行作者代码、Notebook或普通pickle加载；CPU两线程导出，不占GPU、不训练。原PT和ONNX位于`.v0/specialist-next-20261104/`。

```powershell
.v0/train-venv/Scripts/python.exe sop-vision/tools/export_pipeline_candidate.py --checkpoint .v0/specialist-next-20261104/pipeline-leak.pt --output .v0/specialist-next-20261104/new-pipeline-leak.onnx
```

## 实际试推

原数据[Roboflow v11](https://universe.roboflow.com/gas-leak/pipeline-leak-prediction/dataset/11)页面标注CC BY4.0、3586张、train3453/val49/test84；原图下载页面返回403，本轮未绕过。仓库没有LICENSE，不能把数据许可等同于权重授权。

改用另一[公开管道项目](https://github.com/Rizama-Victor/Development-of-an-Object-Detection-Algorithm-for-Crude-Oil-Pipeline-Leakage-Detection)的`Test_Image_1.png`和`Test_Image_5.jpg`，前者可见喷流，后者为管道连接件。两张图均自带作者检测框/文字，未视为独立干净评测集、没有计算准确率。

平台现有CPU后端，固定640、置信度0.25，只试推一次：两张图均0框，分别46.79ms和45.33ms。没有通过降低阈值或换分辨率追结果。完整预测及图像SHA记录于`.v0/specialist-next-20261104/probe.json`。

这不证明权重在其原训练域完全无效，但不足以作为工业可见滴漏交付。`gas`只是模型标签，不能据此判断不可见气体；静态框检测也不等于S12滴落时序识别。下一步必须取得原域未覆盖文字的标注验证图和权重许可，当前保持离线候选。

## 新电池入口核查

[SD-YOLO作者库](https://github.com/luckycloud12138/Lithium-battery-project)本次公开树只有10张`train_*.jpg`样例、模块源码及训练入口，没有标签或训练权重，也没有LICENSE。未下载未标注图片启动训练，不将其算作六面缺陷覆盖。API树/README保存于同一证据目录；既有LGASS、DAEF、PAINE和腕带/PCB资产未重复下载或训练。

专项测试3项通过：固定源指纹、拒绝覆盖、五类顺序。未改共享模型登记或实时配置。
