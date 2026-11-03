# PPE专用模型候选

2026-11-03。新增一个实际取得的14类PPE ONNX，补充现有仓储20类模型没有的护目镜、安全鞋与部分明确负类。**尚未接入生产，也没有ESD材料／电阻识别模型**。

| 资产 | 实际状态 |
|---|---|
| 已有 warehouse20／helmet | 保持原样；warehouse含gloves、helmet、safety vest，不含goggles／shoes |
| Construction Site Safety Hazards v0.3 | 44.8MB原生ONNX；静态检查及现有ModelTrials真实CPU子进程通过 |
| PPE合规与ESD | 不由“下载成功”推导完成；逐人关联、负类和现场真值仍须接入／验证 |

机器清单：[`ppe-candidates.json`](../sop-vision/models/public/ppe-candidates.json)。下载与报告保留于 `.v0/ppe-models/`，该清单不在自动注册入口中。

## 固定来源与类别

[发布者 constructelligence/construction-site-safety-hazards](https://huggingface.co/constructelligence/construction-site-safety-hazards/tree/79673cff5a8bb249fc8ec9cca3daa19e2ef95191)，固定revision `79673cff5a8bb249fc8ec9cca3daa19e2ef95191`。文件 `buildvision-hazards-v0.3.onnx`，44,766,819字节，SHA256与HF LFS一致：

```text
55f4dd2f53158eac4ff093d938b9fbda80c99454a03672145bbc36f539988249
```

模型元数据、配置与模型卡类别顺序一致：

```text
0 person          1 hardhat         2 no-hardhat
3 safety vest     4 no-safety vest  5 no-mask
6 gloves          7 safety shoes    8 excavator
9 wheel loader   10 dump truck     11 goggles
12 no-goggles    13 no-gloves
```

float32 `[1,3,640,640]` → `[1,18,8400]`，opset12，无外部张量／自定义算子，`onnx.checker`通过。复用现有`yolo_raw`：letterbox640/pad114、RGB/255、按类NMS，不新增后端。没有下载PT、执行作者Python或导入torch。

权重声明 **AGPL-3.0**，不是“无商业条件”；发布者列训练数据为CC BY 4.0及5个来源，完整逐项许可／归属链尚未独立审核。候选保留 `evaluation_only`，不写“商用已审核”。

## 实际推理结果

作者 `example.jpg` 是**已经画框、带文字的四宫格**，不是独立原始验证图；已目视检查。SHA256 `0d80be78cb9c050ad88140786173305db88e5c2d1b17bcf3024e445de9f98b96`。

固定conf0.25，真实`ModelTrials → visual.label_predict → detector_registry`按需CPU子进程返回16框：person4、hardhat4、safety vest3、no-safety vest1、safety shoes2、excavator1、dump truck1。启动、解码和推理往返约1096ms，不是实时帧吞吐。**本次整张图没有返回gloves／goggles**，虽然作者图上有相关标注，不能把这些原有框当成本机检测结果。记录：`platform-trial.json`。

另用既有 `sop-vision/.v0/test-helmet.png`，固定SHA `1dce75c0424407f215fedb6d546a70c12b93b259d98ef83c5e91980155b408ad`，返回hardhat6/person3/vest2，约819ms冷进程往返；仍是功能冒烟，没有新增其来源／独立性证明。记录：`existing-fixture-probe.json`。

独立Wikimedia照片 [Safety and work](https://commons.wikimedia.org/wiki/File:Safety_and_work.jpg) 下载遇TLS失败，后续请求403，未取得图片，未计入试测。没有据上述样图计算Precision、Recall或准确率。

## 其他候选与接线缺口

- [6类nano候选](https://huggingface.co/itsadityabaniya/yolov8n-ppe-detection-6classes/tree/02bdec1b9be00d55ec1e804d53e7b54b6cb42eb6)模型卡写ONNX，但固定树只有5.6MB `best.pt`和667MB数据ZIP，没有ONNX；未加载PT或为它下载大数据包。模型卡MIT也不能自动覆盖Ultralytics基座和未知数据许可。
- [Qualcomm PPE-Detection](https://huggingface.co/qualcomm/PPE-Detection)是另一个两输出类别模型，本轮未把它当护目镜／鞋／手套多类模型下载安装。
- 当前`PPEMonitor`仍仅处理已有helmet/vest/gloves别名；新模型的hardhat/no-hardhat、眼镜／鞋、负手套和逐人关联未自动接上。要使用候选，先明确别名与证据语义，再建独立人工框真值与回归，不直接改生产默认检测器。
- 普通`gloves`／`safety shoes`外观标签不能证明ESD电阻、材料认证或防护等级。搜索中的“ESD-YOLO”主要是同缩写的驾驶／跌倒／白蚁算法，不是防静电PPE权重。电阻需设备读数与工件／人员关联，不能从鞋子看起来像工作鞋推断。

复现单图：

```powershell
python sop-vision/tools/public_model_probe.py --model .v0/ppe-models/buildvision-hazards-v0.3.onnx --image .v0/ppe-models/example.jpg --out .v0/ppe-models/new-probe.json
```

`--out`须是新文件，保留既有结果。本轮不改registry/server/runtime，没有替换既有安全帽或仓储模型，没有启用生产工位。
