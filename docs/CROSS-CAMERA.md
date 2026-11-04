# 多机位地面观测

在重叠地面区域，对不同机位的目标生成几何关联候选。入口：视觉应用 → 评测与质量工具 → 盘点对账 → 多机位观测。

## 使用

管理员展开“配置观测组”，填写名称、共同地面坐标系、识别类别，选择至少两个已验证标定及对应工位，保存后手动刷新。可删除不再使用的组；标定与库存记录不受影响。配置保存在数据目录的 `cross_camera.json`。

- 标定必须是 `ground`、单位 `m`，scope_id 对应工位，reference、图像尺寸与指定版本一致。
- **共同坐标系是人工配置前提**：各标定物理点必须使用相同原点、方向和单位。填写相同名称不会自动配准，也无法证明真实坐标一致。
- 只支持 camera / rtsp / industrial；文件、模拟源有独立时间轴，返回 unknown。
- 使用服务收到并开始处理画面的单调时钟，不是相机曝光时间；传输延迟差异会影响关联。

## 结果语义

“各机位观测合计”是各有效成员的原始观测总数，**未去重**。候选关联组仅接受同类别、跨机位、距离满足阈值且组内两两相容的观测；同一机位不合并，一对多或链式匹配进入歧义集合。没有 `confirmed_count`，不写入库存账实结果，不做 ReID，也不维持跨遮挡身份。

成员暂失、停止、画质屏蔽、类别不支持、标定不匹配、过期、接收时间差过大时返回 unknown。新鲜空观测与缺失观测分开处理。总观测超过 512 时保留原始观测、返回 `observation_limit`，不执行平方复杂度关联。框底中心投影仅适合地面目标，不适合悬空物体或堆叠货箱。

## 接口与生命周期

`CrossCameraStore(path, calibrations, snapshot_getter)`；`snapshot_getter(station)` 返回运行时的 `cross_camera_snapshot()`。路由 `router(store, allowed_stations)` 的权限回调返回可见工位集合或 None。读取必须拥有全部成员权限，保存/删除要求 admin，旧组成员校验与写入在同一锁中；快照使用已授权配置副本，不重新读取可能并发改变的成员。

- GET `/api/cross-camera/groups`、`/{id}`、`/{id}/snapshot`
- PUT `/api/cross-camera/groups/{id}`
- DELETE `/api/cross-camera/groups/{id}`

没有后台轮询、新线程或模型。检测线程只发布轻量快照，几何关联在 API 请求中执行。

## 本轮检查

`tests/test_cross_camera.py`：21 项通过，覆盖持久化、候选、时效、时间差、类别、非实时源、同机位不合并、歧义、观测上限、权限、授权配置副本、锁内保存/删除。JS 语法检查通过。

浏览器通过真实 `build_app`、真实配置和快照 API，完成页面入口、新组保存、候选查看、刷新、删除。观测与相机对象为合成测试夹具，验证软件链路，不代表真机同步或现场精度。证据：`.v0/cross-camera-ui/candidates.png`、`result.json`；运行脚本 `.v0/run_cross_camera_ui.py`、`.v0/check_cross_camera_ui.py`。测试服务已停止。
