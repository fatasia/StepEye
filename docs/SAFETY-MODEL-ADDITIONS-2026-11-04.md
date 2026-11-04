# 安全模型增补：发网候选与腕带数据

2026-11-04。新增一个可运行的发网检测候选，腕带数据增加36张；未改现有生产模型，未启动第二个GPU训练。

## 发网单类：权重已取得，许可与正样本评测待补

来源：[penyepesto/PPE-detection](https://github.com/penyepesto/PPE-detection/tree/e1e39f794008c107f93fb99720f68be90172cab3)，固定版本 `e1e39f794008c107f93fb99720f68be90172cab3`，作者文件 `data/weights/best-hairnet (1).pt`。

| 资产 | 字节 | SHA256 |
|---|---:|---|
| 作者PT | 6,244,643 | `bd4b28125aeb0208fed815df1e50c5f44f2f545dc4188b8625e9b398d9b6c8dc` |
| 本地ONNX | 12,238,781 | `e24e9fce107c2ee3e0c29c519dc1f11ab97797be714abf99ae579c44b80615af` |

原生类别确认为 `hair_net`，不是发型分割。YOLOv8n固定640输入 `[1,3,640,640]`、输出 `[1,5,8400]`，兼容现有 `yolo_raw`，不需新建实时检测器。

工具 `sop-vision/tools/export_hairnet_candidate.py` 固定作者SHA与23个已核查的序列化全局名，仅允许本机已安装的PyTorch/Ultralytics类；`torch.load(weights_only=True)`、CPU导出，不执行仓库Notebook，不加载远端自定义代码。重现：

```powershell
.v0/train-venv/Scripts/python.exe sop-vision/tools/export_hairnet_candidate.py --checkpoint .v0/safety-model-audit/remaining-20261104/hairnet.pt --output .v0/safety-model-audit/remaining-20261104/new-hairnet.onnx
```

实际产物位于 `.v0/safety-model-audit/remaining-20261104/hairnet-reviewed.onnx`；以已有PPE发布者拼图经平台后端试推，0框、CPU首次78ms。该图没有独立发网正类真值，因此这只确认推理链可运行，不是发网检出率或实时FPS。新增导出测试2项、已有腕带转换测试3项通过。

仓库完整文件树没有LICENSE，作者只说明使用合并Roboflow数据，没有对应权重授权与训练集许可链。本候选不登记为可商用、不开自动告警。洁净服、鞋套、更衣行为仍未覆盖。寻找真实正样本时，Pexels和Wikimedia图片请求返回403，未绕过限制或使用生成图替代实测。

## 腕带：141 → 177 张

复用[公开项目129799](https://studio.edgeimpulse.com/public/129799/latest)及已有下载缓存，只补原失败分页和图片，没有重下141张。分页现已取得全部196条记录；新增36张，实际成功177张、347框，剩19张图片请求TLS EOF。本次没有继续循环重试。

冻结新版本 `.v0/safety-model-audit/edge-wrist/dataset-v2`，复用 `prepare_wrist_dataset.py`：

| 集合 | 图片 | ring框 | watch框 | wrist-strap框 |
|---|---:|---:|---:|---:|
| train | 118 | 83 | 61 | 82 |
| val | 24 | 14 | 24 | 16 |
| test | 35 | 23 | 20 | 24 |

原 `dataset-v1`、旧141张审计、原31张test报告均保留不变；新35张test为作者测试集合扩充，不与旧31张的数字直接比较。val仍按项目created分钟分组，不声称跨操作者/机位独立。跨原始集合精确像素重复0。新增文件 `audit-complete-v2.json` 和 `rows-complete-v2.json`；其中 `complete:false`，文件名不表示196张已经全部下载。

未重训：新增36张不会自动改变旧模型R0.207的结论。ESD设备接地判断、洁净全套用品和工业滴漏专用权重仍未补齐。发网候选不替代这些缺项。

## 腕带 v2 下一次训练规格（仅准备，尚未启动）

复核v1全部141张在v2中的身份与SHA：split变化0、图像SHA变化0，原31张test全部保留，原train/val没有进入test。新增test仅4张：137377951、137377938、137377937、137377953。证据 `edge-wrist/v2-split-stability.json`。

v2数据版本 `EDGE129799-V2-PARTIAL177-GROUPED`，平台训练指纹 `25c95c4509dcbd3bf0b2a96680785c9d988d712b4b49780880e34abda8f8a4ce`。规格 `edge-wrist/train-spec-v2.json` 固定40轮、batch8、GPU0、640，118训练/24验证，初始化原批准YOLOv8n基座（SHA `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`），不用旧候选或test数据选参数。未修改v1数据，v2新增metadata后已重新固定训练指纹。

一次性脚本 `.v0/safety-model-audit/edge-wrist/train-v2-after-pcb.ps1` 已完成PowerShell语法检查，**尚未执行**。启动后先等待PCB进程64844退出且产生成功result，再调用原training_worker；随后复用原评测工具分别产生 `test-evaluation-v2-original31.json`（可比31张）和 `test-evaluation-v2-added4.json`（新增4张）。不重复评测整35张、不自动发布、结束即退出。新增4张测试视图位于 `test-v2-added4`，不进入训练YAML。
