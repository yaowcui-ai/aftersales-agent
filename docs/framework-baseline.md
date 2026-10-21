# 框架基线与目录导读

整理日期：2026-10-19。

## 已完成的准备

- 原始框架：[JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit)。
- 起始提交：[58f6a059f9787db1a68946030b35dd61023662fa](https://github.com/JoshuaC215/agent-service-toolkit/commit/58f6a059f9787db1a68946030b35dd61023662fa)。
- 用户亲手完成浅克隆，并解释了记录提交编号的作用。
- 助手将框架复制到项目根目录，129 个源码及配置文件经 SHA-256 比较，内容与下载版本一致；现有虚拟环境和学习文档保留。
- 项目根目录现在是独立 Git 仓库；当前开发分支 `codex/aftersales`，原作者仓库远程名称为 `upstream`。保留原框架许可证与来源信息。
- `_framework` 是保留的原始下载副本，用于对照。通过 `.git/info/exclude` 的 `/_framework/` 规则在本机忽略；后续操作和修改均使用项目根目录下的正式文件。

## 当前小节要理解的结构

| 位置 | 作用 |
| --- | --- |
| `src/agents/` | Agent 实现与注册。以后售后任务的决策、状态和执行流程主要在这里组织。 |
| `src/service/` | FastAPI 接口，接收请求并调用 Agent。 |
| `src/client/` | 把访问后端接口的代码封装为客户端，供界面等调用。 |
| `src/streamlit_app.py` | 用户交互页面。 |
| `src/core/` | 模型接入与项目配置。 |
| `src/memory/` | 对话与执行状态持久化的接入位置，后续学习检查点与恢复时再细读。 |
| `src/schema/` | 请求、响应等数据结构。 |
| `tests/` | 原框架测试。 |
| `pyproject.toml` | 项目基本信息、Python 版本要求、直接依赖范围及工具配置。 |
| `uv.lock` | 锁定解析后的具体依赖版本与来源等信息，支持复现安装环境。 |
| `.env.example` | 配置项示例，后续据此准备本地配置。 |
| `.venv/` | 已创建的项目专用 Python 环境。 |

对话请求的简化路径：页面 → 客户端 → FastAPI 接口 → Agent → 模型，再返回结果。后续加入工具后，Agent 执行过程中还会访问业务工具。

例如 `fastapi ~=0.141.0` 是直接依赖的版本约束；`uv.lock` 则记录本次解析选择的具体版本，还包括间接依赖。同步依赖时需要理解这两个文件的分工。

## 当前验证边界

已完成源码完整性、Git 仓库位置和分支、虚拟环境版本的检查。用户随后安装了框架锁定的依赖；助手核验 248 个包已安装，核心包可导入，离线同步检查无需更改环境。用户已成功调用 DeepSeek，并启动 FastAPI；助手直连 `/health` 得到 200 与正常状态。用户已通过 `POST /chatbot/invoke` 获得 DeepSeek 回复，普通 HTTP 对话链路已验证。尚未运行框架测试，售后业务仍未实现。

当前相对原框架的变更：`src/run_service.py` 将开发模式的监听范围限定为 `src`；依赖新增 `asyncmy==0.2.15` 与 `langgraph-checkpoint-mysql[asyncmy]==3.0.0`，原有包版本保留；增加 MySQL 连接检查、Workbench 初始化 SQL、状态图与审批学习脚本，以及配置可选的服务 MySQL 检查点后端。

用户已在 Workbench 检查库名无占用，随后创建并验证项目二独立业务库 `aftersales_agent`（`utf8mb4 / utf8mb4_0900_ai_ci`）和检查点库 `aftersales_agent_checkpoints`（`utf8mb4 / utf8mb4_bin`）。已有账号及其他系统的库表保持原样。

用户已完成 `scripts/learn_state_graph.py` 的最小状态图练习，并理解按字段局部更新。助手准备了源于组件 3.0.0 的 Workbench 初始化文件 `scripts/init_mysql_checkpoints.sql`，离线核对来源和目标范围后交由用户执行；用户返回迁移记录 22 条、最高版本 21，与预期一致。

助手新增 `scripts/learn_mysql_checkpoint.py` 的连接与检查样板，核心 `run_demo` 由用户填写。用户反馈 write 与 read 两个独立进程均按预期输出，按用户实际操作反馈，本例 MySQL 检查点保存和读取通过；脚本在运行核心代码前检查四张表和迁移版本。助手核对了用户代码、Ruff 检查及一次性内存流程验证，未代连 MySQL 或重跑实际写入。用户另在 Workbench 查到同一 thread_id 下的三个不同 checkpoint_id，已明确任务推进时保持 thread_id 不变。

用户已在 `scripts/learn_mysql_interrupt.py` 完成暂停节点与恢复调用，提供两次独立进程的等待审批、恢复后 approved/True 和 NEXT 为空的输出；已理解首次暂停后 return 等待恢复执行，以及恢复时会从被中断节点开头重跑。按用户实际输出，最小模拟审批的 MySQL 暂停与恢复通过；助手没有代用户连接 MySQL 或提交审批结果。

助手已完成基础服务接入：`src/memory/mysql.py` 使用连接池并将 setup 限定为只读结构检查；`src/core/settings.py` 与 `src/memory/__init__.py` 支持选择 mysql；`.env.example` 增加连接参数说明。测试先行的 9 项新用例由失败转为通过，连同现有配置及生命周期用例共 34 项通过，Ruff 检查和格式检查通过。测试未连接真实 MySQL 或调用模型，也未代用户填写本地配置。

用户已在本地配置 mysql 并返回 `/health` 正常，已理解后端选择项的作用。助手只读确认配置为 mysql、目标检查点库正确、凭据已填写，未显示凭据具体值。用户随后在 Workbench 查询返回 `http-mysql-demo-001 | 2`，确认该 HTTP 会话已有两条 MySQL 检查点记录，并能说明为什么需要单独核验数据库保存。按随后重启服务并读取历史的操作反馈，用户通过 `POST /chatbot/history` 和同一 thread_id 取回包含 DEMO-003 的用户消息及模型回复 ACK，能解释历史来自 MySQL 保存的状态；本节保存与重启后历史读取通过。该历史接口不调用 DeepSeek，跨会话长期 Store 仍为内存。用户粘贴的 curl 错误未在本机回显测试中复现，具体原因未确认。

用户检查业务库为空后，亲手执行 `scripts/create_orders.sql`，返回 orders 表的七个预期字段，并理解金额使用 DECIMAL 的原因。用户随后执行 `scripts/seed_demo_orders.sql`，返回三条不同状态、两位模拟客户的预期订单，能解释 learner-001 不能查看归属 learner-002 的 DEMO-003。

用户已完成 `scripts/learn_order_query.py` 中的参数化查询，实际 MySQL 输出确认本人订单可读、其他客户订单不可读，并理解 AND 同时匹配订单号与客户归属。助手读取并核对代码，将这段已掌握的查询整理到 `src/business/orders.py`；8 项内存 SQLite 回归测试先因缺少模块失败，新增模块后通过，Ruff 与帮助检查通过。测试没有连接真实 MySQL；业务函数的客户身份须由可信调用方提供，不代表已实现服务端认证。

用户执行 `scripts/create_shipments.sql`，返回物流表八个预期字段，并理解 order_id 外键要求关联订单已存在。用户随后执行 `scripts/seed_demo_shipments.sql`，返回四条预期模拟物流，覆盖一单多包裹、已签收、物流异常及另一客户的待揽收记录；签收时间与原订单一致，并能解释为什么需要返回列表。

助手新增 `scripts/learn_shipment_query.py` 的连接和列表显示样板，用户已完成 JOIN 查询，实际 MySQL 返回 learner-001 查询 DEMO-001 的两条物流，以及查询 DEMO-003 的空列表。结合数据对照与追问，用户能区分满足客户归属但不满足指定订单的情况，理解检查通过。助手将查询整理到 `src/business/shipments.py`；10 项新用例先因缺少模块失败，新增模块后连同订单查询共 18 项内存 SQLite 测试通过，Ruff 与帮助检查通过。未代用户连接真实 MySQL；调用方仍须提供可信客户身份。

用户执行 `scripts/create_cases.sql`，返回八个预期字段及默认 open、可空 resolution，并能解释登记工单后仍需处理。本节结构与状态理解检查通过。

用户已完成 `scripts/learn_case_create.py` 的事务核心，实际返回 CASE-DEMO-001/DEMO-001 的提交后回读结果，状态 open、resolution=null，时间 2026-10-19 16:44:58。助手核对事务范围正确；用户能判断异常传出时回滚，新工单不会保留，首次创建及概念理解检查通过。

助手增加默认关闭的 --simulate-failure 开关，在本次 INSERT 后、事务提交前主动抛出模拟异常。两项临时 SQLite 文件/独立连接回归先为 1 通过/1 失败，扩展脚本后均通过，Ruff 与帮助检查通过。用户反馈 CASE-ROLLBACK-001 的模拟异常输出及 Workbench 查询均符合预期，原 CASE-DEMO-001 保留；按用户反馈，真实 MySQL 回滚实验通过，已补充解释事务范围。

用户反馈以 learner-001 为 DEMO-003 创建 CASE-DENIED-001 的结果符合预期：ORDER_NOT_FOUND、数据库计数 0。经解释与追问，能判断 root 保持不变、模拟客户改为 learner-002 后，订单归属匹配不会触发该异常；实际拒绝写入及本节理解检查通过，真实认证仍未实现。

用户反馈重复创建原 CASE-DEMO-001 被拦截、旧记录保持，并能判断换新 case_id 不会触发该主键冲突。本节相同编号防重与边界理解检查通过，完整业务幂等仍未实现。

用户已在 `scripts/learn_order_tool.py` 完成 build_order_tool，反馈手动本人/他人订单结果与结构符合预期。助手读取实际定义，确认只暴露 order_id，客户身份由外层函数固定；用户已理解模型不能通过工具参数改变 customer_id。当前 langchain-core 1.4.9 与 uv.lock 一致。助手新增 --ask-model 分支与延迟加载模型的样板，ask_model_to_choose_tool 的模型绑定和调用核心留给用户。本次帮助、Ruff、订单查询 8 项测试、实际 Tool/SQLite 与本地 ChatOpenAI 绑定检查通过，未连接真实 MySQL或请求模型。当前等待用户验证 DeepSeek 返回的工具请求，并区分模型提出请求与程序执行查询；完整循环、完整业务幂等、认证、业务审批及 MCP 接入尚未实现。

用户随后完成模型绑定核心，首次 --ask-model 返回 APIConnectionError，尚未收到工具请求。助手核对代码正确；无认证公开连通性探测在当前默认代理及直连路径均收到 401，未复现连接失败。已仅补充脱敏异常链输出，检查通过，等待用户重试获取真实原因或工具请求。未代用户发送模型推理请求，未更改代理、模型配置或数据库设置。

用户重试后成功收到 DeepSeek 返回的 get_order(DEMO-001) 调用请求，并理解真正访问 MySQL 的是 Python，当前查询尚未执行；本节通过，原网络故障根因仍未知。助手新增 --execute-tool 路由和消息类型导入，执行核心 execute_model_tool_requests 留给用户；Ruff、帮助、参数约束及真实 Tool/SQLite 的离线指导验证通过。当前等待用户执行模型返回的只读请求，检查 ToolMessage 与调用编号对应，再理解结果尚未回传给模型。助手未代调用 DeepSeek、连接真实 MySQL 或填写核心。

用户完成执行函数，返回本人 DEMO-001 的真实 found=true / 199.00 / shipped 查询结果，并理解还需第二次模型调用交付结果；本次未贴请求/结果编号，下一次查看完整输出。助手新增 --answer 的回传路由、保存原问题、收集工具结果与编号完整性检查，answer_from_tool_results 核心留给用户。Ruff、帮助、开关约束与离线真实 Tool/SQLite/伪造模型消息及序列化检查通过；等待用户实测最终回答与解释两次模型调用。未代调用 DeepSeek、连接真实 MySQL 或实现通用 Agent 循环/MCP。

用户随后完成回传函数并提供完整成功输出：get_order(DEMO-001) 的请求与结果编号一致，最终回答使用实际查询出的 199.00 / shipped 等字段；能说明第一次模型调用选工具、第二次结合结果回答。一次只读工具闭环已实测通过。助手核对函数、整理空行并通过 Ruff，补充 null 送达时间只表示未记录。当前让用户沿用完整流程，以 learner-001 查询 DEMO-003 验证归属拦截及未找到时的回答；未改模型行为、凭据或数据库配置，未实现通用循环或 MCP。

用户完整闭环查询 DEMO-003 返回 found=false/order=null，请求与结果编号一致，并理解订单不属于当前客户；实际数据隔离通过。模型却断言订单库中没有此记录，回答准确性尚未通过。助手仅在工具描述和当前问题提示中明确“未找到当前客户可查询的订单”的语义，避免从合并结果推断数据库全局是否存在或实际归属；Ruff/格式/帮助通过，待用户再次实测。没有改 SQL、身份或返回结构，没有把提示词当作权限控制，也没有代调用模型或访问 MySQL。

用户随后反馈回答已更正（未贴全文），按实测反馈通过该检查，并请求正式集成。新增 src/aftersales/ 的正式工具和图节点、scripts/run_aftersales_agent.py 薄启动器；src/agents/ 后续负责框架适配注册，当前未修改原注册表。助手整理已经学过的工具和节点，route_after_model 分支及 StateGraph 组装留给用户。六项工具测试先失败后通过，Ruff/格式/帮助与真实节点/SQLite/假模型的离线图指导检查通过；没有替用户填写两个新核心、调用 DeepSeek 或连接 MySQL。当前等待正式 LangGraph 的实际输出与路由理解，再进入 MCP；售后图当前不带检查点，服务注册、认证与业务审批仍待集成。

用户已完成图并返回 model→tools→model、匹配的调用编号与正确订单回答，能解释根据有无工具请求决定分支；本节通过。下一步使用现有 mcp 1.29.0 的 stdio 服务。新增 mcp_server.py 与 check_aftersales_mcp.py 基础样板，工具注册和远程调用核心仍由用户填写。四项 SQLite 辅助测试从失败转通过，Ruff/帮助及实际 MCP 内存协议指导检查通过；修正 SDK 取消任务时的连接释放。尚未验证真实 stdio/MySQL 或接入图，也未调用模型、改账号或依赖。当前等待用户发现并调用 MCP 订单工具。

用户已完成 MCP 两处核心，实际 stdio 输出 get_order / 仅 order_id / isError=False / 本人 DEMO-001 数据，理解服务端执行查询；本节通过。用户要求复用 .env 中 MYSQL_PASSWORD，助手新增 scripts/_project_config.py 并接入九个脚本，已配置时不再询问，偏好记入 AGENTS.md。九项新配置测试和两项原事务用例共 11 项、Ruff/格式及九个帮助导入检查通过；真实本地密码读取仅核对成功布尔值，无回显或 .env 修改。当前等待 MCP 其他客户订单检查，再适配到图；未代访问 MySQL、调用模型或修改账户。

用户 MCP 查询 DEMO-003 实际返回 isError=False / found=false / order=null，归属验证通过。新增正式 mcp_client.py 共享会话，并为启动器添加 --tool-transport mcp；关键适配函数 load_order_mcp_tools 留给用户。图本身保持原实现，MCP 模式仅服务端访问数据库，整个图运行位于有效会话中。三项配置测试先失败后通过，连同查询/密码共 16 项、Ruff/帮助与实际内存 MCP/适配器/图的离线检查通过；源文件新核心未代填、未调用真实模型或 MySQL。当前等待 LangGraph＋MCP 组合链路的用户实测与会话理解。

用户完成 load_order_mcp_tools，真实组合输出为 MCP 发现 get_order、model→tools→model、匹配的调用编号 call_00_KukyYLMAJPPP8LOGyTDT9118 以及正确的 DEMO-001 / 199.00 / shipped 回答。助手读取实际核心与指导一致，仅清理注释及空行。用户经纠正后理解会话保活用于客户端工具代理通信，messages 负责模型上下文，两者不同；本节通过。当前仅有内存状态，售后图尚未接检查点。下一步由用户验证组合链路中 DEMO-003 的不可用结果与最终回答，再进入持久化。

用户随后返回组合链路中 DEMO-003 的完整输出：编号 call_00_UZzv7uSlDidjiUCx0EDb3695 对应，工具 found=false/order=null，回答正确限定当前客户范围；实测通过。进入正式图持久化小节，助手新增 checkpoints.py 的受限客户/任务配置与复用原只读校验的连接上下文，为启动器加入可选 --thread-id、同步检查点和回读展示；图的 compile 接入核心留给用户。十项新测试先失败，完成基础接线后与原后端/密码共 28 项通过，Ruff/帮助及实际图/SQLite、MCP 内存协议/适配器的离线检查通过。真实 MySQL 保存、独立进程读历史仍待用户操作；未修改账户、业务库或模型配置，也未实现下一节读取入口。

用户完成 compile 接入并返回真实订单查询、匹配的 call_00_hMToaqDdCCF7DPQyTPkK7807、四条存档消息及无后续节点；源文件已核对，真实写入与同进程回读按用户输出通过。用户理解任务编号隔离，已补充同一任务续接沿用原编号。其输出未贴任务键，因此下一步同时核对具体存档编号。助手新增互斥的 --history 参数和只读消息函数，读取时不调用模型、MCP 或业务库；七项新测试先 4 失败/3 通过，完成后与原检查点用例共 17 项通过，Ruff/帮助、空存档及旧命令兼容检查通过。当前等待用户在独立 Python 进程读取真实 MySQL 旧消息及解释结果；没有代跑真实数据库或模型，也未推进审批、认证或写工具。

用户随后返回准确任务键 aftersales:learner-001:mcp-aftersales-001、found=True/四条旧消息及原请求/结果编号，解释从保存状态读取历史；独立进程读取和理解检查通过。进入已学机制的重复物流接线，新增本地/MCP get_shipments 工具，复用既有客户归属 SQL，更新图的工具说明并保留用户核心循环。十三项新用例先因缺失封装/注册失败，完成后与相关订单/物流回归共 33 项通过；Ruff、格式及帮助通过。已核对协议、适配器与模型参数均只公开 order_id；SDK 已知警告仍出现但调用通过。等待用户实测两工具可用时的物流选择和逐包裹回答，暂未进入写工具或审批；未连接真实 MySQL、调用模型或修改已有系统。

用户返回 mcp-logistics-001 的真实两工具结果与按包裹区分的回答，SHIP-001 运输中、SHIP-002 收件信息待核实而暂缓派送，两个请求/结果编号匹配，保存五条消息且无后续节点；理解订单已发货不足以判断包裹异常，本节通过。解释两次模型调用、两个工具依次执行及五条消息的对应关系。当前沿用现有代码，由用户以 learner-001 查询 DEMO-003 的物流，验证实际空列表和回答语义；本次仅记录与复核，没有改生产代码或代调用 MySQL/模型。

用户随后返回 mcp-logistics-denied-001 的实际 get_shipments(DEMO-003) 空列表，编号 call_00_spM6fRnvoDHZ8ID61Vhf1410 对应，回答正确限定可见范围，保存四条消息且 NEXT=[]；物流不可用订单验证通过。下一小节先讲解创建工单的业务幂等，结合原脚本的主键防重边界及业务提交后、检查点记录前中断的例子，确认稳定 request_id 与复用原 case_id 的理解，再安排用户设计/操作。当前仅核对及记录，未新增写工具、修改表或代执行模型/数据库请求。

2026-10-19 用户明确在业务幂等概念小节结束今日学习，并说明这一步尚未完成。该理解检查保持未通过，未开始新的幂等实现；下次从稳定请求编号、业务重试与原工单复用的例子继续，不能默认已懂而直接建表/接写工具。当天已达到的正式功能是具备两个 MCP 读工具、客户数据隔离和 MySQL 持久化/跨进程历史读取的售后 Agent；创建工单、事务回滚和中断恢复已有独立学习练习，但尚未整合为正式售后操作闭环。

2026-10-20 用户继续学习，明确用 request_id 识别同一次申请并复用原工单；稳定编号的基础判断通过。现继续同一客户/同编号下的申请内容一致性，用 DEMO-001 改为 DEMO-002 的例子判断正常重试与请求冲突。该理解检查仍待用户回答，完整业务幂等尚未实现；本次仅复核、讲解与记录，没有新增表结构/写工具或执行真实数据库、模型请求。

用户随后正确判断相同请求号但订单变化应报请求冲突，一致性概念通过。助手准备新表 case_creation_requests 的两阶段 Workbench SQL：客户/请求联合主键、原始申请 JSON、唯一并外键关联的 case_id 及创建时间；共五列，原工单表保留。官方文档及现有表类型/字符集已离线核对；未执行真实 DDL、改账号或生产 Python。当前等待用户核对表名、亲手创建并返回列/索引及联合主键理解，再进入事务核心。

用户提供实际索引三行，确认 (customer_id, request_id) 联合主键及 case_id 唯一索引，并解释区分客户请求；这两项通过。尚未贴字段或完整建表结构，因此下一步只读 SHOW CREATE TABLE 补齐 JSON/FK/列/引擎证据，再进入同事务写入练习。助手未代连数据库、重复建表或提前填写事务核心。

用户随后提供的 SHOW CREATE TABLE 被显示截断。助手在已完成用户建表/联合键实践后，用配置中的密码和原账号进行只读元数据核对，确认五列、JSON 对象约束、联合主键、唯一 case_id、RESTRICT 外键、InnoDB/utf8mb4_bin 和父列类型一致；结构检查通过，无需再次复制或建表。本次确实连接了真实 MySQL，但仅执行 SHOW CREATE / INFORMATION_SCHEMA 查询，没有更改账号、服务、配置、结构或业务数据。

新增 business.cases 和 create_case_with_request.py 的参数/查询/连接/回读样板，沿用固定项目密码读取约定。用户核心 create_case_with_request 仍未填写，本节只要求将归属验证和两条 INSERT 放入同一个 engine.begin 事务，退出提交后从新连接回读。15 项新辅助测试先失败后通过，与密码回归共 24 项通过，Ruff/格式/帮助通过；指导代码在独立 AST 命名空间和临时 SQLite 文件中验证成对提交、越权拒绝、重复请求键导致的新工单回滚及旧记录保留，CLI 回读输出通过。没有代填核心、执行真实业务写入或请求模型。等待用户首次运行 REQ-TXN-001 并解释回滚后，再进入申请复用/冲突；尚未实现完整业务幂等。

用户亲手完成双 INSERT 事务并返回真实成对回读：REQ-TXN-001 / learner-001 对应 DEMO-001 和工单 9209ae20d4b543a9acc4ad7e7f78a9c4，状态 open，创建时间 2026-10-20 09:17:54。用户理解第二条插入失败时新工单应回滚，实际核心也已核对为同一 engine.begin 连接；首次提交与概念通过。助手准备仅含两条 SELECT 的 check_case_request_rollback.sql，供用户重复同一申请前后对比工单数、申请数和原映射；下次先检查真实 1062 回滚及旧记录保留，再接入申请复用。没有修改用户事务核心、已有 MySQL 配置或代执行真实业务写入。

本轮用户实际事务函数的临时 SQLite 文件/外键/新连接检查通过，覆盖正常提交、第二条 INSERT 请求键冲突后整笔新事务回滚、旧记录保留及越权拒绝；Workbench 两条对比 SELECT 亦已离线运行通过，15 项辅助测试通过。只整理源码格式，未改变事务逻辑。本轮未连接真实 MySQL 或调用模型，真实回滚实验仍待用户执行。

用户随后反馈重复实验前后均为 2/1，原 REQ-TXN-001 工单编号、状态和时间不变，并解释已提交记录不会撤销；结果保留与事务边界理解通过，未贴的驱动报错码不记为已观察。进入顺序重试复用小节：助手新增冲突错误类型、已存在申请查询及 CLI 特定冲突提示，成功标记采用 CASE_REQUEST_READY。JSON 内容比较、冲突拒绝和返回原 case_id 留给用户，原双 INSERT 核心保留。新增 CLI 测试先失败后通过，与相关辅助/密码回归共 25 项；临时 SQLite/AST 指导验证匹配复用、不同内容拒绝、客户范围、新申请和原记录保持通过。没有代填新核心、访问真实 MySQL 或调用模型，尚未处理并发首建碰撞。当前等待用户填写分支及真实同内容重试，再进入变化内容验证。

用户亲手完成复用分支，返回 CASE_REQUEST_READY、相同的 9209ae20d4b543a9acc4ad7e7f78a9c4 和原 2026-10-20 09:17:54 创建时间，能解释 return 阻止继续执行创建；实际源码的内容比较、冲突异常与提前返回已核对，匹配重试通过。助手仅清理注释和格式，下一步让用户保留原请求号而将订单改为同客户的 DEMO-002，验证 CASE_REQUEST_CONFLICT，避免与客户归属拒绝混淆。真实变更内容验证及并发恢复仍待后续，不代执行真实数据库/模型请求。

本轮实际用户函数的临时 SQLite 文件/外键/独立连接验证通过：首次创建、相同内容复用、订单或描述变更冲突、原记录和数量保持。相关辅助/CLI 共 16 项测试及 Ruff/格式检查通过；没有连接真实 MySQL 或调用模型。

用户实际返回更换订单后的 CASE_REQUEST_CONFLICT，随后经追问能判断同编号/同订单而描述变化也必须报冲突、不能覆盖原申请；真实订单变化拒绝及内容一致性理解通过。描述变化仅作概念检查，未声明已实测。下一节先讲解两次首次请求都查不到记录的并发场景，等待用户判断联合主键冲突及后者事务回滚，再设计恢复核心；当前只记进度，没有修改业务逻辑或执行数据库/模型请求。

经纠正 B 可提前返回、泛指再次请求的理解，用户能说明 B 候选工单未保存，不能直接返回。准备回滚后新事务查询的代表性练习：提取已完成的比较逻辑，按申请 INSERT 阶段及数值 1062 限定恢复，其他错误保持失败，找不到胜出记录则继续抛出原错误；recover_case_request 核心留给用户。新增受控 A/B 首次请求诊断脚本，拒绝旧编号并核对 1062、同一返回 ID、工单增量 1/申请数 1。11 项新测试先失败后通过，与相关回归共 36 项；Ruff/格式/帮助及 AST/临时 SQLite 注入错误回滚检查、伪造适配器脚本门控检查通过。上述不是实际 MySQL 并发实验，尚未代填用户核心、连接真实 MySQL 或调用模型。当前等待用户填写三行恢复事务并执行 REQ-RACE-001。

用户随后完成恢复核心，提供完整受控 MySQL 碰撞输出：两次初查为空、1062、B 恢复读到记录、A/B 都返回 455465516ad54ab18fc382c67177e962、工单增量 1、申请数 1、RACE_OK；助手读取源码确认三行新事务回读正确。经纠正将 1062 称为占位异常的说法，用户明确其为 MySQL 层的申请编号重复；本场景实测及理解检查通过。当前仅更新记录，未改生产代码或代跑真实写入。下一步正式 Agent 写入先讲清后端保存稳定 request_id 与模型仅填写业务内容的边界，再接 MCP/审批；不提前把可执行写工具接入当前无审批的只读图。

用户经澄清后能判断同订单同内容而改换 request_id 会创建新工单，稳定编号边界通过。MCP 新增可选固定申请上下文与 create_case 业务包装，默认读会话仍仅两个工具，现有 Agent 不传写上下文。手动 check_case_mcp.py 通过子进程环境传客户/申请，工具 Schema 只公开订单和描述；注册体调用语句留给用户。新增测试初始 10 失败/1 通过，样板后与相关回归共 31 项通过；Ruff/格式/帮助及 AST 指导的实际内存 MCP/临时 SQLite 创建复用、跨会话保持、冲突/归属和参数隐藏验证通过。原 SDK 警告保留，未修改依赖、已有账号或配置，未代填新核心或调用真实 MySQL/模型。当前等待首次手动 MCP 写入及上下文来源解释，再验证复用并接人工确认。

用户完成注册体并返回真实三工具发现、仅订单/描述的 Schema、isError=False 和 case_ready=true：REQ-MCP-001 对应 88606384960548f58204f7a339fb704b，open，创建于 2026-10-20 10:08:08。源码已核对。经说明命令行到子进程环境、服务上下文和函数参数的链路，用户正确指出隐藏编号启动时确定；首次 MCP 写入与本节理解通过。当前仅记录进度，不代跑真实数据库或模型。下一步原样重跑客户端，验证新服务进程仍返回相同编号/原时间，再验证内容冲突。

用户随后提供另一进程的相同 MCP 成功结果：原工单编号、10:08:08 时间、内容和 open 状态均不变，并说明从 MySQL 找到原工单；跨进程复用与理解通过。下一步保持 REQ-MCP-001、将订单改为 DEMO-002，验证 MCP 错误结果中传回业务冲突并区分连接失败。助手本轮仅复核接线和记录，没有改代码或调用真实数据库/模型。

用户实际返回 MCP_IS_ERROR=True、CASE_REQUEST_CONFLICT 和诊断客户端主动抛出的 RuntimeError，并正确区分业务拒绝与连接失败；MCP 手动写入小节全部检查通过。下一步先理解正式图的写前人工确认：模型提出请求、保存稳定申请内容、interrupt 暂停，同意后才执行 MCP 工具，拒绝跳过。当前仅复核原图/中断练习和更新记录，等待用户判断“先写入后确认”的问题；尚未改图、开放 Agent 写工具或运行真实模型/数据库。

经区分审批状态与已提交工单，用户正确判断写前确认被拒绝后不应新增工单。准备正式图的申请暂停小节：新增 approvals.py 保存 UUID/客户/ToolCall/订单/描述，图 opt-in 分支先 prepare_case 再 case_approval；新 --request-approval 要求 MCP 和全新持久化任务。此阶段只有本地惰性申请 Tool，MCP 仍为两个读工具，无批准/拒绝命令与真实写入执行边。用户负责 interrupt 核心，源文件尚未填写。14 项新测试及相关回归共 44 项通过，Ruff/格式/帮助和 AST 指导的实际图/内存 MCP/SQLite 暂停、待确认存档及零业务写入验证通过；无真实模型/MySQL 请求。当前等待用户用 mcp-case-approval-001 提供实际暂停输出并理解存档与业务工单的区别。

用户随后亲手完成 interrupt 核心，实际输出 mcp-case-approval-001 / 93602820c9624d1595c87d2c18be4158、waiting_approval、消息数 2 和 NEXT=[case_approval]，并判断为等待确认的流程状态；源码已核对正确。助手补充 --approval-status：仅从客户/任务作用域检查点读取 pending_case 和 approval_decision，在模型/MCP/业务连接之前返回；另提供 check_pending_case_request.sql 只读核对该申请在业务映射表的数量。新增 7 项测试先观察到缺函数、待确认保护分支和不识别 CLI 的预期失败，再实现；与历史/检查点共 24 项通过，Ruff/格式/帮助通过，真实用户中断函数在临时 SQLite 保存及连接重开测试中通过。当前等待用户独立进程回读原编号、Workbench 计数 0，以及重启后编号为何不变的解释。本轮未访问真实 MySQL 或调用模型，批准/拒绝恢复和正式 MCP 写入边仍未实现。

用户已返回独立进程的实际 MySQL 状态：CHECKPOINT_FOUND=True，原申请 93602820c9624d1595c87d2c18be4158 / ToolCall / 订单 / 描述保持，waiting_approval 和 decision=null；Workbench 精确申请计数为 0。持久化与业务未创建验证通过。用户表示恢复 request_id 的任务，需进一步明确 thread_id 定位流程、request_id 标识业务申请并防止重试重复创建，两者不可混淆；当前先核对该区别，再进入恢复核心。本轮仅核对源码和记录结果，没有改功能、连接真实数据库或调用模型。

用户随后正确回答必须使用 thread_id 定位 LangGraph 检查点，区分通过。按既定小节继续准备拒绝恢复：正式图新增 False→reject_case→END，申请原字段/编号保持、审批状态改 rejected，decision=False，给原 AI 批次每个未执行 ToolCall 补一个拒绝/取消 ToolMessage；不调用工具或模型。新 recovery.py 用不可调用模型与惰性工具重建图，先核对实际中断、客户、原申请内容及未决状态，结束后核对拒绝结果；核心 resume_case_rejection 的 Command 调用仍为用户占位。启动器 --reject-case 要求原 thread，在模型/MCP/业务连接前处理并返回。16 项新测试最初 12 失败/4 通过；样板实现后与相关回归共 67 项通过，Ruff/格式/帮助通过，保留原 SDK 警告。测试用明确的临时参考调用验证旧图检查点跨连接恢复、相同编号、批次 ToolMessage 匹配、已结束/无任务/客户不符/无中断/内容变化拒绝及 CLI 无外部调用；未完成的真实核心不宣称已验证。当前等待用户填写恢复调用并执行原任务拒绝、Workbench 计数 0，解释 Command 如何把 False 传回 interrupt；正式批准执行仍未开放，无真实 MySQL/模型请求。

用户已填写恢复调用，实际拒绝结果保持原请求 93602820c9624d1595c87d2c18be4158 和申请内容，状态 rejected、decision=False、消息数 3、NEXT=[]。用户随后确认 Workbench 计数仍为 0，解释人工已拒绝，程序直接走拒绝分支，无需模型再判断；该小节结果和理解通过。源码核对正确，助手仅格式整理，移除测试里的参考恢复替换，16 项拒绝测试使用用户真实核心重新通过，Ruff/格式通过。下一步先由用户用全新 mcp-case-approval-002 保存申请，随后接原 request_id 的批准 MCP 创建；本轮未实现批准命令、执行节点或代跑真实数据库/模型。

用户已创建 mcp-case-approval-002 新待确认任务：request_id=8f567faa0d524e0d86abdf9277d86bfc，tool_call_id=call_00_7f09YAFzu6mpjFLIXZ5b1957，DEMO-001，实际保存描述为“收件信息待核实，派送暂缓”（无句末句号），状态 waiting_approval、消息数 2、NEXT=[case_approval]。用户要求暂缓功能推进，先解释中文输入给 Agent 还是 DeepSeek，以及对应源码。助手本轮仅追踪 CLI/HumanMessage、模型 bind_tools/ainvoke、ToolCall 路由、后端 UUID 和 interrupt 的实际代码；批准分支未实现，也未调用真实模型/数据库。后续沿用该待确认任务和原申请内容，不重新创建。

用户明确理解并继续后，准备批准 MCP 执行小节。正式图扩展 True→execute_case→END，要求显式 executor；只读/申请/拒绝图没有实际写回调。新增 execution.py 校验明确批准、既有且匹配的 pending_case，以存档客户/request_id 启动 MCP，会话参数仍只向工具公开订单/描述；invoke_approved_case 调用核心留给用户。返回 JSON 必须匹配客户、请求、原始内容及实际工单字段，才记 created、case_result 与匹配 ToolMessage；错误不伪报成功。恢复模块和 --approve-case 支持初次 Command(resume=True)、已批准失败步骤 ainvoke(None) 使用原编号重试、已结束结果直接回读；本步不调用模型。准备 Workbench check_approved_case_request.sql 两条 SELECT。新增 30 项初测 26 失败/4 通过，另补缺失存档申请的回归先失败，补充 MCP 前拒绝；共 31 项新测试与完整售后回归 122 项通过，Ruff/格式/帮助通过，保留已知 SDK 警告。测试用明确的参考 MCP 核心、真实内存协议及临时 SQLite 验证旧图存档跨连接批准、匹配内容、业务提交后丢响应再重试仍仅一单、终态只读复用和保护分支；源文件新核心仍未代填，未访问真实 MySQL/模型。当前等待用户填写 invoke_approved_case，使用原 mcp-case-approval-002 批准并提供实际工单与 Workbench 一条映射结果，再检查存档 request_id 如何交给 MCP。

用户随后反馈 Workbench 精确申请计数为 1，回答从任务编号寻找 request_id。助手已读取实际源码，确认用户填写了正确的 session.call_tool(create_case, 原订单/描述) 并返回结果。需精确补充 thread_id→检查点 pending_case.request_id→后端 MCP 启动 case_request_id→业务函数的链路；业务记录已存在，但用户尚未贴批准命令 CASE_RESULT/实际工单编号及 CHECKPOINT_NEXT，不将完整检查点闭环记为已验证。当前等待补齐该输出，再做重复批准结果复用小节；本轮只核对源码和记录，无功能修改或真实数据库/模型调用。

用户要求继续后，助手自行以原账号/.env 配置连接真实 MySQL，只执行检查点读取和 aget_state，不运行图或调用 MCP/模型。实际存档确认 True / created / 消息数 3 / NEXT=[]，case_result 与原客户、申请和内容匹配；工单号 0e475797c7d5483ba46c9b07dfeeccfe，open，创建/更新时间及申请创建时间均为 2026-10-20 11:58:51。结合用户 Workbench 计数 1，批准创建和结果持久化验证通过。助手移除测试临时参考调用，仅改为直接验证用户实际 MCP 核心；31 项执行测试通过，Ruff/格式通过，保留既有 SDK 警告，生产代码仅格式整理。下一小步由用户重复相同 --approve-case 并重跑原 Workbench SELECT，核对相同 case_id/时间/计数 1，理解终态检查点直接回读与业务已提交但检查点未完成时的同 request_id 重试保护。未提前增加新功能或代跑实际批准/业务写入。

用户重复练习后反馈 Workbench 申请数仍为 1，未再次贴工单号/时间，不额外记为已观察。起初将保护归因于已有工单编号，经说明响应丢失时 Agent 可能尚无 case_id、需使用提前保存的 customer_id/request_id 和原始申请内容查业务映射，用户正确判断换新 request_id 会创建新工单，申请身份与幂等理解检查通过。下一小节准备把真实工单结果交给 DeepSeek 生成最终用户回复，先讲明业务提交与回复生成的阶段边界，等待用户判断模型回复失败应重建工单还是只重试回复，再实施。本轮仅核对原规划/源码、更新教学记录，无功能修改或真实模型/数据库调用。

用户正确判断写入已提交而 DeepSeek 回复失败时应只重试回复，但理由侧重 request_id 防重；助手明确检查点使执行停在回复节点，业务幂等是写入重试的额外保护。按已确认方向准备 replies.py：校验 created/True、客户/原始申请与匹配成功 ToolMessage，提供系统规则和消息历史，惰性普通模型（不绑定工具），检查 AI 文本无 ToolCalls 且包含真实订单/工单编号，将 AIMessage 和 case_reply 一起保存；这些检查不宣称保证所有自然语言事实。invoke_case_reply 模型调用核心留给用户。正式图增加可选 execute_case→case_reply→END，默认原模式保留；新 --reply-case 对旧已完成任务用同一验证结果的 aupdate_state(as_node=execute_case) 仅接新回复节点，失败后 ainvoke(None) 只恢复回复，已保存回复直接读回。22 项新测试最初 18 失败/4 通过，样板后与执行/拒绝回归 69 项通过，完整售后回归 144 项通过，Ruff/格式/帮助通过，保留已有 SDK 警告。验证用假模型/临时 SQLite 及明确的参考核心，覆盖跨连接追加第四条 AI、业务字段保持、失败后只恢复 case_reply、终态免模型复用及校验/CLI 保护；源文件核心未代填，本轮无真实模型/MCP/MySQL 请求。当前等待用户填写一行调用并对原 mcp-case-approval-002 执行 --reply-case，解释新增消息的来源与依据，再核对真实回复存档。

用户填写最终回复核心，实际返回 CASE_REPLY_SAVED=True、DeepSeek 中文回复包含 DEMO-001 和原工单 0e475797c7d5483ba46c9b07dfeeccfe、open/待处理、原申请 8f567faa0d524e0d86abdf9277d86bfc，消息数 4、NEXT=[]；能说明由 DeepSeek 根据原工单结果生成，实际输出和理解通过。源码核对后移除测试参考，22 项回复测试直接使用用户核心通过，Ruff/格式通过，生产代码仅格式整理。下一步核对原框架 FastAPI：服务生命周期已持有共享 saver，router 有既有 bearer 依赖，可新增 GET /aftersales/tasks/{thread_id} 只读状态投影；本地学习由服务端固定模拟客户 learner-001，后续替换登录身份，不信任前端 customer_id。当前先检查用户理解客户端任填客户编号的越权问题，尚未新增 HTTP 接口、改服务/配置或请求真实模型/数据库。

经澄清前端任填 customer_id 会造成越权而非仅查不到数据后，用户回答不应允许查看他人任务、属于越界，接口身份边界理解通过。按前述设计准备只读 HTTP 小节：新增 aftersales/task_state.py，读取用图禁止执行模型/回复节点且无业务 executor，read_task_snapshot 的 aget_state 核心留给用户；新增 service/aftersales.py 的 GET /aftersales/tasks/{thread_id}，服务端固定本地模拟客户 learner-001，依赖共享 saver，响应为原 pending/decision/result/final_answer/消息数/next_nodes。框架 lifespan 只在存活期间暴露已有 saver，路由挂到原 verify_bearer 下，不更改认证配置或 MySQL 参数。11 项新接口测试和改进生命周期测试先 12 项失败，实现样板后与认证 15 项通过，连通用服务和完整售后回归 172 项通过，Ruff/格式/OpenAPI 通过，保留既有框架/SDK 提示。测试用临时 SQLite、ASGI 和核心参考，覆盖四条消息/待审批只读、范围隔离/客户端身份无效、404/422/503、错误脱敏和 Bearer 继承；源核心未代填，本轮未启动真实服务或访问真实 MySQL/模型/MCP。当前等待用户填写一行 aget_state，重启项目 FastAPI 后在 Swagger 执行原 mcp-case-approval-002，返回 200/消息数 4/最终回复/next_nodes=[] 并解释为何无需调用模型。

用户已完成 GET 的实际 aget_state 核心，报告状态查询按预期返回，理解这是读存档、无新 Agent/DeepSeek 执行。助手移除 GET 测试参考并准备下一小节 POST /aftersales/tasks/{thread_id}/messages：输入严格只收 message，身份由服务端模拟，复用共享 saver 和客户/thread 作用域；conversation.py 先检查未决/已处理申请或未完成节点，再在只读 MCP 会话内绑定模型和惰性 create_case 提议 Schema，执行到答案或人工中断。invoke_task_message 的 ainvoke 核心仍留给用户。消息入口不开放写工具、不处理聊天文本审批；同进程 HTTP 对同客户/任务串行，其他进程及 CLI 不在此锁覆盖内，学习时不得并行操作同任务。共享状态投影现在可返回只读 Agent 的最后回答，暂停/失败时不误返旧回答。模型配置抽为 CLI/HTTP 共享惰性函数。发现 Windows 启动 Selector 与 stdio 子进程不兼容，新增 MySQL 模式专用 Uvicorn Proactor 工厂；在 reload 开关两种配置下验证实际本地 Python 子进程通信，保留其他数据库模式原路径。14 项消息测试及 3 项循环测试先观察缺模块/功能失败，样板后连服务及全部售后回归 189 项通过，Ruff/格式通过，六项既有依赖警告保留。测试用假模型、临时 SQLite、进程内 MCP；POST 仅核心用测试参考，GET 真实核心直接验证；无实际模型/数据库请求或 MySQL 账号/配置/数据变化。下一步用户填写核心后在 Swagger 对新 http-case-approval-001 发消息，验证 waiting_approval、case_result=null、next_nodes=[case_approval] 并解释尚无业务工单，再进入 HTTP 审批小节。

HTTP 消息入口实测排错：用户正确填写核心，但报告错误并要求助手自主验证。检查存档发现原 http-case-approval-001 的输入是 Swagger 默认 string，失败停在 model 且保存 APIConnectionError；路由将执行异常映射503，未确认用户报告533，422原响应未知，不能猜具体字段。真实 DeepSeek/MCP/MySQL 经 ASGI 和完整服务生命周期+本地临时 Uvicorn HTTP 均验证200/waiting_approval，临时服务已关闭。原任务先只恢复失败模型节点，再追加正确中文申请，现 waiting_approval / NEXT=[case_approval] / case_result=null / 消息数7，request_id=669a796864bf448cb80b7a556b7ee02b。仅产生检查点，无业务工单；两个诊断待审批任务保留，未批准。增加 Swagger 明确 JSON 示例，防止默认 string；该测试先缺example失败再通过。移除 POST 核心测试替换，相关29项测试真实核心通过，Ruff/格式通过。旧8080健康检查仍超时，需重启项目Python进程后GET原任务，未宣称该运行进程已修复。未动MySQL账号/服务配置/原库表，尚不进入下一小节。

用户明确拒绝不会创建业务工单，HTTP消息入口理解检查通过，转入HTTP审批小节。助手准备严格布尔审批接口和decisions.py边界：客户与原申请字段取自服务端作用域检查点，复用approve_saved_case/reject_saved_case；同决定完成态复用结果，反向决定409，失败批准允许用原编号重试，不允许中途撤为拒绝；审批与消息共用同进程任务锁。仅批准未完任务惰性创建MCP executor，拒绝与完成重放无工具/模型调用。本节不自动生成最终回复。用户新核心resume_http_approval仍占位，留待亲手填写现有批准恢复调用；未代批原http-case-approval-001。17项初始测试先失败，样板后通过，再加OpenAPI契约项；服务及售后相关回归208项通过，Ruff/格式通过，保留6项既有依赖警告。测试使用真实恢复/执行组件、模拟MCP与业务记录及临时SQLite，核心用明确参考替换，覆盖正确与相反决定、重放、并发、模拟提交后响应失败再重试、严格输入/身份/鉴权，未请求真实MySQL/模型/工具或修改MySQL配置。等待用户填一行、重启刷新文档，对原任务执行approved=true并解释审批恢复为何沿用原申请；预期真实工单创建后case_result与存档保持原request_id=669a796864bf448cb80b7a556b7ee02b。

用户提供HTTP批准实际成功结果：原申请669a796864bf448cb80b7a556b7ee02b不变，工单62538c1ace92447b9ff5029efec2b183/open，created/updated/request时间2026-10-20 14:33:17，pending created、decision true、消息数8、NEXT=[]、无最终回复，并正确解释人工决策已确定、无需模型再判。助手仅格式整理真实核心并移除批准测试参考，18项直接核心测试通过。直接8080重放超时后，以同一ASGI处理器和真实池化MySQL saver重复批准原任务，返回200且完整状态相等；只读业务SQL核对前后映射与工单均1条，未新建业务记录。继续已规划的HTTP回复小节：新增http_replies.py范围/就绪校验和用户占位恢复调用，新增POST /aftersales/tasks/{thread_id}/reply无请求体，共用任务锁、身份与状态响应，复用既有reply_to_saved_case与responder；共享提取原CLI普通回复模型工厂，保留CLI同名包装及配置。仅表达已创建工单，不配置MCP执行器；重复直接返回存档，失败仅case_reply恢复。10项新测试先因缺模块/路由失败，样板后通过；服务及售后相关回归218项通过，Ruff/格式通过，6项既有依赖警告保留。测试用假模型/SQLite和明确的新核心参考，未代填核心、运行真实回复模型或修改MySQL账号/配置/表数据。等待用户填写一行reply_to_saved_case调用，重启刷新后在原任务生成第9条消息并解释失败重试边界，再进入后续小节。

用户完成HTTP回复核心并正确解释失败恢复只执行模型回复节点。助手去掉测试参考，10项接口测试用真实核心通过，代码仅格式/import整理。实际MySQL存档起初仍8条/无最终回复，助手在原已创建任务执行一次真实回复核验得到200，现9条、中文答复含DEMO-001与原工单62538c1ace92447b9ff5029efec2b183，原申请/业务结果不变、NEXT=[]；随后禁止模型初始化验证重复reply，返回200且完整状态与检查点ID不变，无新工单。结果/理解通过后，按已有规划组合审批与回复：新增workflow.py，仅协调已有submit_case_decision/submit_case_reply；批准后创建成功再回复，已创建或回复失败任务跳过创建，只恢复回复，完成态复用保存答案，拒绝仍不调用模型或工具。approval路由改为一次正常响应带case_result+final_answer，共用任务锁；旧/reply保留。错误返回不再暗示审批必然未生效，回复无效/连接失败返回503并可用同一批准重试。更新既有测试并加4项组合场景，先6失败/16通过，再审批回复32项全通过，服务/售后全相关回归222通过，Ruff/格式/OpenAPI通过，6项既有依赖警告保留，无HTTP核心参考替换。组合阶段只用假模型/MCP与SQLite，未代创建新申请；由用户以新http-case-flow-001执行messages→approval两次请求，核对同一申请的真实工单和中文回答一并返回，并解释503与业务已创建的区别，验证通过后再继续。MySQL账号、配置、库表均未调整。

用户报告重复Accept failed/WinError87并要求先修复整体运行问题、暂停学习。助手定位并隔离复现同一listener跨spawn子进程重新注册IOCP：child1成功、child2错误87，与Uvicorn reloader保持父socket再传给新Proactor子进程的源码路径吻合；此前仅stdio子进程测试遗漏了HTTP重载复用。修复启动器Windows/MySQL分支为原生asyncio/Proactor、单worker、关闭内置热重载并清空reload_dirs，显式workers=1覆盖WEB_CONCURRENCY；其他平台/后端保持原策略，移除service.loops旧工厂。后续改代码需人工重启Python服务。新运行回归先3失败2通过，再5项通过，覆盖真实独立TCP连接、真实MCP stdio握手ping、临时文件改动不换服务PID、同端口两轮完整重启；测试考虑Windows venv解释器redirector的PID差异并清理自己的进程树。实际项目服务另在临时49671端口用两个独立Proactor循环启动关闭：每轮11次health、原已完成任务GET、只读MCP get_order均200并干净释放MySQL资源，无真实模型或业务写入。服务/售后相关回归224项通过，Ruff/格式通过，保留6项既有SDK警告。依赖、.env、MySQL账号/配置/库表未改；现有8080旧服务需用户Ctrl+C完全退出后按原脚本启动才能采用新启动参数，未声称旧进程自动修复。原组合流程验收暂停，运行恢复后再继续。

用户确认原 http-case-flow-001 已提交与审批成功，并正确说明不能仅凭 HTTP 失败认定未创建，应查工单编号。助手只读检查真实 MySQL 检查点及业务表：request_id=676e2be5a34e405ab8e5c904ee4e8a1f，case_id=1c8f714aeef64d82b3cbabb3a1b87d79，DEMO-001/open，批准 true、pending created、7 条消息、最终答复已保存、NEXT=[]，对应申请/工单链接恰好 1 条。无模型请求、审批恢复或业务写入，不由历史任务成功推断当前 8080 旧进程状态。组合小节操作与理解通过；下一小步为现有能力的自然语言条件业务验收：新 http-business-flow-001 先查物流、发现异常再提出工单申请，先停在人工确认核对工具事实，不提前增加新功能。

用户明确委托助手完成自然语言验收并要求先盘点剩余任务、加快进度。助手实际通过 FastAPI ASGI 消息入口调用真实 DeepSeek/MCP/MySQL：http-business-flow-001 返回200，模型先请求 get_order 和 get_shipments，收到真实两个包裹信息后根据 SHIP-002 异常提出 create_case。申请159e876de289425098441f3e0ca31d9f，描述保留模拟标识、事件与时间；5消息、waiting_approval、decision/result=null、NEXT=[case_approval]。未提交审批，无业务工单创建；不是8080现有监听进程测试。原始脱敏轨迹存 output/verification/http-business-flow-001.json。只读对照原始Word规划与源码后，新建2026-10-20-remaining-milestones.md：完整项目剩余工单闭环、可信身份审计、模拟退款、库存补发、独立MCP、演示界面、故障与评测、部署交付8个模块。原物流建单核心后端MVP已跑通，尚未开展新增核心模块，不虚报整体百分比。基础重复内容后续合并处理，保留少量代表性核心实践。

2026-10-20 用户明确要求连续完成全部八模块直到部署交付，覆盖原逐节教学等待。助手以独立 aftersales_delivery 应用和业务库实现工单生命周期、签名认证/角色、不可变审批与审计、模拟退款、并发库存补发、真实独立HTTP MCP、LangGraph状态恢复、Streamlit、评测及Docker交付。最终新交付74项、旧售后及相关服务224项测试通过，Ruff/格式/文档检查通过。实际MySQL退款容量与库存竞争及4并发重放通过；Docker物流/退款/补发/拒绝、权限/版本、真实API容器重启恢复通过。容器checkpoint排序规则冲突修复仅针对新Docker实例，不修改原MySQL账号/密码/服务/业务库；本机原MySQL仍8.0.45/root@localhost/utf8mb4/utf8mb4_0900_ai_ci。最终Docker在8510/8090/8091运行，隔离MySQL不发布宿主3306。真实模型48固定案例×3最终129/144通过（89.6%），15失败和缺失计量均保留，运行中源码/锁文件哈希一致；评测后仅API排版并再测74项通过，评测原API副本哈希已核对保留。已观测审批前/越权/重复业务结果均0。5场景SQLite工程对照通过，真实LLM简单Agent基线未执行，未伪称模型100%或企业生产能力。186.4秒真实GUI录像已回放，9截图及脱敏报告保留。README_DELIVERY和docs/delivery/acceptance、architecture、learning-guide、interview、evaluation、demo给出运行与学习说明；交付压缩包排除配置秘密/随机密码/虚拟环境并验证完整性和敏感值扫描。未提交或推送代码。
