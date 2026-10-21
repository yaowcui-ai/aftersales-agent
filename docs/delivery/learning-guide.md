# 交付版代码导读

本页供接手代码、复盘和面试准备使用。用户已授权助手完成剩余实现，不再按小节等待教学确认；原代表性实践记录保留在 [environment-check.md](../environment-check.md)。运行、账户和实际验收记录以 [README_DELIVERY.md](../../README_DELIVERY.md) 与交付验证记录为准。

## 从入口读到真实业务

| 顺序 | 代码入口 | 要看懂的职责 |
| --- | --- | --- |
| 1 | [ui.py](../../src/aftersales_delivery/ui.py)、[client.py](../../src/aftersales_delivery/client.py) | Streamlit 8510 调用 FastAPI 8090；页面展示任务、审批及工单，身份由登录确定 |
| 2 | [api.py](../../src/aftersales_delivery/api.py) 的 `create_app`、`identity`、`approver` | 登录、令牌校验、账户绑定和角色检查；客户提交消息，审批员提交审批或恢复请求 |
| 3 | [security.py](../../src/aftersales_delivery/security.py) | 带盐 PBKDF2 密码摘要、恒定时间摘要比较、有签名和到期时间的 token |
| 4 | [agent.py](../../src/aftersales_delivery/agent.py) 的 `build_graph` | 组织 `model → tools → approval → execute → reply`；查询结果不足时可继续调用模型 |
| 5 | [transport.py](../../src/aftersales_delivery/transport.py) 的 `call_tool` | 建立 HTTP MCP 会话，完成 `initialize`，携带短时签名上下文调用白名单工具 |
| 6 | [mcp_server.py](../../src/aftersales_delivery/mcp_server.py) 的 `create_app` | 8091 提供 Streamable HTTP；查询绑定客户，执行还绑定单个动作 |
| 7 | [business.py](../../src/aftersales_delivery/business.py) 的 `Store` | 所有权、金额、库存、审批、事务、幂等和业务结果，最终均由 Python 业务代码验证 |
| 8 | [db.py](../../src/aftersales_delivery/db.py) | SQLAlchemy Core 表定义；`initialize` 建表并只补缺失演示记录，已有状态不会被重置 |

本机业务库是新增的 `aftersales_delivery`，图检查点复用 `aftersales_agent_checkpoints`，任务编号带 `delivery:` 前缀。两者可以使用同一 MySQL 服务，但数据职责不同。交付版新增独立应用，保留旧教学入口及旧业务库，不改变已有 MySQL 账号、权限或服务配置。

## DeepSeek 在哪里参与

`api.Runtime.model` 调用 [aftersales/models.py](../../src/aftersales/models.py) 的 `get_bound_model`；后者复用 [core/llm.py](../../src/core/llm.py) 的 `get_model`，由项目 `DEFAULT_MODEL` 与供应商配置选择实际模型。当前项目的 DeepSeek 接入沿用这一工厂，不能仅凭兼容客户端的供应商标签判断远程服务。

首次模型请求拿到用户消息和工具定义，返回文本或 `tool_calls`；返回 `get_order` 并不代表数据库已经查询。`tools_node` 再调用 MCP，MCP 的 Python 函数调用 `Store.get_order`，由 SQLAlchemy、asyncmy 将参数化 SQL 发给 MySQL。工具结果作为 `ToolMessage` 回到图中，下一次模型请求据此继续判断。

业务完成后，`api.Runtime.reply` 通过 `get_case_reply_model` 再调用模型，把已保存的动作结果转为中文回复。模型负责理解需求、选择查询、提出申请和表达结果；SQL、审批判断、扣库存和退款流水都由程序执行。此处退款只是模拟数据写入，没有向支付渠道转账。

## 一次退款如何走完

1. 客户登录后调用 `POST /tasks/{thread_id}/messages`。API 从 token 得到客户身份，消息不能覆盖 `customer_id`。
2. 模型先查询订单，得到实付、已退、状态等事实，再调用 `prepare_action(kind="refund", ...)`。这个工具没有执行退款的能力。
3. `tools_node` 调用 `Store.prepare`：校验金额与所有权，保存 UUID 动作、版本 `1` 和 `waiting_approval`，同时写入准备事件。
4. `approval_node` 调用 `interrupt`，图将消息、候选和暂停位置保存到 checkpoint；此时还没有退款流水。
5. 审批员调用 `POST /approvals/{action_id}/decision`，只提交 `approved`、`version`。`Store.decide` 检查版本并持久化审批人及决定；夹带另一金额会被接口拒绝。
6. `Runtime.resume` 用 `Command(resume=True)` 恢复图。中断节点会从开头重入，但真正授权来自 `Store.get_action` 读到的数据库记录，不来自 resume 值或聊天里的“已批准”。
7. `execute_node` 检查动作是否已完成；未完成才通过 MCP `execute_action` 执行。该请求的短时上下文绑定客户和 `action_id`，MCP 再核对批准记录。
8. `Store.execute` 在事务中锁定动作和订单，再次校验余额；更新已退金额，插入唯一退款流水、工单及动作结果，提交后才返回。
9. `reply_node` 根据实际结果生成回复。回复失败显示 `reply_failed`；业务动作仍是 `completed`，退款事实不会随模型失败回滚。

## 三个可观察的示例

以下以订单仍符合规则为前提，每个新申请使用不同任务编号；查看现有状态后再演示，不通过删库或重置记录复现。

| 请求示例 | 关键检查 | 执行结果 |
| --- | --- | --- |
| “订单 DEMO-001 两个包裹都查一下，有异常请登记物流工单” | `get_shipments` 逐个返回包裹；异常记录是事实依据 | 审批后创建 `kind=logistics`、`status=open` 的工单，尚未解决物流异常 |
| “订单 DEMO-004 申请模拟退款 10 元” | 本人订单、状态允许、可退余额至少 10 元 | 审批后累计已退增加 1000 分，生成退款流水和 `resolved` 工单 |
| “订单 DEMO-001 申请模拟补发 1 件” | 已发货/已签收、未全退、累计补发未超原数量、SKU-001 库存充足 | 审批后扣减库存、累计补发数量增加，生成补发记录和 `open` 工单 |

审批到执行之间，库存和退款余额可能被其他任务改变，因此准备时与执行时都检查。业务拒绝保存 `failed` 及错误码，保留原批准人；审批员可以在条件恢复后重试原动作，不能顺便改变数量或金额。

## 为什么 checkpoint 之外还要业务幂等

checkpoint 回答“图运行到哪里、保存过哪些消息”；业务表回答“退款是否真的提交、库存是否真的扣过”。数据库提交后可能丢失 HTTP 响应，图也可能来不及保存节点结果；只看图状态会误判尚未执行。

`Store.execute` 从真实批准记录取参数；动作已完成时直接返回原 `result`。客户与任务的唯一键限制重复准备；退款、补发和工单对动作的唯一键限制重复落库。MySQL 行锁与条件更新守住退款额度和非负库存；SQLite 测试用 `BEGIN IMMEDIATE` 串行化写事务，不能替代真实 MySQL 并发验证。

金额内部使用整数分，例如 `10.00 → 1000`，公开接口再格式化为两位小数字符串；不靠浮点累加。版本校验绑定审批员看到的候选；当前候选不可变，版本保持 `1`，同任务换参数得到 `REQUEST_CONFLICT`，另起申请应使用新任务。

审计 `events` 记录准备、决定、执行及工单状态更新。UUID 标识事件，自增 `sequence` 决定插入顺序，避免同时间戳加随机 UUID 排序颠倒；序号可能因回滚出现缺号，不能作为成功业务次数。

## 如何读失败与恢复

- 查询遇到瞬时连接故障会有限重试；明确业务错误直接返回错误码。模型调用有次数上限，无法安全继续时进入 `human_required`。
- 执行失败先核对动作、工单和错误码，再走 `POST /approvals/{action_id}/retry`；接口只接受已批准的原动作。
- 业务已完成但回复失败时，`Runtime.resume` 用 `aupdate_state(..., as_node="execute")` 安排回复节点；已完成业务不会再次执行。
- 页面刷新或应用重启后，`GET /tasks/{thread_id}` 从持久化 checkpoint 读取；`GET /cases` 从业务表读取。恢复需要相同客户和任务编号。

相关回归入口是 [test_business.py](../../tests/delivery/test_business.py)、[test_recovery.py](../../tests/delivery/test_recovery.py)；模型评测定义与实际记录位置见 [evaluation.md](evaluation.md)。测试用替身模型验证确定性流程，真实模型记录另行统计；缺失 Token 或未完成的运行不能算成已测成功。

## 交接时保留的边界

当前是单 API worker、本机签名 token 的演示系统，未接企业 SSO、公网 HTTPS 网关或真实支付/仓储。部署脚本仅管理自己记录并核实的进程。教学阶段以 stdio 理解 MCP，交付版改为独立 HTTP MCP 服务，便于 API 与工具进程分开部署；协议接入方式改变不会替代业务权限控制。


## 最新公开复测口径

2026-10-21固定48案例各3次的完整真实模型回归复测为 **140/144（97.2%）**，4条失败全部保留；80项应用离线测试及7步聊天浏览器验证通过。此处是查看历史案例后的回归复测，不是未见独立测试；历史129/144记录保留，不回填。模型调用的有限重试属于端到端执行流程，144是任务数。详细条件、失败原因及原始记录见[评测方法](evaluation.md#2026年10月21日公开发布复测)与[验收记录](acceptance.md)。
