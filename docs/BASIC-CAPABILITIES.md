# 基础能力：配方、画面标定与工业相机

## B5 画面标定 / Q6 平面测量

`visual/calibration.py` 将固定画面中的像素坐标映射到指定平面，适用于地面距离（m）或工件平面距离（mm）。它不是镜头畸变校正、三维重建或计量认证。

- 至少 4 个不重复、非退化的标定对应点，另需至少 2 个独立验证对应点；采用归一化 DLT。
- 标定限定来源 `scope_id`、画面参考状态 `reference`、原始图像尺寸和标定点凸包。测量禁止凸包外推。
- 独立验证最大误差与拟合最大误差均须小于等于用户给定容差；失败版本保留记录但不能测量。
- SQLite 保存不可变版本、原始点、变换矩阵、误差与覆盖范围；没有覆盖修改或自动选用最新版本的接口。

### API

由 `calibration_routes.router(CalibrationStore(db_path))` 注册。认证和写权限沿用服务的统一中间件；应用退出时关闭 store。

`POST /api/calibrations`：

```json
{
  "scope_id": "camera-a",
  "reference": "fixed-lens-v1",
  "image_size": [640, 480],
  "plane": "workpiece",
  "unit": "mm",
  "points": [
    {"pixel": [100,100], "physical": [0,0]},
    {"pixel": [500,100], "physical": [400,0]},
    {"pixel": [500,400], "physical": [400,300]},
    {"pixel": [100,400], "physical": [0,300]}
  ],
  "validation_points": [
    {"pixel": [200,200], "physical": [100,100]},
    {"pixel": [400,300], "physical": [300,200]}
  ],
  "tolerance": 0.1
}
```

地面使用 `plane=ground, unit=m`。返回 `id/version/validated/validation_max/validation_rmse/fit_max` 等完整记录；201 表示记录已创建，不代表验证通过。

`GET /api/calibrations?scope_id=camera-a` 列表；`GET /api/calibrations/{id}` 获取历史版本。

`POST /api/calibrations/{id}/measure`：

```json
{"scope_id":"camera-a","reference":"fixed-lens-v1","image_size":[640,480],"points":[[200,200],[500,400]]}
```

返回两点物理坐标、平面距离、单位、版本及该标定的验证误差。误差是验证点残差，不是测量不确定度或全平面精度保证。未知版本返回 404；越界、来源不匹配、退化、非有限值、未通过验证返回 422。

### 使用约束与验收

物理坐标来自现场实测控制点；代码无法判断填写的尺寸是否真实。`reference` 是调用方确认的画面状态标识，不会自动检测相机移动。相机位置、镜头、焦距、裁剪/缩放或被测平面变化后，应建立新参考状态并重新标定。明显镜头畸变须先在固定图像链路中校正；离开标定平面的物体不能使用此距离。

`tests/test_calibration.py` 使用已知仿射/透视几何验证真实数学计算、m/mm 单位、SQLite 重启持久化、版本、独立验证失败、越界/来源错配拒绝及 HTTP 接口。此次没有相机或标准量具实测，未给出现场毫米精度结论。

## B4 配方多机型

`visual/recipes.py` 保存工位配方、机型标识与不可变版本；切换指定版本，选择历史版本即回滚。配方按工位绑定，一个工位可登记不同机型的多份配方。

- `RecipeStore(db_path)` 持久化配方、版本及最近切换记录，`RecipeService(store, manager)` 执行配置验证与切换；退出时关闭 store。
- `GET/POST /api/recipes`；创建请求为 `{"name":"产品 A","model_key":"SKU-A","station_id":"station-1","patch":{}}`。空 patch 从当前工位提取业务配置；返回配方与 `latest_version`。
- `GET/POST /api/recipes/{id}/versions`；新增请求 `{"patch":{"quality":{"enabled":true,"blur_min":20}}}`，在上个版本上替换相应顶层字段。
- `GET/POST /api/stations/{sid}/recipe`；切换请求 `{"version_id":"指定历史版本 id"}`。GET 返回 active、配置 SHA256 与最多 100 条历史记录；外部修改配置后 active 为 null，避免误报当前配方。

允许修改 zones、roi_states、cycle、steps、violations、safety、quality。station/source、检测器和模型文件路径保持工位所有，不接受在配方中修改。每次切换前执行完整 StationConfig 校验和 SOP 编译；活跃周期拒绝切换，旧线程停机超时拒绝启动新线程。配置文件采用同目录临时文件、fsync 与原子替换。

重载失败时恢复原文件并重载；恢复失败明确返回错误并记录 `restored=false`，不能继续当作成功运行。文件替换和数据库历史不是跨资源事务，进程断电仍需运维核对配置与运行状态；不承诺无停机切换。现有实时线程切换需要现场验证停机窗口。

`tests/test_recipes.py` 覆盖持久版本、切换/回滚、配置漂移、设备字段拒绝、活跃周期拒绝、重载及恢复失败、HTTP 和真实 StationManager 的线程未退出门禁。测试使用受控重载回调，没有驱动物理生产设备。

## B6 工业相机

`visual/industrial_camera.py` 通过可选 Harvester/GenTL 采集，普通 USB/OpenCV/RTSP 路径保持原行为。Harvester 官方提供 CTI 加载、按序列号选设备、有界取帧和节点访问；官方教程的 TLSimu 示例可用于后续有对应 CTI 时的模拟器验收。[官方教程](https://harvesters.readthedocs.io/en/latest/TUTORIAL.html)

```yaml
source:
  type: industrial
  width: 640
  height: 480
  industrial:
    cti: C:/Vendor/GenTL/Producer.cti
    serial_number: YOUR_SERIAL_NUMBER
    exposure_us: 5000
    gain: 0
    trigger: off
```

安装可选 `requirements-camera.txt`，另安装匹配平台/位数的厂商 GenTL producer，CTI 保留原安装目录及其 DLL/SO 依赖。Linux 使用对应 Linux CTI。Harvester 采用 Apache-2.0；厂商 CTI 的许可单独核对。[项目与许可](https://github.com/genicam/harvesters)

- 工位首次 read 才加载 SDK 和设备；仅查询未启动设备不会打开相机。普通服务不安装可选 SDK 也能启动。
- 固定序列号选择；曝光/增益先关闭 Auto，再写入并读回核对。不支持或无法读回则报错，不伪造设置成功。
- 明确设置 FrameStart 与 TriggerMode；`trigger: software` 使用单槽请求，由采集线程执行 TriggerSoftware。重复待执行请求拒绝，不堆队列。请求返回 queued 只表示已接受，不表示已经产生图像。
- `capabilities()` 返回曝光/增益/触发/PixelFormat 节点可读可写状态及实际值；`software_trigger()` 提交软件触发。服务层路由沿用权限控制。
- 目前仅转换 Mono8、RGB8、BGR8；Bayer、打包高位深、多平面载荷明确拒绝，不自动丢失位深。返回 BGR 独立副本后归还采集缓冲。
- 每次 try_fetch 最长 200ms，超时返回现有断流标记，由运行时清除旧视觉证据；停止事件结束下一轮采集并释放设备。CTI 初始化及厂商底层异常卡死不能由 Python 超时强制终止，需现场验证驱动行为。
- 规则时钟使用主机单调时间，设备时间戳仅作为诊断元数据保存，不把未知单位或时基混入 SOP。

本机检查未发现 Harvester 安装或工作区 CTI，未进行 SDK 实加载、TLSimu 或工业真机采集。`tests/test_industrial_camera.py` 的协议替身覆盖惰性加载、真实 NumPy 图像转换、缓冲归还、超时/停止、读回/不支持拒绝及资源释放；它不代表真机通过。
