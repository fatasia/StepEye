# 设备结果接入：现状与缺项（C4）

日期：2026-11-05。范围：盘点既有能力，登记真实缺项；无厂商样本不虚构协议。

## 已覆盖（不再重建）

| 能力 | 位置 | 证据 |
|---|---|---|
| 通用结果导入（OK/NG/UNCERTAIN） | `sop-vision/visual/equipment_results.py::ingest` | 幂等键唯一约束，重复提交比对 signature |
| 设备类型枚举 | `equipment_results_types.py::source_kind` | AOI/AXI/line_scan/xray/ct/other |
| SN/批次/面别分组 | `complete_face_identity` 校验器 | 六面必须同时提供 product/inspection/face |
| 时间归一化 | `observed_at` 必须显式时区，入库存 UTC | 乱序到达按时间字段排序，不按到达顺序覆盖 |
| 复判/重检幂等 | `review`/`recheck` 独立幂等键 | 机器建议与人工复核分开存 |
| Modbus 只读桥 | 既有（HANDOFF 记录） | 只读，不写设备 |
| 扫码 | `code_reader.py` + reader_routes | 已接读码与 OCR 流程 |
| 质量报告/复判/追溯联动 | quality_routes + equipment_results_routes | 已接（HANDOFF 记录） |

## 缺项（缺真实样本，暂缓直到取得样例）

1. **厂商专有格式适配**（AOI/AXI/线扫/X-ray/CT 的私有文件与协议）：无任何厂商格式样本。通用 JSON/图像导入已可用；专有适配器在拿到真实导出样例前不开发。需要：至少一份脱敏导出文件 + 字段说明。
2. **扭矩扳手信号解析**：Atlas Copco Open Protocol 等为 TCP 二进制会话，无样例无对端，不虚构实现。可离线验证的替代路径：接受扭矩 CSV/JSON 报表文件导入（走现有通用导入），但缺真实报表样例确定字段名。
3. **断连重放语义**：HTTP 导入天然无状态；客户端重试用幂等键去重已覆盖。串口/TCP 长连接类设备接入需网络层重连设计，当前无设备可验证。

## 边界声明

- 幂等键由客户端生成，同键不同内容返回 409 而非覆盖（`ingest` signature 比对）。
- 乱序：两条不同 observed_at 的结果并存，不做"后来者覆盖"——判定合并是复判（review）流程的职责，不自动合并。
- 以上均为设计行为，如与现场实际冲突需在拿到真机数据时重新评估。
