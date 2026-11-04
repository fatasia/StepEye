# 运行包增量精简

2026-11-04。本轮只修改既有构建清单和依赖收集，不重打完整包，不改品牌打包bat。

## 已落实

- 排除未使用的麦克风依赖 `sounddevice` / `_sounddevice_data`，过滤库内test/benchmark隐藏导入。手、姿态、目标检测保留。
- 胶路/针脚权重作为 `--include-quality-extensions` 可选扩展；基础包保留目录元数据，不放入这188.44MB权重。未安装模型在现有模型目录显示不可用，禁止试跑，不伪装已安装。
- 保留weihu文档模块的MIT归属文件 `visual/documents_LICENSE.txt`；它之前被“仅收集.py”过滤掉。

使用现有构建入口：

```powershell
# 基础运行包（仍含已有PPE/仓储/PCB等运行资产）
python sop-vision/deploy/build_runtime.py --output .v0/runtime-next --allow-evaluation-assets
# 同时包含胶路与针脚分割权重
python sop-vision/deploy/build_runtime.py --output .v0/runtime-quality --allow-evaluation-assets --include-quality-extensions
```

不创建core/full等另一套打包系统；本地Qwen继续沿用已有 `--include-local-vlm`。以上命令未在本轮执行完整构建。

## 体积依据

现有v2 ZIP仍为 **567,844,000字节**、解压 **998,660,260字节**。本轮没有新ZIP，不能报告新的安装体积。

| 项目 | 实测字节 |
|---|---:|
| v2未使用音频依赖，解压 | 2,039,278 |
| 同一ZIP内上述音频依赖，压缩 | 971,763 |
| 新增胶路+针脚扩展权重，未压缩 | 188,444,866 |
| 本轮基础资源白名单（不含PyInstaller运行库），286项 | 537,639,789 |

检查v2所有大于1KB文件的同尺寸SHA，最大相同内容仅2,529字节（图标）；没有可通过去重大幅缩小的大DLL或重复权重。时区别名、许可文件和说明副本保留，不为几KB破坏运行约定。

不删除Matplotlib：MediaPipe 1.2.0的vision入口会直接导入drawing_utils，再导入pyplot；贸然排除会让手/姿态入口失败。OpenCV、ORT、SciPy/BLAS和MediaPipe原生库均有现有功能依赖。基础资源本身已超过100MB，本轮不承诺完整离线包小于100MB。

## 检查

37项打包/入口/公开模型检查通过。另在现有干净运行时venv拦截音频包导入，实际创建并关闭hand_landmarker、pose_landmarker和efficientdet三个模型，全部成功；没有重跑多帧精度评测。

证据目录：`.v0/runtime-build-audit-20261104/`，含白名单、体积审计及依赖探针结果。最终冻结EXE与ZIP仍需下一次正式发布构建时验证；Windows旧ZIP不会自动包含本轮新代码。Linux原生包仍未构建。

## 电机模型补充

已有mechanism_bundles清单新增motor-rf目录：只收ONNX、manifest/source、THIRD_PARTY_NOTICES与LICENSE-weihu，共20,460,434字节；离线特征转换辅助脚本不入包。文件缺失时保持既有未安装状态，不在构建时下载。最新基础资源白名单286项、537,639,789字节，包含本轮同期应用源码更新；这不是新ZIP体积。证据 `.v0/runtime-build-audit-motor-20261104/assets-before.json`。
