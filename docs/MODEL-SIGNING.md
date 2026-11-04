# 模型签名

部署管理员可要求平台只使用外部信任公钥签过的模型包。复用 weihu 的 Ed25519 目录清单格式，与已有 RUL 签名工具兼容，不增加服务或逐帧开销。

## 启用

默认不设置 `STEPEYE_MODEL_TRUST_KEYS`，现有未签名模型继续可用。设置后必须是 1–8 个外部 Ed25519 公钥 PEM **绝对路径**的 JSON 数组；空值、空数组、错误格式不会关闭验证。

PowerShell 示例，在启动 StepEye 的同一环境设置：

```powershell
$env:STEPEYE_MODEL_TRUST_KEYS = '["D:/StepEye/trust/release-public.pem"]'
```

Linux：

```sh
export STEPEYE_MODEL_TRUST_KEYS='["/etc/stepeye/trust/release-public.pem"]'
```

多公钥用于轮换；至少一个公钥验证成功才接受。配置只能由本机部署管理员改，HTTP 用户不能指定信任公钥或私钥。变更环境后重启服务；已加载内存模型不会逐帧重复验签。

## 制作模型包

建议每个不可变模型版本独立目录，放 ONNX、类别/预处理元数据和许可来源。签名覆盖目录全部文件与相对路径，新增、删除、替换任何文件都需要重新签名。已有共用 `models/public` 目录也可整目录签名，但后续加入其他模型会使旧清单失效。

私钥在模型目录外离线保存；服务器只部署公钥。使用已有 Ed25519 PEM 密钥，执行：

```powershell
python sop-vision/tools/model_signature.py sign --directory D:/StepEye/models/pcb-v1 --key D:/Signing/release-private.pem
python sop-vision/tools/model_signature.py verify --directory D:/StepEye/models/pcb-v1 --key D:/StepEye/trust/release-public.pem
```

工具生成 `signature.json`，拒绝覆盖已有签名。模型升级应新建版本目录，再走现有评测、审批和发布；不原地替换活动模型。

## 覆盖位置

| 入口 | 检查时机 |
|---|---|
| 已登记模型试跑、预标注、固定评测、创建发布候选 | `LabelEngines.snapshot`，加载前验证模型所在目录 |
| 实时 ONNX 检测 | 创建/重载 `OnnxObjectDetector` 时验证；推理帧不验签 |
| 发布审批、激活、手工回滚 | 账本状态变化前验证 SHA 与签名；失败不写新修订 |
| 自动回滚、工位重启/重载发布 | 复用 `validate_release_model`，当前信任集合重新验证历史版本 |

审批、激活及恢复版本记录 `signature.verified/status/algorithm`，不写密钥内容或私钥路径。未启用时状态为 `not_required`。只通过 SHA 校验不算签名通过。

固定评测先验证登记源模型，再按既有流程复制到隔离目录并校验 SHA；不把可变测试输出写入签名目录。低层科研 CLI 的 `create_backend` 不受这项部署策略约束，避免破坏隔离评测。

RUL 继续使用已有独立外部公钥配置。外部 LLM/VLM 服务、VideoMAE 动作配置、MediaPipe 原生任务和未走登记入口的专项离线工具不在本次统一门禁范围内。签名证明文件来源与完整性，不代表模型精度或许可证审批。

## 验证

`test_model_trust_policy.py` 覆盖未配置兼容、错误配置拒绝、签名文件增删/篡改、公钥轮换、审批/激活/回滚无半套写入、登记快照和实时加载前拒绝。连同发布/自动回滚/检测评测相关检查，本次 72 项通过（39 + 33，无重叠）。
