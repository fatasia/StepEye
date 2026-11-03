# Q2 原生读码与实验 OCR

上传一张图片，在一次性 CPU 子进程读取条码或文字；不常驻模型，不使用 VLM。识别内容只作为文本返回，不执行其中 URL、命令或脚本。

## 接口与部署

- `GET /api/readers`：可选依赖/随包模型是否齐备，不等于精度已验收。
- `POST /api/readers/read`：multipart `file`，`kind=barcode|ocr`，可选 `roi=[x0,y0,x1,y1]`（0–1，按 EXIF 校正后的图片）。仅 JPEG/PNG/WebP/BMP 单帧图片。
- 响应 `items` 含 text、format、confidence、quad_norm；坐标映射回完整正向图片。条码不伪造置信分数，返回 null。空结果表示未读到，不作为产品合格结论。
- 10 MB、400 万像素、最大边 4096、最多 64 个结果；单任务 20 秒硬超时、1 并发（忙返回 429）。每次进程退出、临时图片删除。API 不接受本地路径/网络 URL。
- root 服务挂 `reader_routes.router()` 后使用原有 RBAC；写接口应限 operator/admin，MCP 默认不得访问。

本机可选运行环境已安装：

```powershell
$env:STEPEYE_READER_PYTHON="D:\Documents\ChatGPT\visoul\.v0\reader-venv\Scripts\python.exe"
```

环境变量和项目 `.env` 均未设置时使用服务当前 Python。本机主环境已有 RapidOCR，但没有 zxing-cpp，因此条码需要隔离环境；本机 `.env` 已选好，重新启动服务即可生效。本机已将该非敏感路径追加到项目 `.env`（若已有值则保留），其他配置保持不变。环境变量优先于 `.env`，随后才使用当前 Python。其他机器可建立专用虚拟环境，安装项目图像依赖及 `zxing-cpp==3.1.1`；OCR 的 `rapidocr-onnxruntime==1.2.3` 是本次复用版本，不是推荐精度基线。

## 实际验证（2026-11-03）

zxing-cpp 3.1.1 的 Windows wheel 已从 PyPI 实际下载并安装到 `.v0/reader-venv`，没有装入主环境。

| 样本 | 实际结果 | 一次性进程总耗时 |
|---|---|---|
| 自制 QR，STEPEYE-SN-20261103-001 | 完全匹配 | 245 ms |
| 自制 Code128，STEPEYE-12345 | 完全匹配 | 230 ms |
| 自制 DataMatrix，STEPEYE-12345 | 完全匹配 | 217 ms |
| ZXing 官方公开 QR 照片 | 与其 1.txt 完全匹配 | 245 ms |
| 自制清晰英文 SN A12345 | 分成 SN / NA12345，存在重叠和误字 | 2616 ms |

这些是单图软件接线证据，不是工业污损码/反光/曲面基准。OCR 有真实输出，但此版本未达到该清晰文字样本的完整正确识别，保持实验状态；本次不做反复分辨率调优。

原始结果及样本：`.v0/reading/selftest.json`、`verified-results.json`。公开样本来自 [ZXing qrcode-1/1.png](https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/qrcode-1/1.png)，图片 SHA256 `6908e133d3ce9bdbf3f72bd2c881ea993c6c5d388f8420ac3c87673e2b3f7bd4`，对照 [1.txt](https://raw.githubusercontent.com/zxing/zxing/master/core/src/test/resources/blackbox/qrcode-1/1.txt)。

专项测试覆盖真实 QR 子进程、ROI 回映射、文件清理、拒绝坏图/超限/动画、并发限流、超时杀进程、异常输出及 multipart 接口。主测试环境没有隔离读码环境时，真实 QR 项会明确跳过。

## 代码与权重来源

- [zxing-cpp 官方项目](https://github.com/zxing-cpp/zxing-cpp) / [LICENSE](https://github.com/zxing-cpp/zxing-cpp/blob/master/LICENSE)：Apache-2.0。版本 3.1.1；无模型权重，解码算法为原生库。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 代码及本机 1.2.3 包 metadata：Apache-2.0。
- 复用已安装包中的 PaddleOCR 转换 ONNX，没有重复下载/训练；[PaddleOCR 上游](https://github.com/PaddlePaddle/PaddleOCR) 为 Apache-2.0。随包文件无单独模型许可证声明，本记录不代替模型分发/训练数据授权核对。

| 本地随包权重 | 字节 | SHA256 |
|---|---:|---|
| ch_PP-OCRv3_det_infer.onnx | 2432880 | 3439588c030faea393a54515f51e983d8e155b19a2e8aba7891934c1cf0de526 |
| ch_PP-OCRv3_rec_infer.onnx | 10690752 | 897a3ededb38fee0dae2c1ccee38241f37df202c9509e3abca02e9217c5ee615 |
| ch_ppocr_mobile_v2.0_cls_infer.onnx | 585532 | e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c |

## 剩余

OCR 更新模型与独立文本真值评测、字符规则/料号期望值判定、流水线工位配置、Web 读码界面、跨平台原生轮子验证、现场污损/反光样本验收。当前接口不写标签真值、不自动判 PASS，也不触发生产动作。

Web 已在视觉应用的“评测与质量工具”中提供图片上传和识别结果导出；已通过真实QR图片页面交互，OCR仍明确标为实验能力。原“Web读码界面”待办已补齐。
