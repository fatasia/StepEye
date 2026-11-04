# 佩戴戒指权重补缺

2026-11-04。核查三个新入口，没有同时满足“佩戴场景、公开可下载权重、明确许可”的候选。本轮没有下载或训练新戒指模型，腕带 v2 的戒指漏检仍待解决。

| 来源 | 核查结果 | 处理 |
|---|---|---|
| [Jewellery_CV_project](https://github.com/rohithebbar-ai/Jewellery_CV_project/tree/e51cdc77c7428a9967c2e0d5b5e4b612b714501c) | 真实手部佩戴场景；公开 `YoloV8_results/ring_detector/weights/best.pt`，6,235,498 字节。仓库无 LICENSE，API 的 license 为 null，README 也未给权重许可 | 未下载，不登记 |
| [humaisj/ring-detector-yolov8](https://huggingface.co/humaisj/ring-detector-yolov8) | 模型卡标 AGPL-3.0；关联的 [visual_ring_search](https://github.com/HumaisJ/visual_ring_search) 面向商品首饰目录检索，不能证明佩戴场景能力 | 不拿孤立首饰检测替代佩戴检测 |
| [Jewelry Detection (Esp. Ring)](https://universe.roboflow.com/thanveer/jewelry-detection-esp.-ring) | 数据页面标 CC BY 4.0、单类 Ring；显示托管推理接口，未见公开下载权重 | 数据许可不等于模型许可，不登记 |

首个来源的作者 README 另报视频 precision 0.25、recall 0.263，训练集指标不能代表该视频效果；这些是作者结果，本轮未复测。数据描述仅称取自公开图片，未提供逐源授权清单。

GitHub 仓库元数据与完整树已保存：`.v0/specialist-next-20261104/ring-repo.json`、`ring-tree.json`。停止扩搜；后续只有取得明确权重许可及适用样本，才进入 CPU 固定尺寸试推，不占用当前动作模型 GPU。
