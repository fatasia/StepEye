# 胶路断胶与过量胶模型

已有作者 Darknet 权重已转为 ONNX，可用 CPU 检测两类胶路缺陷；不覆盖胶宽、体积或三防漆覆盖率。

- 固定416输入，直接缩放；复用既有按类NMS，运行时不需要Torch或OpenCV4。
- ONNX为23,551,625字节，SHA256 `4a398f4e1a0ebfe69877f593f43bac3d220f1c5b785d5be3880e24462e1ea32a`。
- 作者19张holdout、57个框，固定置信度0.25、NMS IoU0.45、匹配IoU0.5：TP33 / FP15 / FN24，P0.6875 / R0.5789 / F1 0.6286。
- 权重与图片未单列许可；保持 `evaluation_only`，不作为已获商用授权或产品自动放行模型。

## 来源和转换

[作者固定版本](https://github.com/RicardoSPeres/GAN_Synth_Adhesive/tree/b309653ec5ef27013f5dc0d6ce1fc7bf4763c148)及此前下载证据见[公开候选记录](PUBLIC-SPECIALIZED-MODELS.md)。源权重SHA为 `c51a715e886f585a1046a6b09f62270d212a8715a0e5428490c7897a88ced3a3`；两类派生cfg为 `44dbebf92ad2dba2ebcc3da31d2058cd5a94b37ad9901c3e00973b1866cca3bb`。原80类cfg保留，未覆盖。

转换工具只解析固定SHA的cfg和浮点权重，以标准ONNX节点重建网络；无需作者Python、pickle、Torch或再次下载骨干。输入 `images[1,3,416,416]`，输出 `predictions[1,2535,7]`。最后两列已包含objectness，不能再相乘。

预处理为RGB/255直接缩放，不是letterbox。独立 `glue_yolov4` 适配器先还原原图坐标，再复用平台NMS与裁剪；不能用现有 `yolo_raw` 猜测布局。

OpenCV4参考使用其[Region层](https://github.com/opencv/opencv/blob/4.11.0/modules/dnn/src/layers/region_layer.cpp)逻辑。该实现将小于0.2的类别得分置零，ONNX保留完整得分；双方仅在固定0.25工作点比较。19图检出数和类别全部一致，最大坐标误差0.02182像素、最大置信度差0.000369。

## 实际评测

| 类别 | TP | FP | FN | 精确率 | 召回率 |
|---|---:|---:|---:|---:|---:|
| 断胶 discontinuity | 22 | 9 | 18 | 0.710 | 0.550 |
| 过量胶 excess | 11 | 6 | 6 | 0.647 | 0.647 |

19图仅运行一个固定分辨率和阈值，没有调参追结果。CPU两线程纯推理中位数28.01ms，不包含进程/加载/上传，也不是平台端到端FPS。作者holdout不等于独立厂区数据，本轮没有训练。

## 复现

导出工具：`sop-vision/tools/export_glue_onnx.py`。评测工具：`sop-vision/tools/probe_glue_onnx.py`，将参考OpenCV4放在独立环境，通过 `--reference-python` 指定。主环境只使用ONNX Runtime；不降级cv2。

本机证据位于 `.v0/specialized-models/glue-onnx-eval-v1/`：逐图参考输出、来源SHA、预测、匹配明细和耗时。模型复制至 `sop-vision/models/public/glue-yolov4-tiny.onnx`，登记为 `glue-models.json`，二进制不入Git。

新增5项适配/导出检查及既有ONNX检测检查共16项通过，覆盖直接缩放、RGB顺序、objectness不重复计算、按类NMS、裁剪、模型契约和关闭行为。

主服务已接glue_yolov4检测适配、原模型试跑/预标注/运行配置/冻结评测/发布参数。真实Web固定1.png试跑3框（页面默认0.4阈值），评测抽屉自动选中glue_yolov4；未修改任何生产工位或自动发布。
