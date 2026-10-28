# AI全栈售后平台升级实施计划

> 执行方式：按现有授权持续实施；独立模块并行，接口契约先固定，所有结果由主代理复核。

**Goal:** 在原单Agent项目上增加React业务平台、严格沙箱规则和可核验评测。
**Architecture:** 保留现有包、API和Streamlit兼容入口；扩展业务/策略与审计表，MCP继续独立运行；React通过同源 `/api` 调用原FastAPI并补充REST，部署用独立Compose项目与新数据卷。
**Tech Stack:** Python3.12、现有锁文件、React/TypeScript/Vite、FastAPI、MySQL、LangGraph、MCP、pytest、Playwright。

- [ ] 审计与历史证据复算：`python tools/verify_evaluation.py`、`pytest tests/delivery -q`。
- [ ] 业务/安全：`db.py/business.py/policies.py`；先加失败测试，金额、时限、状态、冻结摘要、取消/退货收货均锁内校验。
- [ ] API/任务恢复：扩展`api.py`及任务索引，结构化请求日志；`tests/platform/test_api.py`覆盖真实HTTP依赖与拒绝路径。
- [ ] React用户/管理员：`frontend/`；构建、类型检查；所有业务读取/写入真实HTTP，离线浏览器使用明确的替身模型而非假数据库。
- [ ] 独立120场景与执行器：`evaluation/upgrade/`、`scripts/evaluate_upgrade.py`；开发/验证/测试分区固定，逐条保存初态、预期、轨迹与数据库终态。
- [ ] 故障/安全/并发：独立MySQL、HTTP MCP、进程重启、提交后丢响应、写失败和并发重放；不修改旧容器、旧库和宿主MySQL。
- [ ] Docker/React端到端：`compose.upgrade.yaml`，使用新端口/数据卷；实际客户申请、管理员审批、数据库效果、浏览器截图。
- [ ] 有限真实模型：硬上限60次模型HTTP调用、估算120000 tokens内；开始前保存预算和冻结源文件/案例哈希，不自动购买API。B0不调用模型；B1/B2/B3隔离同模型/同任务/同工具合同。若服务不可用如实报告。
- [ ] 文档与阶段提交：按用户列出的九份文档、README、简历证据；每项数字引用具体运行文件和验证命令。

## API契约（前后端并行依据）

保留 `/auth/login`、`/me`、`/tasks/{id}/messages`、`/tasks/{id}`、`/approvals`、`/approvals/{id}/decision`、`/approvals/{id}/retry`、`/cases`、`/cases/{id}`、`/cases/{id}/status`、`/actions/{id}/events`。
新增：GET `/orders?limit=20&offset=0` 返回total/orders；GET `/orders/{id}` 返回订单对象；GET `/orders/{id}/shipments` 返回shipments数组；GET `/tasks` 返回任务摘要数组；POST `/requests` 接收 `{request_id,kind,order_id,description,amount?,quantity?}`，返回图任务状态；POST `/actions/{id}/cancel`；GET `/admin/overview` 返回counts/tasks/alerts；POST `/returns/{action_id}/receive`（审批员）。审批请求新增可选reason，响应仍为原任务状态。所有界面显著注明沙箱模拟。

## 规则契约

有策略的订单：paid可直接refund；shipped/delivered直接refund报RETURN_REQUIRED，使用return_refund先批准退货，收货确认后按冻结金额模拟退款；reship仅shipped/delivered，未全退、有可补数量及库存；超售后期限、取消订单、金额/数量异常拒绝。高额或风险订单只转人工审查，不自动执行。所有写动作必须审批。旧无策略订单保留既有规则，不重新写历史结果。
