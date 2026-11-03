# B0 检测接口与 B1 轻量跟踪

面向现有 YOLO ONNX 实时管线：统一检测输出，并为每个相机实例提供短时目标 ID。不更换已有权重。

## 已接线

- `Detection(label, xyxy, confidence)`：原图像素坐标、有限数值、非退化框、0–1 分数。`YoloOnnxBackend.detect()` 同时服务实时检测与单图预标注；模型会话关闭时释放。
- 支持静态单图 float32 NCHW 方形输入、YOLO v5/v8 原始输出。不是任意 ONNX 接入层；YOLOX、DETR 多输入/专用解码仍需独立适配。MediaPipe 工厂路径已修正，未知扩展名拒绝。
- `OnnxObjectDetector.process()` 将当前帧 `ObjectTrack` 写入 `Context.object_tracks`，保留现有 `object_boxes` 和区域事件。结果数量遵守 `DetectorCfg.max_results`。
- 每相机独立 `ClassAwareIoUTracker`，同类 IoU 贪心一对一匹配、默认阈值 0.3、TTL 1 秒、容量受 max_results 限制。缺帧期间只保留关联候选，不输出预测框；同时间戳不增加观察次数，时间回退重置。
- `reset_observations()` 同时清空轨迹和区域去抖，已使用 runtime 现有断流/光照恢复 hook。重置不复用实例内 ID；Context 每帧清空 tracks。

## 证据

2026-11-03：`python -m pytest tests/test_tracking.py tests/test_onnx_detector.py -q`，20 项通过；连同标注任务、模型接口、光照运行时回归共 86 项通过。覆盖类别隔离、一对一、短暂缺帧、TTL、断流重置、时间回退/重复、容量、非法框、真实 ONNX 合成图接线、关闭与模型输入校验。

本机已有视频 `test-videos/FpTmB9IQdVo.mp4` 前 60 帧，30 FPS（2 秒内容），复用仓储模型进行真实 CPU 推理：89 个当前帧目标观测、2 个轨迹 ID、最长 60 次连续观测。原始逐帧框/耗时在 `.v0/public-models/tracking-replay.json`。

- 视频 SHA256：`a936a2b375a29fab239ce1de62a6fef76c87425250d5e2a8ba415e92ddcb4013`
- 模型 SHA256：`f695550212d2dd2059928f543f3550690527bc0ed7071dca040e5f12c849e432`

该片段没有人工轨迹真值，只证明管线可运行、ID 跨帧复用。未测 IDF1/HOTA，不代表交叉/遮挡场景稳定性。

## 仍需完成

1. 接业务级消费者（越线、停留、人与叉车距离关系）及轨迹可视化；当前 Context 接口可读，但尚无这些消费者。
2. 同类交叉/长遮挡/快速运动容易换 ID；需要运动预测、全局分配或真实 ByteTrack/ReID，以及公开标注视频评测。当前算法名称始终为 class-aware IoU，不称 ByteTrack。
3. MediaPipe 目前仍只输出区域计数，未接个体跟踪。进程重启后 ID 重新开始，跨相机/跨实例关联应附相机 ID 与会话 ID，不能仅用数字 track_id。
4. 现有权重许可状态不变，仓储权重仍缺明确许可证，仅评估使用。框架协议或 ONNX 格式不改变权重许可。

## 来源核查

[YOLOX 官方许可证](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE) 为 Apache-2.0；[ByteTrack 官方许可证](https://github.com/FoundationVision/ByteTrack/blob/main/LICENSE) 为 MIT。它们是后续候选，不是本次已集成模型或跟踪实现。未下载新权重或引入对应重依赖。
