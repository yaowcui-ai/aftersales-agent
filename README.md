# 电商售后工单 Agent

基于 **LangGraph + MCP + FastAPI + MySQL** 的单 Agent 应用。围绕订单和物流查询、物流异常工单、模拟退款、模拟补发，实现“自然语言需求 → 查询事实 → 准备申请 → 人工审批 → 受控事务执行 → 结果回复”的完整流程。

**[公开演示页](https://yaowcui-ai.github.io/aftersales-agent/demo/)** · [架构与状态图](docs/delivery/architecture.md) · [评测方法](docs/delivery/evaluation.md) · [原始验证记录](evaluation/results/) · [本地运行说明](README_DELIVERY.md)

演示页提供真实运行录像、聊天窗口与历史恢复截图，以及可复算的验证记录。它是公开成果展示页；需要交互操作时，请按下文在本机启动完整应用。退款与补发使用模拟数据，不连接支付或仓储渠道。

## 可以验证什么

- **聊天与历史**：查询并多轮追问；按原任务编号从 MySQL checkpoint 加载历史；切换对话不重复发送用户消息。
- **工具与权限**：通过独立 Streamable HTTP MCP 查询订单、物流、库存和工单。身份来自服务端，不允许模型填写客户身份；查询限制订单归属。
- **审批执行**：模型只能提出 `prepare_action`；LangGraph `interrupt` 暂停。审批员提交决定后，受控节点才调用 `execute_action`。
- **幂等与并发**：同一动作复用已保存结果，参数改变报冲突；事务、唯一约束和条件更新限制重复退款、重复补发与超卖。
- **失败恢复**：提交后丢响应先查原动作；业务完成而回复失败时只恢复回复阶段。动作编号 `action_id`、工单编号 `case_id` 与对话 `thread_id` 分开使用。

## 验证结果

| 验证 | 当前公开口径 | 原始记录 |
| --- | --- | --- |
| 应用离线测试 | 80 项通过，使用替身模型及隔离 SQLite | [pytest 输出](evaluation/results/offline-tests.txt) |
| 真实浏览器 | 7 个聊天、追问、历史切换与刷新恢复步骤通过 | [浏览器记录](evaluation/results/chat-ui-browser.json) |
| 真实 MySQL / MCP / Docker | 并发退款、库存竞争、幂等重放及完整审批业务验收 | [MySQL](evaluation/results/mysql-concurrency.json) · [MCP](evaluation/results/mcp-http.json) · [Docker](evaluation/results/docker-end-to-end.json) |
| 真实模型新一轮回归复测 | **140 / 144，97.2%**（2026-10-21） | [新轮 JSON](evaluation/results/latest.json) · [逐次 JSONL](evaluation/results/latest.jsonl) |
| 历史完整评测 | 129 / 144，89.6%，保留全部失败 | [历史 JSON](evaluation/results/historical-20261020.json) · [逐次 JSONL](evaluation/results/historical-20261020.jsonl) |

真实模型评测采用冻结的 48 案例，每例 3 次。最终成功须同时满足预期动作、权限、回复及数据库结果，不能只看 HTTP 200 或模型说“成功”。新轮是查看过历史结果后的回归复测；原 `heldout` 分组保留用于对照，不再代表未见独立测试集。历史并发 3、新轮并发 2，不能把结果差异直接解释为模型能力提升。

指标包括系统内部的有限重试，以及评测协议允许的原动作回复恢复；不是每次第一次调用的成功率。固定关键词判据也有误判和覆盖局限，详见[评测说明](docs/delivery/evaluation.md)。

面试官无需 API Key 即可复算公开报告：

```bash
python tools/verify_evaluation.py
```

该命令检查全部 144 个 `(case_id, repetition)` 组合、JSON/JSONL 一致性，并依据冻结判据重新计算每次结果与汇总。复算已保存记录不等于重新调用模型；真实重跑需要自己的模型配置。

## 本地运行

要求 Python 3.12、uv 0.12.5、Docker Compose。先复制配置示例，填写自己的模型凭据：

```bash
cp .env.example .env
uv sync --frozen
uv run python scripts/setup_delivery.py --configure-only

docker compose --env-file .env.delivery -f compose.delivery.yaml up -d --build --wait
```

`.env` 使用兼容 OpenAI 接口的模型时填写：

```dotenv
DEFAULT_MODEL=openai-compatible
COMPATIBLE_MODEL=your-model-name
COMPATIBLE_BASE_URL=https://your-provider.example/v1
COMPATIBLE_API_KEY=your-own-key
USE_FAKE_MODEL=false
```

然后打开 [客户与审批工作台](http://127.0.0.1:8510)；API 文档为 [http://127.0.0.1:8090/docs](http://127.0.0.1:8090/docs)。账户为 `customer1`、`customer2`、`reviewer`，随机密码由初始化脚本写入本机 `output/delivery/credentials.txt`，不在公开仓库提供。Docker 使用独立 MySQL 容器与数据卷，不发布宿主 3306。

离线验证不需要真实模型密钥或正在运行的数据库：

```bash
uv run --frozen pytest tests/delivery -q
uv run --frozen python scripts/evaluate_delivery.py --offline
```

停止时使用 `docker compose --env-file .env.delivery -f compose.delivery.yaml down` 保留数据卷。更多配置及接口见 [README_DELIVERY.md](README_DELIVERY.md)。

## 源码导航

| 路径 | 职责 |
| --- | --- |
| `src/aftersales_delivery/agent.py` | 模型、工具、审批、执行和回复节点及条件路由 |
| `src/aftersales_delivery/api.py` | 登录鉴权、对话、审批、状态与恢复接口 |
| `src/aftersales_delivery/business.py` | 业务规则、事务、动作幂等和持久化结果 |
| `src/aftersales_delivery/mcp_server.py` | 工具服务与签名上下文校验 |
| `src/aftersales_delivery/ui.py` | 客户聊天、历史导航、审批与工单界面 |
| `tests/delivery/` | 业务、权限、工作流、恢复与界面测试 |
| `evaluation/delivery/cases.json` | 固定案例、预期结果和分组 |
| `docs/demo/` | 公开演示页与真实录屏 |

## 框架来源与实现边界

基于 Joshua Carroll 的 MIT 开源项目 [agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit)，固定学习基线为 `58f6a059f9787db1a68946030b35dd61023662fa`，保留原 [LICENSE](LICENSE) 及[上游说明](docs/upstream-toolkit.md)。复用其模型工厂、LangGraph 依赖和 checkpoint 组件，新增售后业务、身份隔离、审批执行、幂等恢复、独立 MCP、专用界面、评测和部署。上游通用示例代码保留在仓库，本文展示的是 `aftersales_delivery` 交付入口。

项目采用 AI 辅助实现。本人完成代表性机制实践，后续在设计与验证基础上使用 AI 辅助完成其余实现；不声称从零独立编写全部框架代码。当前为单 API worker、本机演示账户和模拟业务，未接企业 SSO、真实支付或仓储，不宣称生产系统能力。
