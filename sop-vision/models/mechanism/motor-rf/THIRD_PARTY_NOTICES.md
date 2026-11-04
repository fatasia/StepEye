# 第三方声明

项目源码尚未选择自己的开源许可证。下列第三方许可不授予本项目整体发布许可。

新增研究数据来源：Virginia Tech National Security Institute，https://github.com/vtnsi/actuator_dataset ，Copyright (c) 2025，仓库MIT许可证。固定提交f9926eb53e1c324d7ffafc4536f2494b7508d7ed；许可证原件保存在本地数据目录及外部实验报告包。没有复制其训练实现，也未在源码包中分发原始传感记录。

气动压力/电流制造过程v3：Žiga Stržinar、Boštjan Pregelj、Janko Petrovčič、Igor Škrjanc、Gregor Dolanc，DOI 10.17632/ypzswhhzh9.3，CC BY 4.0。下载CSV和发布方文件校验清单；演示笔记本仅供格式阅读，没有执行或复制其实现。

Marine Engine Fault v1：Ahmad BahooToroody、Oleksiy Bondarenko、Mohammad Mahdi Abaei、Yoichi Niki、Enrico Zio，DOI 10.5281/zenodo.19857425，CC BY 4.0。原始ZIP、README、LICENSE和CITATION.cff保留在独立数据目录；尚未训练模型。上述两套数据不随源码包分发。

MCC5-THU Motor v1：Shijin Chen、Zeyi Liu、Chenyang Li、Dongliang Zou、Xiao He、Donghua Zhou，DOI 10.17632/6s3dggj9mw.1，Mendeley 发布方许可 CC BY 4.0。原始归档已完成发布方 SHA-256 校验，已解压并完成288个CSV的逐文件数值审计；不随源码包分发。镜像仓库的 MIT 元数据不作为该数据的再分发许可依据。数据说明论文 DOI 10.1016/j.dib.2026.112583，CC BY 4.0；仅引用格式、标度及试验方法，未复制实现或图像。

| 来源 | 使用范围 | 许可与归档 |
|---|---|---|
| aeon 1.6.0 / MiniRocket、MultiRocket | 隔离环境调用原版强基线；MiniRocket 数值参数到 ONNX 的映射 | BSD-3-Clause，见 licenses/aeon-BSD-3-Clause.txt |
| Torchvision / ResNet18 | 标准残差结构、官方 ImageNet 预训练权重的键与运行统计；实现到 layer3 | BSD-3-Clause，见 licenses/torchvision-BSD-3-Clause.txt |
| UCI Hydraulic / MetroPT-3、Cranfield actuator、VisA | 本地公开数据实验和演示回放 | CC BY 4.0；各自来源和处理步骤见 SOURCES.md、data 下清单 |
| CWRU、NASA C-MAPSS | 本地基准实验 | 公开访问不等于已授予再分发许可；原始数据不随源码或模型包分发 |

视觉演示使用 VisA candle 图像及其缩放副本，作者 Yang Zou、Jongheon Jeong、Latha Pemula、Dongqing Zhang、Onkar Dabeer；来源 https://github.com/amazon-science/spot-diff ，数据 CC BY 4.0。热力图是本项目根据模型距离生成的，不是发布方标注图。部署 ONNX 不含原图。

ResNet18 权重来自 https://download.pytorch.org/models/resnet18-f37072fd.pth 。下载器保存实际 SHA-256；视觉模型包含冻结特征权重。分发该模型时连同本文件和 Torchvision BSD 声明保存。ImageNet 上游数据权利需在具体商业发布范围内另行核对。

依赖包本身的许可由各包维护。源码包不捆绑 Python、Node、依赖源码或 node_modules；若制作完整安装器，需重新生成实际依赖的许可清单。

数据署名：Hydraulic Systems — Nikolai Helwig、Eliseo Pignanelli、Andreas Schütze，DOI 10.24432/C5CW21；MetroPT-3 — Narjes Davari、Bruno Veloso、Rita Ribeiro、Joao Gama，DOI 10.24432/C5VW3R；线性执行器 — Ruiz-Carcel、Starr，DOI 10.17862/cranfield.rd.5097649。三者 CC BY 4.0；本项目窗口选择、分组、特征、重采样和模型训练属于处理步骤，不由数据作者背书。

Plant Dataset for StictionGPT v1 — Tianci Xue、Chao Shang，DOI 10.17632/dc9fp3nppf.1，CC BY 4.0。仅下载发布方80份原始文本用于本地审计与控制回路研究，不随源码包分发；未使用其视觉语言模型、提示词、外部训练实现或权重。
