# 电芯六面设备结果汇总

在视觉应用→质量工具→电芯六面检，锁定产品型号、SN、检测批次，逐面上传设备图像和原判。模型试跑独立显示，不修改设备原判；默认待复核。

复用设备结果存储，不新建影像库。POST `/api/equipment-results` 的metadata新增product_id、inspection_id、face三项（必须同时提供）；face为front/back/left/right/top/bottom。result支持OK、NG、UNCERTAIN。幂等重试保持同一记录，既有不带面别的设备接口保持兼容。

GET `/api/battery-inspections?product_id=...&sn=...&inspection_id=...` 返回六面状态：缺面pending，任一NG则NG，待复核/同面多记录/跨面重复图片则review；六面均为OK且无冲突才complete。complete只表示设备声明记录齐备，不表示平台已完成缺陷模型验证。复检使用新的inspection_id，不覆盖原记录。

六面图片归档、设备结果与模型试跑已接；密封钉专用识别、六面相机联动/自动触发/工件翻转和Pack检测配方未由此实现。模型与数据来源见BATTERY-INSPECTION.md。

实测：真实Web默认UNCERTAIN上传正面后1待复核/5缺面；切换SN清空图片/模型证据；不可用模型不判合格。后台验证六面齐备、缺面、重复图片、冲突、NG、跨产品/SN/批次隔离及旧接口幂等。
