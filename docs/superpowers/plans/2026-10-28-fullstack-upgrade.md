# AI全栈售后平台升级实施计划

> 执行方式：按现有授权持续实施；独立模块并行，接口契约先固定，所有结果由主代理复核。

**Goal:** 在原单Agent项目上增加React业务平台、严格沙箱规则和可核验评测。
**Architecture:** 保留现有包、API和Streamlit兼容入口；扩展业务/策略与审计表，MCP继续独立运行；React通过同源 `/api` 调用原FastAPI并补充REST，部署用独立Compose项目与新数据卷。
**Tech Stack:** Python3.12、现有锁文件、React/TypeScript/Vite、FastAPI、MySQL、LangGraph、MCP、pytest、Playwright。

- [x] 审计与历史证据复算：旧140/144记录及哈希未变；最终Python150项包含旧83项。
- [x] 业务/安全：`db.py/business.py/policies.py`；金额、时限、状态、冻结摘要、取消/退货收货均锁内校验。
- [x] API/任务恢复：任务索引、结构化日志、按request_id恢复及实际数据库状态核对；新增API和故障回归。
- [x] React用户/管理员：真实HTTP、生产构建、6项单测及真实浏览器业务闭环；没有用静态JSON替代业务。
- [x] 独立128条件与执行器：固定开发/验证/测试分区；136次结构化执行135通过，保留U082错误码差异。
- [x] 故障/安全/并发：独立MySQL/MCP7项、一次真实容器进程替换恢复及SQL故障测试；未修改旧库或宿主服务。
- [x] Docker/React端到端：新端口/卷；表单退款/退货和自然语言Agent申请、界面审批、数据库效果、刷新恢复均有记录。
- [x] 有限真实模型探索：冻结v1六条件；37次直接计数HTTP、54790 tokens。另计产品验收后另加U001真实重复后共观测56次应用级调用、81340 tokens；SDK内部重试未知，不将应用调用冒充供应商HTTP计费日志。v1的B1不公平与U045输入缺陷均保留。
- [x] 文档与阶段提交：九份报告、README、简历证据及阶段提交；数字均区分历史、Mock、真实模型和业务端到端。
- [x] v2核心真实重复：已见开发条件U001、B3独立初态三次，3/3通过；7次HTTP/10764 tokens，不能外推泛化。
- [ ] v2公平四臂真实对照：执行器和Mock已完成，尚未执行真实四臂重跑；未证明B3决策收益。
- [ ] 真人业务价值研究/企业上线：当前仅沙箱，不宣称企业人效收益、真实资金或生产集群能力。

## API契约（前后端并行依据）

保留 `/auth/login`、`/me`、`/tasks/{id}/messages`、`/tasks/{id}`、`/approvals`、`/approvals/{id}/decision`、`/approvals/{id}/retry`、`/cases`、`/cases/{id}`、`/cases/{id}/status`、`/actions/{id}/events`。
新增：GET `/orders?limit=20&offset=0` 返回total/orders；GET `/orders/{id}` 返回订单对象；GET `/orders/{id}/shipments` 返回shipments数组；GET `/tasks` 返回任务摘要数组；POST `/requests` 接收 `{request_id,kind,order_id,description,amount?,quantity?}`，返回图任务状态；POST `/actions/{id}/cancel`；GET `/admin/overview` 返回counts/tasks/alerts；POST `/returns/{action_id}/receive`（审批员）。审批请求新增可选reason，响应仍为原任务状态。所有界面显著注明沙箱模拟。

## 规则契约

有策略的订单：paid可直接refund；shipped/delivered直接refund报RETURN_REQUIRED，使用return_refund先批准退货，收货确认后按冻结金额模拟退款；reship仅shipped/delivered，未全退、有可补数量及库存；超售后期限、取消订单、金额/数量异常拒绝。高额或风险订单只转人工审查，不自动执行。所有写动作必须审批。旧无策略订单保留既有规则，不重新写历史结果。
