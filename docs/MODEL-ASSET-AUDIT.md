# 既有模型资产盘点

2026-11-03。先复用既有资产，不把场景模板、模型下载或导出当成已训练成果。本次仅只读盘点，未加载 PTH/PT、未下载、未训练。

## 范围与定位

- 列出了 `D:/Documents/ChatGPT` 一级项目目录，限定搜索当前 `visoul`、相邻 `IoT`（含 trainer）及 `C:/Users/rain/.zcode/workspace`。
- 排除 node_modules、site-packages、虚拟环境、.git、build、dist、release、target、cache、commit-check 副本。未扫描整个 C 盘，也未读取认证配置或密钥。
- 只读查询 `C:/Users/rain/.zcode/v2/tasks-index.sqlite` 的项目根路径和相关任务定位：zcode 的 visoul 项目及相关任务均指向 `D:/Documents/ChatGPT/visoul`，没有发现另一个视觉项目根路径。`C:/Users/rain/ZCodeProject` 当前未见文件。
- 其余兄弟项目仅列目录，未递归搜索；压缩备份、远程主机、历史会话附件及其他盘符未检查。因此结论仅限本次范围，不等于用户从未训练过模型。

## 找到的权重文件

下表路径均相对于 `D:/Documents/ChatGPT/visoul`；哈希为本次实际文件 SHA256。

| 路径 | 字节数 | SHA256 |
|---|---:|---|
| `.v0/videomaev2-small.pth` | 44,303,649 | `525f7dec130381f40cfa218bd86fa06fd5078f542c7f3488c9f16ddcc1358461` |
| `sop-vision/models/efficientdet_lite0.tflite` | 7,254,339 | `4b59100025bea1235a84c1038879a6cccc9f6c49f5e41144e91e74d99e780993` |
| `sop-vision/models/hand_landmarker.task` | 7,819,105 | `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1` |
| `sop-vision/models/pose_landmarker_lite.task` | 5,777,746 | `59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a` |
| `sop-vision/models/community/fire-smoke.onnx` | 13,288,081 | `17270b12b08ed7b3ae7358ed4d37dc9e4f41768f21183adb46f56ebac7661b73` |
| `sop-vision/models/community/fire-smoke.pt` | 6,775,651 | `62f990f60e2383576b6a201da0f62464b2943ed07a6fad2817fb97e1dfca7cc8` |
| `sop-vision/models/community/helmet.onnx` | 12,266,521 | `fa30e26e86c6cc1a1c78678aca272b4ce06bba85b38e3d08cceb1e590c0cc5ab` |
| `sop-vision/models/community/helmet.pt` | 6,199,658 | `c8eb324e365cf4faeab491d9cc301535ec745171b55e8b1acadea62be5101a9d` |

## 来源与可复用判断

- `hand_landmarker.task`、`pose_landmarker_lite.task`、`efficientdet_lite0.tflite`：现有运行时基础模型。HANDOFF 记录为已下载本机资产；没有发现这些文件对应的本项目训练记录。
- `helmet.pt/.onnx`：社区来源 `iam-tsr/yolov8n-helmet-detection`，项目说明记录了 ONNX 导出和实际检出。应复用现有权重，不重新训练或另下载同款；它不是已证实的本项目自训成果。
- `fire-smoke.pt/.onnx`：社区来源 `raidavid/yolov8_fire_and_smoke`。文件存在，但既有四张火图测试全部漏检，不能因有权重就标为合格烟火能力。
- `.v0/videomaev2-small.pth`：新增发现 44,303,649 字节文件。文件头是 ZIP 的 `PK`，但 Python `zipfile.is_zipfile` 返回 False，当前不是可完整识别的 ZIP 权重容器，需核对完整性。旁边 `action-source.json` 记录候选来源 OpenGVLab/VideoMAE2 的固定提交及 feature-extraction 模型卡，不能由文件名推断已经训练、已完整下载或可分类工厂动作。未尝试 pickle/torch 加载。

## 训练证据

限定搜索范围内未发现 `results.csv`、`args.yaml`、`metrics.json`、`environment.json` 训练产物，也未发现额外 PT/ONNX/PTH/Safetensors 权重。`IoT/trainer` 有训练代码，不代表存在视觉训练成果。

历史 HANDOFF、TRAINING-READINESS 与 community/README 一致记录了基础模型、社区安全帽和不合格烟火权重。`.v0/maskflow-review` 中“训练完成”的文字属于被研究的外部项目文档，且部分注明替身训练，不能算 StepEye 的既有训练证据。

**当前结论：保留7个已知模型文件，复用其中合格资产，不启用不合格烟火权重；VideoMAE候选文件待完整性确认。本次没有找到可证明“很多专用视觉模型已在本项目完成训练”的对应产物，但尚不能排除未检查位置的历史资产。**

补查同一范围原生格式 `.engine/.xml/.bin/.ckpt`：共 2 个 XML 文件，内容为 Android adaptive-icon 与背景颜色资源，不是 OpenVINO 模型；.engine/.bin/.ckpt 均为0。
- `D:\Documents\ChatGPT\IoT\src-tauri\icons\android\mipmap-anydpi-v26\ic_launcher.xml`（261字节）。
- `D:\Documents\ChatGPT\IoT\src-tauri\icons\android\values\ic_launcher_background.xml`（115字节）。
VideoMAE 候选文件原样保留，未删除或修改。
