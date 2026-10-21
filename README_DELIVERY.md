# 电商售后 Agent：独立交付版

基于 LangGraph、MCP、FastAPI、Streamlit 和 MySQL，完成「客户提出售后需求 → 查询事实 → 保存候选动作 → 人工审批 → 受控执行 → 查询处理结果」流程。物流工单、退款和补发使用演示数据；退款与发货均为模拟，不连接支付渠道或真实仓储。

交付代码位于 `src/aftersales_delivery/`。原教学入口、原 `compose.yaml` 和已有业务数据继续保留。新业务库固定为 `aftersales_delivery`；本机 checkpoint 使用已有 `aftersales_agent_checkpoints`，交付任务采用独立前缀。Docker 使用自己的 MySQL 容器和命名卷，与本机 MySQL 服务隔离。

公开成果：[演示页](https://yaowcui-ai.github.io/aftersales-agent/demo/) · [GitHub源码](https://github.com/yaowcui-ai/aftersales-agent)。本次已实际完成部署和验收，详见 [acceptance.md](docs/delivery/acceptance.md)。当前机器的交付实例由Docker运行，直接打开 [工作台](http://127.0.0.1:8510) 即可，不用再启动本机版本。新旧教学任务、本机交付任务和Docker交付任务分别保存在各自数据库中，界面不会混入旧任务。切换部署方式前停止当前方式，勿同时争用默认端口；停止Docker时不使用 `down -v`，保留业务数据。

## 本机运行（Windows / PowerShell）

在项目根目录执行；依赖 Python 3.12–3.14、项目虚拟环境以及原 `.env` 中配置好的 MySQL 和模型访问。

```powershell
uv sync --frozen
.\scripts\start_delivery.ps1
```

启动脚本先检查 8090、8091、8510 端口，再执行 `scripts/setup_delivery.py`，随后依次启动 MCP、API 和 UI，并等待各服务健康检查。检查使用明确关闭代理的本地 HTTP 客户端，单次请求超时 3 秒，每项服务默认等待最多 90 秒，不改变系统或终端代理配置。服务进程隐藏运行；启动失败时只清理本次启动且身份可核实的进程。已健康运行时再次执行会返回现有入口。部分进程残留时先停止，再启动。

| 入口 / 文件 | 用途 |
| --- | --- |
| [客户和审批员工作台](http://127.0.0.1:8510) | Streamlit 登录、对话、审批与工单处理 |
| [API 文档](http://127.0.0.1:8090/docs) | 登录后用 Bearer token 调试接口 |
| [API 健康状态](http://127.0.0.1:8090/health) | 服务就绪检查 |
| `http://127.0.0.1:8091/mcp` | 有签名上下文保护的 Streamable HTTP MCP |
| `output/delivery/credentials.txt` | 首次初始化生成的本机演示登录密码 |
| `output/delivery/*stdout.log`、`*stderr.log` | 每次启动的独立进程日志 |
| `output/delivery/processes.json` | 本脚本启动的 PID、时间和命令标识 |

账户为 `customer1`、`customer2`、`reviewer`。客户身份分别绑定 `learner-001`、`learner-002`；审批员拥有审核与工单处理权限。密码随机生成，仅保存在本机凭据文件；不要将该文件或 `.env.delivery` 纳入版本管理。初始化重跑不重置已有账户和演示业务状态。

停止交付服务：

```powershell
.\scripts\stop_delivery.ps1
```

停止脚本检查项目路径、Python 可执行文件、命令标识及创建时间，仅停止记录中仍匹配的进程。Windows 虚拟环境启动器及其实际 Python 子进程分别记录；先核实并停止实际服务子进程，再停止启动器，避免仅停止启动器后端口仍被占用。PID 被复用时跳过；无法核实时保留记录并报错。不会停止 MySQL、旧 8080 服务或其他项目。

## Docker Compose

先运行本机初始化生成 `.env.delivery`，再启动 Docker Desktop 引擎。下列命令始终指定独立交付配置和独立项目名：

```powershell
.\.venv\Scripts\python.exe scripts/setup_delivery.py
docker compose --env-file .env.delivery -f compose.delivery.yaml config --quiet
docker compose --env-file .env.delivery -f compose.delivery.yaml up -d --build --wait --wait-timeout 300
docker compose --env-file .env.delivery -f compose.delivery.yaml ps
```

如果本机交付进程正在运行，先用 `stop_delivery.ps1` 释放相同端口。本机与 Docker 的演示业务数据相互独立；切换部署方式时看到的历史也不同。

容器顺序为 MySQL 健康 → 一次性 schema 初始化 → MCP 健康 → API 健康 → UI 健康。镜像使用 Python 3.12、uv 0.12.5 与锁定依赖；源代码显式复制，不将 `.env` 或凭据文件写入镜像。原 `.env` 仅在运行时提供模型设置，`.env.delivery` 提供交付签名密钥、账户哈希和新容器专用 MySQL 随机密码。Compose 将 MySQL 主机覆盖为 `mysql`，不复用本机数据库密码。

项目名固定为 `aftersales-delivery`，数据库卷为 `aftersales-delivery_delivery_mysql`；MySQL 不发布主机 3306 端口。应用仅发布本机回环地址的 8090、8091、8510。

```powershell
docker compose --env-file .env.delivery -f compose.delivery.yaml logs --tail 100 api mcp ui
docker compose --env-file .env.delivery -f compose.delivery.yaml down
```

`down` 保留业务卷；日常停止请保留该行为。不要删除卷来解决应用错误。首次初始化后保留同一份 `.env.delivery`，避免已有数据库卷密码与配置不一致。

## 配置与业务约束

配置优先级为项目 `.env` → `.env.delivery` → 当前进程环境变量，后者覆盖前者。初始化只读取本项目配置。已有 MySQL 账号、权限、配置和服务保持原样；本机仅创建或识别交付专用数据库，并初始化属于交付模块的表。

| 变量 | 说明 |
| --- | --- |
| `DEFAULT_MODEL` 及模型供应商密钥 | 沿用原项目配置，交付图复用 toolkit 模型工厂 |
| `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` | 本机既有连接；Docker 在容器环境覆盖 |
| `DELIVERY_SIGNING_KEY` | 自动生成的交付签名密钥 |
| `DELIVERY_ACCOUNTS_JSON` | 带盐密码哈希和固定角色、客户绑定 |
| `DELIVERY_MYSQL_PASSWORD` | 仅供新建 Docker MySQL 容器使用的随机密码 |
| `DELIVERY_MCP_URL` | 本机默认 `http://127.0.0.1:8091/mcp` |
| `DELIVERY_API_URL` | UI 调用地址；本机 8090、容器内 `http://api:8090` |
| `DELIVERY_CHECKPOINT_BACKEND` | 部署为 `mysql`；测试可使用隔离 SQLite |

客户身份、审批人和可执行动作来自服务端认证及数据库记录。模型不能通过工具参数切换客户或自批操作。MCP 使用不同用途的短时效签名上下文，普通网页登录 token 不能直接代替 MCP 执行授权。

退款金额以分存储，必须大于零且不超过实付金额减去已退款金额；演示规则允许已支付、已发货或已签收订单退款。补发要求已发货/已签收、尚未全额退款、累计补发不超过原订单数量、库存足够。准备时检查规则，执行事务再次检查，审批不能保证库存永远充足。重复执行同一动作返回已保存的原结果。

## 接口

除登录和健康检查外均需要 `Authorization: Bearer <token>`。请求不接受客户身份覆盖。

| 方法与路径 | 角色与作用 |
| --- | --- |
| `POST /auth/login` | `{username,password}` 换取 token 和用户信息 |
| `GET /me` | 当前账户与服务端绑定身份 |
| `POST /tasks/{thread_id}/messages` | 客户提交 `{message}`，创建或继续本人任务 |
| `GET /tasks/{thread_id}` | 读取有权查看的任务与已保存状态 |
| `GET /cases`、`GET /cases/{case_id}` | 客户查看本人记录；审批员查看工作范围内记录 |
| `GET /approvals` | 审批员查看待审批动作 |
| `POST /approvals/{action_id}/decision` | 审批员提交 `{approved,version}` |
| `POST /approvals/{action_id}/retry` | 审批员恢复已批准的原动作 |
| `POST /cases/{case_id}/status` | 审批员提交 `{status,resolution}`，按状态规则处理 |
| `GET /health` | 就绪检查 |

任务响应包含 `thread_id`、`customer_id`、`status`、`messages`、`proposal`、`final_answer`、`next_nodes`、`metrics`。其中消息保留模型工具调用和工具返回；候选动作包含金额、数量、版本、审批状态和已保存结果。对话回复失败与业务执行结果分别展示。

## 实现、验证与交付说明

架构与状态图见 [architecture.md](docs/delivery/architecture.md)，演示流程与验收观察点见 [demo.md](docs/delivery/demo.md)。业务事务/权限测试、图与 API 故障测试、真实 MySQL/MCP/模型联调，以及固定案例模型评测需要分别记录；规则测试通过不代表模型成功率。

| 验证项 | 证据位置 / 记录方式 |
| --- | --- |
| 自动化测试 | `tests/delivery/`；由实际测试命令结果确认 |
| 本机部署 | 启动日志、进程记录及三个健康端点 |
| Docker 部署 | `config --quiet`、构建输出、`compose ps`、健康状态 |
| 模型评测 | 48 固定案例（32 开发、16 保留），逐次原始记录；未执行部分不计成功 |
| 浏览器演示 | 按演示脚本记录实际操作、截图或视频 |

上述是验证入口与要求，不能将其当成已通过的验收结果；最终实际验证记录由本次交付的运行结果补充。Token 使用量只使用模型返回数据，缺失时保留缺失；费用不作估算成功率或伪造计费。

最新公开复测（2026-10-21）：80项应用测试通过，固定48案例各3次，140/144通过（97.2%），4条失败全部保留。历史2026-10-20结果为74项交付测试及224项相关回归、129/144（89.6%），原记录不回填。新轮是历史案例已查看后的回归复测，历史heldout不再视为未见测试集。详见验收与评测记录。实际Docker业务及API重启恢复通过；真实演示录像长186.4秒。源码导读见 [learning-guide.md](docs/delivery/learning-guide.md)，面试说明见 [interview.md](docs/delivery/interview.md)。模型评测与自动化规则测试分别报告，不能互相代替。

框架来自 Joshua Carroll 的 [agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit)，保留 MIT [LICENSE](LICENSE)，基线提交 `58f6a059f9787db1a68946030b35dd61023662fa`，详见 [framework-baseline.md](docs/framework-baseline.md)。沿用其模型工厂、LangGraph 依赖和原有 checkpoint 组件。本项目新增售后业务表与规则、身份隔离、审批执行、MCP 服务、图编排、专用 API/UI、评测和独立部署。项目由用户进行代表性机制实践、助手完成授权后的其余实现；面试或报告应按实际完成与理解范围说明个人贡献。

当前面向本机演示：内置演示账户与签名 token、单 API worker，未接入企业 SSO、支付/仓储、外部消息队列或公网 HTTPS 网关。单 Agent 已覆盖该流程的工具选择、审批与恢复；本项目没有知识库检索需求，因此没有额外引入 RAG 或多 Agent 编排。真实生产还需针对实际订单政策、外部支付幂等/对账、密钥托管和多实例调度另行设计与验收。
