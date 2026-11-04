# 工业装配动作子集

2026-11-04：已取得 5 条完整装配/维护录像，共 137,804,832 字节，提取 41 段 VideoMAEv2 冻结特征。验证与测试录像尚未完整取得，因此没有训练动作头、没有登记新模型。

## 来源与许可

[IndustReal 作者仓库](https://github.com/TimSchoonbeek/IndustReal)与 [4TU 数据集 v2](https://data.4tu.nl/articles/dataset/b008dd74-020d-4ea4-a8ba-7bb60769d224)均标明 Apache-2.0。本地保存数据页、LICENSE、官方动作识别说明和原始三个 CSV。数据是人员实际操作构造玩具组件的连续 RGB 视频，不能等同于生产线动作验收。

官方标签共 train 3,667 / val 1,928 / test 3,678 段，训练集含 72 类；包括零件对齐、插销、垫圈安装、螺母拧紧/松开、查阅说明等。RGB 压缩包 4,960,644,152 字节；使用公开 HTTP Range 读取 ZIP 目录及指定成员，没有下载全包。另有作者 MViT/SlowFast 权重包 1,266,190,326 字节，本轮未下载。

## 固定划分

按官方 split，在每组中按完整录像文件大小排序，每名参与者只选一条，得到约 288 MB 的 10 条目标录像：

| 分组 | 固定录像 | 当前完整数 |
|---|---|---:|
| train | 21_main_0_1、07_main_0_1、25_assy_2_1、27_main_3_1、15_main_3_2、11_main_0_1 | 5/6 |
| val | 14_main_2_2、24_main_0_1 | 0/2 |
| test | 10_main_3_4、03_main_0_1 | 0/2 |

已下载视频通过 ZIP CRC/大小验证及 SHA256 登记；实际解码为 1280×720、10 FPS。特征缓存包含基座 SHA、预处理代码 SHA、视频 SHA、标签 SHA、类别顺序和采样协议，续跑不重复提取。

计划仅评四类：`align_objects / tighten_nut / fit_nut / loosen_nut`。每段均匀采样 16 个不同帧，不足 16 帧则剔除；完整子集应为 train 45 / val 18 / test 16 段。保持官方跨人划分，启动时检查参与者无交叉。冻结原 VideoMAEv2 Small，384 维特征；均值方差只用训练集，线性头固定 200 步、验证损失选一次 checkpoint，测试仅最终评一次。**该训练尚未执行。**

## 中断及续接

第六条整段请求曾在已读 33,259,072 字节后缺 34,566 字节中断；初版在内存中读整段，未保留该不完整请求体。后续工具改为 1 MB 分块落盘、最多两个独立连接，保留了 3,188,888 字节的部分 Range 缓存。新请求连续遇到 TLS EOF 后已停止，不再重试。完整视频和所有持久缓存均保留。

证据在 `.v0/industrial-action-20261104/`：`selection.json`、逐视频 `*.download.json`、`acquisition-status.json`、`features-*.npz`、官方 CSV 与来源文件。所有下载和 GPU 提取进程已退出。

从项目根目录续接，下载器验证已有文件 CRC，不覆盖坏文件，不把部分文件当成视频；新视频用 `.part` 原子发布：

```powershell
python sop-vision/tools/industreal_subset.py --output .v0/industrial-action-20261104
# 下载间隙只提取已完整的视频，不训练
.v0/action-venv/Scripts/python.exe sop-vision/tools/train_industreal_probe.py --data .v0/industrial-action-20261104 --extract-only
# 全部下载完成后，单次训练与保留测试
.v0/action-venv/Scripts/python.exe sop-vision/tools/train_industreal_probe.py --data .v0/industrial-action-20261104
```

下载工具复用项目现有 httpx，不给主运行包增加训练依赖。测试覆盖 Range 缓存续接、响应范围错误、坏缓存、同长度视频损坏和参与者泄漏；没有更改共享动作模块或模型目录。
