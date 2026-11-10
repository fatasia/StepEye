# 告警通知

StepEye 用可选的 [Apprise](https://github.com/caronc/apprise) 原生 Python SDK，将新告警发送到邮件、飞书、企业微信和钉钉。默认不启用，不需要 Docker。

- 通知失败不影响告警落库；每个目标分别记录成功或失败。
- 默认最多排队 128 条，串行发送；每渠道每条告警最多尝试 2 次。
- 有任务才启动线程；队列清空即退出。每次发送使用独立子进程，默认 10 秒超时后终止。
- 令牌与 SMTP 密码只放本机环境变量或 `.env`，状态接口不返回 URL、消息正文或异常原文。

## 安装与配置

在项目根目录创建独立环境，主视觉环境无需安装 Apprise：

```powershell
python -m venv .v0/notify-venv
.v0/notify-venv/Scripts/python.exe -m pip install -r requirements-notify.txt
```

Linux 使用 `.v0/notify-venv/bin/python`。SDK 固定为 Apprise 2.1.0（[PyPI](https://pypi.org/project/apprise/2.1.0/)，BSD-2-Clause）。

编辑服务使用的 `.env`，填入解释器的绝对路径，以及需要的渠道：

```dotenv
STEPEYE_NOTIFY_PYTHON=D:/Documents/ChatGPT/visoul/.v0/notify-venv/Scripts/python.exe
STEPEYE_NOTIFY_FEISHU_URL=feishu://YOUR_BOT_TOKEN
STEPEYE_NOTIFY_WECOM_URL=wecombot://YOUR_BOT_KEY
STEPEYE_NOTIFY_DINGTALK_URL=dingtalk://YOUR_ACCESS_TOKEN
STEPEYE_NOTIFY_EMAIL_URL=mailtos://USER:APP_PASSWORD@smtp.example.com:465?to=operator@example.com
```

以上均为占位符；SMTP 参数以邮件服务商为准，URL 中的密码特殊字符需要百分号编码。企业微信应用消息可用 `wechat://CORP_ID:APP_SECRET@AGENT_ID/USER_ID`；群机器人用 `wecombot://`。[邮件](https://appriseit.com/services/email/)、[飞书](https://appriseit.com/services/feishu/)、[支持渠道与语法](https://appriseit.com/services/)以官方文档为准。

也可以用 JSON 数组配置多个目标：

```dotenv
STEPEYE_NOTIFY_URLS=["feishu://YOUR_BOT_TOKEN","wecombot://YOUR_BOT_KEY"]
```

`STEPEYE_NOTIFY_URLS` **非空时优先**，忽略四个单渠道变量；`[]` 表示显式关闭新渠道。空字符串则读取单渠道变量。系统环境变量覆盖 `.env`；修改后重启服务。

仅接受 `mailto`、`mailtos`、`feishu`、`wecombot`、`wechat`、`dingtalk`，最多 8 个目标。不接受 HTTP/HTTPS 网页、任意 webhook、远程配置文件或附件。URL 相同的目标去重；不同目标即使属于同一渠道也分别发送。配置错误只显示固定错误码，不回显输入。

## 查看结果

管理员可从 `GET /api/notifications/status` 查看配置状态、队列和最近发送结果。目标显示为 `email-1`、`feishu-1` 等名称；`sent`、`failed`、`timeout`、`unavailable`、`invalid`、`cancelled` 分别表示成功、失败、超时、SDK 不可用、配置无法解析、关闭时取消。`queued` 和 `sending` 为处理中状态。

SDK 缺失或目标无效不会重试；普通失败和超时最多再试一次，间隔 250ms。Apprise 的额外渠道重试被关闭，URL 中的 `retry` 不能放大尝试次数。队列满时拒绝该次通知，告警仍保存在告警中心。

关闭服务会取消待发送项并终止正在发送的子进程，不等待网络响应。接收方已经收到但响应丢失时，重试可能产生重复消息；该实现不提供 exactly-once 投递。

最近记录和按告警 ID 去重只在进程内，默认最多保留 256 条；历史淘汰后或服务重启后不再记得此前投递。没有跨重启恢复、持久化发件箱或自动补发。

原有 `FEISHU_WEBHOOK_URL` 仅在**没有新通知配置**时使用，防止同一告警双发。它保留原发送路径，不包含在 Apprise 渠道状态中。告警表的 `pushed` 仍是旧飞书标记；多渠道发送结果读取通知状态接口，不能用该字段代表全部渠道成功。

## 开发接口与验证

```python
dispatcher = NotificationDispatcher.from_env(env_file)
dispatcher.submit(alert_record)  # 告警先落库；这里只接收 AlertStore 的 id/station/kind/severity/message
status = dispatcher.snapshot()  # 只含脱敏状态，不含目标 URL
dispatcher.close()              # 幂等、非阻塞
```

`submit` 返回 `accepted`、`reason`、`channels`；重复告警 ID、队列满、关闭和未启用均不会抛出发送异常。调用方不把 `accepted` 当作投递成功。

本机验证：mock 多渠道、独立成功/失败、两次上限、并发队列、空闲退出/再启动、真实 Python 子进程超时与关闭终止；隔离 Apprise SDK 验证六种 URL 的解析，发送方法被替换，不访问外部通知服务。实际 SMTP、机器人权限、签名及接收端投递仍需部署环境验收。
