# 电池专项：密封钉、电芯六面与电池包

已取得完整 EV 电池焊缝数据，完成3轮pilot及一次全训练集10轮扩量，两个ONNX候选并存；误放率仍较高。DAEF 已核作者代码与数据入口，未取得其电池微调权重。资产清单：`sop-vision/models/public/battery-candidates.json`。

## v2：全训练集扩量，一次10轮

复用本机v1的 `best-state.pt`（weights_only，严格匹配固定WeldCNN架构），初始权重SHA256 `4a7996d9fa483374860d0eb36bdcf4492955e445cb65a9d0b89d656777c71691`。一次固定224、batch128、10epochs，全部43,169训练图、5,000验证图；从官方test先排除pilot用过的2,000成员，再冻结抽取5,000图。GPU训练/导出总269.13秒，按验证集平衡准确率选第4轮，不按test追分。

新目录 `.v0/battery-inspection/train-full-v2/`，不覆盖v1。旧checkpoint和v2均在**同一批新5000 test**评测，结果如下：

| 指标 | v1旧权重 | v2扩量 |
|---|---:|---:|
| OK正确 / 误杀（FP） | 2999 / 1045 | 3426 / 618 |
| KO漏放（FN） / 检出 | 100 / 856 | 104 / 852 |
| 良品误杀率 | 25.84% | 15.28% |
| KO召回 | 89.54% | 89.12% |
| KO精确率 | 45.03% | 57.96% |
| KO误放率 | 10.46% | 10.88% |
| 总准确率 | 77.10% | 85.56% |
| 平衡准确率 | 81.85% | 86.92% |

**误杀减少427，但漏放增加4**；不能称全面改善或生产合格。这里的旧权重结果与下文旧2000图pilot分母不同。新5000图仍是官方split内抽样，物理焊点/电芯分组未知，精确去重不能保证工件独立；统一224也不能保证消除采集捷径。

输出ONNX仍317,603字节；SHA256 `f8fdccba12bbfbecdec19a227002d1642fe5539f4730468bca6823760984d081`。分类契约不变：images float32 `[1,1,224,224]`，PIL灰度bilinear/255，logits `[1,2]`，0=OK、1=KO；不进YOLO框检测器。

实图 `test/OK/image_7392.png` 保存为 `train-full-v2/probe.png`（SHA256 `4745d6ffb13a983b20828dcd56de60e3e294f27794bedea422c4253fbef552b8`），v2输出OK概率0.99405，Torch/ORT最大绝对差2.15e-6。同图、CPU intra2/inter1各一次：v1推理0.881ms，v2推理0.695ms；会话加载分别43.85/1.84ms，受到首次运行初始化与缓存顺序影响，不据此宣称v2速度提升或持续FPS。

报告：`train-full-v2/report.json` 与 `comparison.json`。冻结成员SHA256 `2408a8b8357e8bc6441695be272f01417b2ade5b132c14cb11ef1f86c2c132ff`；排除文件SHA256 `82164b9936f1fed48886c65f2f2c103f59b9dfd308e4852a16a54250b435e4fa`；索引SHA256 `f8a7a71c6c8453c63fcdd69afd5c715b1706f2ac2d5d3c8a18a7c2071688d658`。数据CC-BY4仅适用于原数据；自训代码/权重没有擅自改为MIT。

新增工具参数 `--initial-state`、`--exclude-test-members`，异常输出目录仍保留且禁止覆盖；工具5项测试通过，含固定架构state加载与仅排除test。没有继续追分训练，GPU/推理进程均已退出。

## v1：完整数据与首个分类候选

已完成1,153,183,844字节原包，官方MD5 `f187dac063cf656bb627327fa9771d16` 一致；SHA256 `4d05b87dfb66c515f261186f17f409978b2660add911424c5ec5c7f52274e52a`。全量61,670图CRC/解码/SHA检查通过，精确重复和跨split精确重复均0，物理工件分组仍未知。

只跑一次3epoch pilot：随机初始化4层小CNN，seed42，6000 train /2000 val /2000 test，CUDA batch64，总35.70秒。按val平衡准确率选择第1轮权重，test不参与选择。

| 独立test子集（2000张） | 结果 |
|---|---:|
| OK正确 / OK误报KO | 1215 / 403 |
| KO误放OK / KO检出 | 36 / 346 |
| KO召回率 | 90.58% |
| KO误放率 | 9.42% |
| KO精确率 | 46.19% |
| 总准确率 | 78.05% |

此任务类别不均衡，test全判OK的基线约80.9%；总准确率不能单独表示缺陷检测价值。本候选误放及误报仍不足以生产放行，没有追分训练第二轮。

模型：`.v0/battery-inspection/train-v1/battery-weld-cnn.onnx`，317,603字节，SHA256 `b2fc4b04f7fc3c0868c71267770c7db7168e4af0e4580a15984b29b6d622cca0`。明确分类契约：

- 输入 `images` float32 `[1,1,224,224]`：PIL转L灰度，bilinear直接缩放224×224，除255；不输入尺寸、不填边。
- 输出 `logits` `[1,2]`：0=OK、1=KO，调用方一次softmax；不是YOLO框检测器。
- 实际原test图 `test/OK/image_287.png` 推为OK，概率0.9005；Torch/ORT最大绝对差2.98e-7，CPU首次推理1.56ms（不含会话加载，不外推持续FPS）。

完整训练/测试报告在 `train-v1/report.json`，选中成员列表在 `train-v1/selected-members.json`。数据CC-BY4；模型为本项目随机初始化训练产物，无外部模型权重；项目尚未给此产物指定发布许可。

**尺寸偏差全量审计**：train KO宽中位404，OK319。只用train中位间阈值361.5猜类别，test准确75.41%、平衡准确81.28%、KO召回90.79%；这不是视觉模型。显著类别尺寸分布差异必须保留为评测风险。固定224仅去掉显式尺寸元数据，不证明消除了纹理拉伸/采集工况捷径。完整审计在 `.v0/battery-inspection/audit/audit.json`。

以下保留原始来源核验与下载阶段记录；最终状态以上述结果为准。

## 1. EV 焊缝数据：已下载并验证六张原图

原始发布：[OPENZDM，Zenodo 14025630，v1](https://zenodo.org/records/14025630)，DOI `10.5281/zenodo.14025630`，2024-11-01。Zenodo原始API明确数据许可 **CC BY 4.0**，不是由镜像推断。

这个记录只有一个 **1,153,183,844 字节（约1.15GB）** ZIP，不是271GB；页面累计下载流量不能当作文件大小。记录内是电池焊缝外观二分类，不含缺陷框或密封钉工艺标签。

实查 ZIP 中央目录：

| 拆分 | OK | KO | 合计 |
|---|---:|---:|---:|
| train | 34,916 | 8,253 | 43,169 |
| val | 7,482 | 1,768 | 9,250 |
| test | 7,482 | 1,769 | 9,251 |
| 总计 | 49,880 | 11,790 | 61,670 |

服务器已实测支持HTTP206 Range。本次只取65KB尾部、约6.47MB中央目录和每拆分每类一张图，未整包下载。6张成员均逐项CRC32通过、OpenCV解码成功，SHA256与原始ZIP成员名在 manifest。整包官方MD5为 `f187dac063cf656bb627327fa9771d16`，由于未下整包，**没有验证整包MD5**。

本机样本目录：`.v0/battery-inspection/`，如 `test_KO_image_1.png` 和 `test_OK_image_1.png`。样本是细长实际焊缝裁片；已目视查看KO样本，不是生成图。

**实际发现**：作者文字称409×40灰度图，但六张实际PNG均解码为3通道，尺寸分别为：test KO 410×40、test OK 316×35；train KO 414×40、train OK 315×34；val KO 410×41、val OK 317×31。不能按固定尺寸盲读，也不能忽略这个小样本中类别与裁片尺寸相关的问题；全量训练前需检查是否能只靠尺寸或背景猜标签。

### 可执行的训练下一步

优先从上述 Range 索引按作者三种拆分分别分层取图，在独立目录保留原成员名和标签，不用文件名同名覆盖。核对跨拆分内容哈希/近重复及原电池来源，统一裁片有效区域与宽高比策略，检查尺寸类别偏差，然后用现有隔离环境训练轻量 **OK/KO分类器**，导出ONNX，以KO召回、误放率、混淆矩阵验收。这里没有框标注，不能直接假装已能训练缺陷定位YOLO。六张样本仅用于读取和标签协议，不足以训练或计算准确率。本批未训练。

## 2. DAEF/BSD：作者入口已核，数据尚未取得

[作者仓库](https://github.com/sober-white/DAEF/tree/ef0e5538a907d4647bb74dc599c8a598190c846e)，固定提交 `ef0e5538a907d4647bb74dc599c8a598190c846e`。作者声明 BSD 为1,266张5184×3456图、5.77GB、COCO标注，五类是侧面凹陷、防爆阀凹陷、电解液泄漏、侧面挤压、蓝膜缺失。

作者 README/完整仓库树已下载。根代码 LICENSE 为 **Apache-2.0**；外部百度数据包未单独明确数据许可，不能把代码许可自动套给数据。

数据唯一入口：[作者 BSD 百度分享](https://pan.baidu.com/s/1GS3CKhxnUPV_73OZjhLuSQ?pwd=hcqm)。本轮自动访问未能打开，未下载包或核验COCO标注。仓库没有样本图/COCO文件；唯一PTH是15,500,899字节的 `deim_dfine_hgnetv2_n_coco_160e.pth`，作者训练命令用它作COCO初始化，**不是已微调电池缺陷权重**，所以未重复下载这个通用初始化。

DAEF覆盖的是部分表面类别，不自带六面归属/SN绑定。不能因为有侧面和阀面图片就标记完整六面质检已实现。

## 3. 密封钉与电池包的边界

- **密封钉**：本批没有取得可核许可的专用权重或带标签原图。已查到[密封钉点云分割研究](https://www.sciencedirect.com/science/article/pii/S0952197625028246)，其模态为3D点云，不等于普通可见光焊缝检测；没有据论文题目登记已可用模型。
- **六面检**：模型负责各面局部缺陷；业务还必须以电芯SN聚合 front/back/left/right/top/bottom，检查缺面、重复面、错SN和复拍覆盖规则。两项数据均未提供这套身份信息，单图输出不能代表六面通过。
- **电池包**：EV焊缝OK/KO可作为包制造的一项外观点检，不能替代连接器/母排装配、紧固件、尺寸、气密、电气或热安全检测。
- [SD-YOLO作者项目](https://github.com/luckycloud12138/Lithium-battery-project)另有论文样本入口，但本次可见仓库没有发布权重或明确LICENSE；未把论文指标当成本平台结果。

## 工具与复现

完整数据下载、全量审计及一次真实训练已完成。密封钉专用模型、DAEF完整数据及六面/电池包专用缺陷验收未完成；六面结果汇总流程见[BATTERY-SIX-FACES](BATTERY-SIX-FACES.md)。

下载器保留已完成分块和残块；网络兼容使用TLS1.2/HTTP1.1，仍验证证书。只有完整大小和官方MD5通过才进入后续。训练输出目录必须是新目录，避免覆盖旧结果。

```powershell
python sop-vision/tools/battery_weld_download.py .v0/battery-inspection --seconds 1200 --transport curl
python sop-vision/tools/battery_weld_audit.py .v0/battery-inspection/ev-weld-v1.zip --out .v0/battery-inspection/audit
.v0/train-venv/Scripts/python.exe sop-vision/tools/battery_weld_train.py .v0/battery-inspection/ev-weld-v1.zip --index .v0/battery-inspection/audit/deduplicated-images.csv --out .v0/battery-inspection/new-run --epochs 3
```

工具检查覆盖断点Range/TLS、跨split重复和尺寸审计、残包拒绝、训练输出防覆盖与异常关闭。Windows实机下载/训练已执行；Linux下载和训练脚本尚无同设备实测。
