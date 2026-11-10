# MCP 接入

StepEye 可把项目、标注和质量报告交给外部 Agent 只读查询，也可显式调用本机管理员登记的外部 MCP 工具。

- 两个方向分开：`mcp_server` 提供 StepEye 数据，`mcp_connectors` 调用外部服务。
- 默认只读、不传图片；图片读取需单独 scope 和进程开关。
- 专用令牌按项目授权、可到期和撤销，不复用浏览器会话。
- stdio 和本机 Streamable HTTP 均使用官方 Python SDK；无需 Docker。
- 模型建议仍是草稿，MCP 不开放确认真值、训练、发布或删除。

## 最短上手：让 Agent 读取 StepEye

先启动启用 `--auth` 的 StepEye。管理员用已登录会话调用 `POST /api/mcp/tokens`，创建只读令牌：

```json
{"name":"本机标注助手","scopes":["read"],"project_ids":["项目ID"],"expires_in_s":3600}
```

`project_ids: []` 表示全部项目，不是无权限；优先填写具体 ID。明文 `token` 仅创建时返回一次。`GET /api/mcp/tokens` 查看授权，`DELETE /api/mcp/tokens/{id}` 撤销，`GET /api/mcp/audit` 查访问记录；这些管理接口只接受管理员会话，不接受 MCP 令牌。

### 独立安装

在仓库根目录创建单独环境，不要装进视觉运行环境：

```powershell
python -m venv .v0/mcp-venv
.\.v0\mcp-venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

Linux 对应：

```sh
python3 -m venv .v0/mcp-venv
.v0/mcp-venv/bin/python -m pip install -r requirements-mcp.txt
```

固定官方 `mcp==1.30.0` 的仍维护 v1 分支，保留现有客户端的 `ClientSession` 握手。当前官方 v2 已改 API/协议，迁移另行验证；这不是第三方 `fastmcp` 包。MCP 环境不安装模型、不加载 cv2、不升级主环境的 FastAPI/Pydantic。[官方维护说明](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[v2 变更](https://py.sdk.modelcontextprotocol.io/whats-new/)。

### stdio（推荐）

在 MCP 客户端配置中指定绝对 Python 路径，工作目录指向仓库根目录，启动参数为：

```text
-m visual.mcp_server
```

通过客户端的本机环境配置传入：

| 变量 | 用途 |
|---|---|
| `STEPEYE_MCP_API_URL` | 默认 `http://127.0.0.1:8000`；仅 HTTP(S) 的 loopback、RFC1918、IPv6 ULA 源站，不接受路径/凭据/查询参数 |
| `STEPEYE_MCP_TOKEN` | 管理员创建的 `se_mcp_` 专用令牌，必填 |
| `STEPEYE_MCP_ENABLE_IMAGES` | 默认 `0`；显式设 `1` 才注册图片工具，后端还会检查 `images` scope |

配置文件和系统凭据存储只留本机，别提交到仓库。代理不自动读取 `.env`、浏览器登录态或认证数据库；由启动器显式注入所需环境变量。stdio 的 stdout 只传 MCP 报文，日志写 stderr。

### 本机 Streamable HTTP

同样注入上述变量，在 仓库根目录运行：

```text
<MCP环境的Python绝对路径> -m visual.mcp_server --transport streamable-http --port 8766
```

客户端连接 `http://127.0.0.1:8766/mcp`，每次请求带 `Authorization: Bearer <同一专用令牌>`。固定绑定 `127.0.0.1`，官方 SDK 验证 Host/Origin，只接受该端口的 `127.0.0.1`/`localhost`；不提供 CORS 放行。不要经公网隧道、`0.0.0.0` 或反向代理暴露此进程。这一版是本机共享秘密认证，不是公网 OAuth 服务。[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

令牌撤销后，每次工具调用都会被 StepEye REST 重新拒绝；已返回给客户端的数据无法收回。HTTP 的静态秘密门禁仍允许发现工具名称，不代表后端数据授权仍有效。换令牌需重启代理和更新客户端配置。

## 只读工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `stepeye_list_scenes` | `limit`, `offset` | 脱敏场景摘要，不含 YAML、相机地址或提示词 |
| `stepeye_list_label_projects` | `limit`, `offset` | 授权项目列表 |
| `stepeye_list_assets` | `project_id`, `limit`, `offset` | 图片元数据和分页，不含像素 |
| `stepeye_get_annotations` | `asset_id` | 当前标注、修订和人工确认状态，包在 `data` 中 |
| `stepeye_get_label_job` | `job_id` | 已有任务状态，包在 `data` 中，不启动推理 |
| `stepeye_get_quality_report` | `project_id` | 数据集就绪检查，包在 `data` 中，不是检测准确率 |
| `stepeye_get_asset_image` | `asset_id` | **可选** PNG/JPEG/WebP，传给当前 MCP 客户端，需显式 opt-in |

列表默认 50 条、最多 100 条，返回 `has_more` 和 `next_offset`。场景和项目的当前 REST 是数组，代理在 2 MiB 总响应上限内分页；超限会拒绝，不静默截断。资产列表由后端分页。ID 只能使用列表返回的字母/数字/下划线/连字符，不能传 URL 或文件路径。

工具通过固定白名单 GET 访问同一 StepEye REST，不直读数据库。总超时 10 秒（包含排队）、最多 4 个并发、解码后的响应上限 2 MiB；大图可在 Web 查看或先上传较小副本。REST 重定向关闭，忽略系统 HTTP 代理；DNS 全部地址先验为允许网段，再固定连接 IP。HTTPS 保留原主机 SNI/Host 和证书校验。跨机 API 使用可信 HTTPS；私网 HTTP 本身不加密令牌。

服务端将令牌绑定到创建它的管理员，逐次检查用户仍存在且仍为管理员、令牌有效期/scope/project_ids。MCP 令牌不能调用普通写接口、其他平台 API、WebSocket、原始配置、导出 ZIP 或人工确认。普通 viewer/operator 也不能创建令牌。

## 调用外部 MCP：本机命名连接器

首批是 CLI/可调用 Python 客户端，不是自动聊天门户。管理员手工维护一个绝对路径 JSON 文件。没有服务配置 API，没有 URL/命令参数由网页、标注文字或 Agent 动态生成；程序不会自动连接或自动执行发现的工具。

```json
{
  "version": 1,
  "connectors": {
    "local_docs": {
      "enabled": true,
      "transport": "streamable-http",
      "url": "http://127.0.0.1:9100/mcp",
      "bearer_env": "DOCS_MCP_TOKEN",
      "allow_tools": {"search_documents": ["query", "limit"]},
      "timeout_s": 10,
      "max_response_bytes": 262144
    }
  }
}
```

`bearer_env` 指定该外部服务的独立凭据变量名；不填表示明确匿名连接。不能借用 `STEPEYE_MCP_TOKEN`。文件里不写 token。配置应放在仅管理员可写的目录，Windows 设置 ACL，Linux 设置所有者和 `0600`；显式配置路径是本机信任边界，不能交给普通 Web 用户改。

在 仓库根目录，用 MCP 环境的 Python：

```text
-m visual.mcp_connectors --config <配置文件绝对路径> list-connectors
-m visual.mcp_connectors --config <配置文件绝对路径> list-tools local_docs
-m visual.mcp_connectors --config <配置文件绝对路径> call local_docs search_documents --arguments '{"query":"安全帽标注规范","limit":5}'
```

停用时将 `enabled` 改成 `false` 并保存。每次操作及排队结束后重读配置，下一请求立即拒绝；已开始的请求最多继续至总超时。

连接器规则：

- 只连接固定的字面量 loopback IP（`127.0.0.1`/`::1` 等）的 HTTP(S) MCP 路径；不接受任意公网/局域网 URL、DNS 主机名、命令、shell 或外部 stdio。
- `allow_tools` 与每个工具的参数名必须由管理员逐项登记。`readOnlyHint` 是对方自报信息，不作授权依据；即使对方谎报只读，未登记工具也不能调用。管理员仍须核对工具真实行为，白名单不是沙箱。
- 仅传显式的小型标量 JSON 元数据：字符串≤512 字符、整批≤8 KiB，无嵌套文件/图片数据、URL、data URI 或本机路径参数；不读取 StepEye 图片或本机文件。文本本身仍可能敏感，调用前检查内容。
- 最多 2 个操作并发，总超时默认 10 秒；HTTP 原始响应在 SDK 解析前限 256 KiB（可配置 1 KiB–1 MiB），拒绝压缩和重定向。返回图片、音频、资源附件一律拒绝，远端错误不原样回显。
- 远端工具描述/返回文本都是非可信数据，不会变成本机命令，不会自动确认标签、触发训练或调用下一个工具。

外部 stdio 暂不开放：官方 v1 stdio 客户端没有原始消息长度上限，需要另加受控进程/报文隔离后再验收。公网 OAuth、多用户远程 MCP、外部图像共享、Web 连接器管理和写工具授权都在后续阶段。

## 验证

在 仓库根目录，用独立 MCP 环境执行（测试环境另装 pytest）：

```text
<MCP环境Python> -m pip install pytest
<MCP环境Python> -m pytest tests/test_mcp_client.py tests/test_mcp_connectors.py tests/test_mcp_sdk.py tests/test_mcp_connectors_sdk.py -q
```

覆盖官方 `ClientSession.initialize`、`list_tools`、所有默认工具调用、stdio 图片 opt-in、HTTP Host/Origin/Bearer、撤销后数据拒绝、外部命名连接器真实握手/白名单/停用/错误凭据/图片拒绝/解析前限额/超时。SDK 专项在默认视觉环境缺依赖或非 v1 时跳过，不改变主安装。

本轮还对实际运行的 StepEye REST 完成贯穿检查：管理员发放限定项目的令牌 → 官方 stdio 握手与 6 个工具发现 → 仅见授权项目 → 读取质量报告 → REST 撤销 → 后续工具调用拒绝。检查用令牌已撤销，不依赖固定账号或入库凭据。

本机 Windows 的 SDK 契约测试通过不等于现场准确率、Linux 实机或任意第三方 MCP 服务兼容性验收；Linux 在独立 CI 环境验证同组测试。现场标注与工业验收按现场流程执行。

### 可选：智能体提交框建议

默认仍是只读6个工具。管理员发放含 `read,draft` 且限定项目的专用令牌，代理显式设置 `STEPEYE_MCP_ENABLE_DRAFTS=1` 后，才会提供 `stepeye_propose_annotation`。参数是资源ID、当前修订号和归一化框。建议记录为 agent，过期修订及已确认资源拒绝覆盖；只有人在 Web 确认后才进入数据版本。任务启动、训练、发布不对 MCP 开放。
