# 安全与 SOP 公开模型缺口

2026-11-04。用于决定下一批模型接入与采样工作；资产存在、接口跑通和现场识别有效分开记录。本轮只核查资料与已有试推记录，未新增训练、下载权重或生产发布。

## 先做什么

1. **PPE：继续用已有 14 类权重做逐人评估**，优先补手套、护目镜、安全鞋独立测试集，不重复下载。
2. **积水：复用已下载 Kairos INT8**，增加独立 RT-DETR 输出适配后试用；它不是管线滴漏模型。
3. **ESD、洁净穿戴：有公开标注数据入口**，先核导出包与类别，再训练；尚未取得可直接本地部署的同域权重。
4. **睡岗与工业动作：现成通用动作基座可用，专用动作头仍缺**；近景驾驶困倦模型不能直接替代厂区远景睡岗。

## 当前资产与缺口

| 任务 | 已有或可取得的资源 | 当前能完成 | 仍缺什么 |
|---|---|---|---|
| 安全帽、反光衣 | warehouse20、helmet、PPE14；已有逐人状态与异步增强链路 | 检测、关联、持续确认、未知态显示 | 工位尺度、遮挡、逆光下独立评测 |
| 手套、护目镜、安全鞋 | PPE14 含正类，手套/护目镜有明确负类 | 现有接口试推、逐人状态 | 小目标真值、误配率；没检出不能直接判未穿戴 |
| 全用品 | PPE14 不是全用品；只有 no-mask，没有正向 mask，未覆盖耳塞、安全带、防护服所有细类 | 明确已支持类别 | 其余用品数据与专用权重 |
| ESD | 公开腕带穿戴数据 2,314 图/3 类 | 取得导出后可走现有 YOLO 标注训练流程 | 本地权重、独立测试；接地/电阻仍依赖设备信号 |
| 洁净穿戴 | cleanroom 1,000 图/4 类 | 补口罩、围裙、手套、护目镜穿戴训练候选 | 数据导出审核、洁净服/帽/鞋套等覆盖 |
| 洁净行为 | 上述是静态穿戴，不是行为序列 | 区域/顺序规则已有平台基础 | 更衣顺序、跨区、触碰等时间段标注与专用动作头 |
| 地面积水 | wetfloor 分类已登记；Kairos RT-DETR 已下载并试推 | 干湿分类；新增适配后可显示积水框 | 室内反光/阴影/油污难例与现场评测 |
| 滴漏/管线泄漏 | 发现油气 YOLO 权重候选，未完成本地核验 | 候选调查 | 同设备滴落时序、介质/背景差异、可复现权重输出 |
| 睡岗 | 驾驶困倦 ONNX 候选；现有 K710 动作基座 | 人脸候选离线验证、动作微调准备 | 远景/遮挡睡岗样本、持续时间真值、专用动作头 |
| 工业 SOP 动作 | 已有 VideoMAEv2 Small K710 ONNX；公开装配数据 | 固定片段分类与已有采样/动作参考流程 | 工位步骤对应的类别头、动作边界及跨人验证 |

## 1. PPE：修正旧文档状态

当前 `sop-vision/models/public/specialized-detection-models.json` 已登记 PPE14；`visual/ppe.py` 已包含 helmet/vest/gloves/goggles/shoes 及负类别名。旧 `PUBLIC-PPE-MODELS.md` 中“尚未登记/逐人关联仍须接入”的描述不能继续当成当前代码状态。**登记与状态链路完成，不等于全用品现场验收完成。**

- [发布者与固定版本](https://huggingface.co/constructelligence/construction-site-safety-hazards/tree/79673cff5a8bb249fc8ec9cca3daa19e2ef95191)：模型 AGPL-3.0；卡片称训练数据 CC BY 4.0，多来源归属仍需核对。
- 原生 ONNX 44,766,819 字节；SHA256 `55f4dd2f53158eac4ff093d938b9fbda80c99454a03672145bbc36f539988249`；输入 `[1,3,640,640]`，输出 `[1,18,8400]`，现有 yolo_raw 可读。
- 类别：person、hardhat、no-hardhat、safety vest、no-safety vest、no-mask、gloves、safety shoes、excavator、wheel loader、dump truck、goggles、no-goggles、no-gloves。
- `.v0/ppe-models/platform-trial.json` 等已有真实 CPU 子进程试推。发布者带原标注的拼图返回 16 框，其中安全鞋 2 框，没有手套/护目镜框；拼图不是独立测试集。约 1.1 秒是整次子进程请求时间，不是实时流 FPS。

下一步复用此权重建立五用品逐人评测，不再把“类别表包含”当成小目标检测已有效。许可保持模型、代码、训练数据分开登记。

## 2. ESD 与洁净

[ESD_NOT_WEARING 发布页](https://universe.roboflow.com/dinneshs-workspace/esd_not_wearing)公开索引显示 CC BY 4.0、2,314 图、3 个版本、Models 0；类别为 ESD-NOT-WEARING、ESD-Strap、ESD-WEARING。本轮直开部分页面超时，发布页搜索索引可读；未取得导出包，不报告标签质量或拆分可靠性。页面的通用云 API 示例不是现成 ESD 本地权重。

[cleanroom 发布页](https://universe.roboflow.com/counting-trsti/cleanroom-vhj35)列出 1,000 图、4 类 mask/apron/gloves/goggle、4 个数据版本、1 个托管模型，数据许可 CC BY 4.0。[版本 4](https://universe.roboflow.com/counting-trsti/cleanroom-vhj35/dataset/4)有导出入口；未下载数据或调用需凭据的推理服务，未核实可下载 ONNX 权重。托管服务使用条件不能由数据许可代替。

建议按来源保留图像分组，审核重复图与标签，再使用现有 YOLO 训练执行器。ESD 腕带外观只能证明可见穿戴；不能判断实际接地、电阻或服装材料。洁净行为需要视频步骤标注，静态 PPE 数据不覆盖动作流程。

## 3. 积水与滴漏

[Kairos 发布者](https://huggingface.co/builtbyd3v/kairos-water-rtdetr)标注 Apache-2.0，RT-DETRv2-R18，单类 water。已有资产在 `.v0/puddle-leak/downloads/`：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| kairos-water-rtdetr-int8.onnx | 21,490,967 | `30c96b4e48d5bcc56cb1b129c97ee9ac8875a91d0315b83593c21b2166be9e69` |
| kairos-water-rtdetr-fp32.onnx | 80,701,337 | `00d80de942f79da878fc05728fa77214f696f03147caceccd011e9fdaa0ee969` |

输入动态 batch、固定 640；输出 logits 与归一化 cxcywh pred_boxes，不是 YOLO raw/e2e。已在提交51cc956新增 kairos_rtdetr 原生适配器，并接单图试跑、运行时与固定评测。

旧 `.v0/puddle-leak/candidates.json`/`probe_kairos.py` 使用 letterbox，0.882/0.169不能作为正确适配的评测结论。已按发布者直接缩放RGB/255修正；同一水洼图真实Web结果1框、置信0.862042。相关33项检查通过；无独立标注集精度统计。

已登记 wetfloor ONNX 的 SHA256 与[原发布者](https://huggingface.co/builtbyd3v/spot-wetfloor-convnext-tiny)一致，许可 CC BY 4.0。它输出整图干湿分类，不定位滴漏源。

[thalos-oil-gas-safety-v1](https://huggingface.co/thalostech2025/thalos-oil-gas-safety-v1)提供 YOLO 派生 PT，模型卡 AGPL-3.0，宣称含 leak/flame/corrosion 等类，同时提示实际类别可能变化。本轮未加载 PT、未核原生输出或训练数据许可，不列为可部署模型。海面油污/遥感分割数据也不能替代工业管线滴漏。当前检索范围内尚未核实能直接满足该工况的公开权重。

## 4. 睡岗：有候选，但域不匹配

[cnn-based-drowsiness-detection](https://huggingface.co/notgoodkeeper/cnn-based-drowsiness-detection)公开 MIT 原生 ONNX，输入是预处理后的 412×412 近景人脸，不是整幅监控图。输出类别、置信度及困倦/眼口比例/头姿回归。作者称约 2,376 样本、弱启发式标签、研究用途；[数据卡](https://huggingface.co/datasets/notgoodkeeper/cnn-based-drowsiness-detection-data)也标 MIT。

本轮未下载或试推该候选。接入需核完整预处理、输出顺序和测试主体分组；不值得为了“有权重”直接替换远景睡岗任务。厂区应以可见人体/头部连续片段标注“清醒操作、低头工作、短暂停顿、疑似睡岗”，再训练小动作头；低头工作不能直接当睡眠真值。

## 5. VideoMAEv2 与工业动作资源

已有 `sop-vision/models/action/videomaev2-small-k710.onnx`，输入 `[1,3,16,224,224]`、输出 710 类；完整分类头来自[官方固定权重版本](https://huggingface.co/OpenGVLab/VideoMAE2/tree/706cc172d65ebd4dedbee3f9c0183a93df9fa125)，不是缺 head 的骨干。

- 权重 `distill/vit_s_k710_dl_from_giant.pth` SHA256：`24fb71687fa3671b8387cadfbcbab0f72af695692e93cf1ecc82caa888626172`。
- 官方代码 MIT；此权重仓库 Apache-2.0。其他名字相近的 VideoMAEv2-Base 仓库采用不同许可，不能混用许可结论。
- [官方数据说明](https://github.com/OpenGVLab/VideoMAEv2/blob/master/docs/DATASET.md)：K710 合并 K400/600/700，并非厂区步骤类别表。
- `.v0/videomaev2-gpu-bench.json`：RTX 4060 Laptop、PyTorch CUDA，冷加载至结果约 3,224ms；5 次热运行 P50 30.06ms/P95 31.16ms，显存分配峰值约 229MiB。没有把采样等待、排队或 ONNX CUDA 包含在此数字中。样例输出是 eating spaghetti，不是工业动作验收。

| 数据/基准 | 实际适用内容 | 许可与接入判断 |
|---|---|---|
| [Assembly101](https://assembly-101.github.io/) | 4,321 段玩具车辆装配/拆卸，多视角、粗细粒度步骤 | 数据 CC BY-NC 4.0；可作研究参考，不能直接作为商业训练包 |
| [IKEA ASM](https://ikeaasm.github.io/) | 371 组家具装配，33 动词-物体动作类别 | 数据及 benchmark CC BY-NC 4.0；代码 MIT 不改变数据限制 |
| [MMAction2 VideoMAEv2 配方](https://github.com/open-mmlab/mmaction2/blob/main/configs/recognition/videomaev2/README.md) | K400 等通用动作模型训练/评测 | 可复用工具链，不提供用户工位专用类别头 |

优先按已有时间轴抽帧/动作卡保留原视频来源与时间，按操作者、视频来源和日期拆分，形成用户工位动作集。公开视频动作基座用于微调初始化，YOLO 保持逐帧主链路，动作模型仅对触发后的短片段按需调用。

## 验证边界与后续验收

本轮更新了来源核查和现有代码状态，没有重跑旧模型基准。未取得的导出包、未执行的推理均已逐项标记；检索范围为上述发布者页面、官方代码和本地已有资产，未找到不等于资源不存在。

Kairos 原生适配已完成真实水洼图端到端。下一批验收：PPE 五用品用独立标注算逐人漏检/误配；ESD/洁净先查实际导出内容；睡岗与 SOP 先做同工位跨人视频验证。所有新候选维持 evaluation_only，不自动更改生产规则。
