# 整图分类模型

视觉应用现在区分目标检测和整图分类。分类试跑输出类别概率，不画框，不创建YOLO框标注项目，不改变六面检的设备原判。

- 湿地dry/wet：ConvNeXt q8、RGB短边256后中心224、ImageNet归一化。原湿滑照片误判dry，不用于放行。
- EV焊缝OK/KO：本项目随机初始化CNN、灰度224/255。首批6000训练图候选测试误放9.42%，仅供评估；完整43169图扩量版本已另存v2并列登记，不覆盖v1；同一新5000测试集误杀减少、漏放略增，均未批准自动放行。

两类预处理必须显式登记，不猜测通道/尺寸/输出。分类登记与框标注引擎隔离；本机清单固定文件SHA。启动只加载元数据，试跑才创建CPU双线程进程，结束释放；与检测试跑共用单槽，超时终止。

清单：`sop-vision/models/public/classification-models.json`；来源和真实指标分别见PUBLIC-SPECIALIZED-MODELS.md、BATTERY-INSPECTION.md。API复用GET `/api/models` 与POST `/api/models/{id}/trial`，分类返回task=classify、probabilities、predicted_label、confidence；没有targets或质量verdict。分类的/project请求返回409。

已验证真实模型子进程推理、统一页面概率显示、检测项目入口禁用和六面原判不被覆盖；软件契约检查不作为分类准确率。
