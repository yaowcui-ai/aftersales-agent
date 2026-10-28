# 真实 Edge 写入闭环

2026-10-28，`npm run test:e2e -- --grep 'sandbox form'`：1 passed，12.2 秒。后端为独立 MySQL + HTTP MCP，业务接口未 Mock，退款均为沙箱模拟。

- 复用原 `UP-PAID` 待审批申请，管理员填写理由，批准模拟退款 1.00 元。
- `UP-SHIPPED` 退货退款 1.00 元，批准后先进入 `awaiting_return`，管理员确认收货后完成。
- 客户回读原任务，断言同一 `action_id`、`completed`、金额 `1.00` 和真实保存的 `refund_id`。
- 管理员读取任务与审计事件。原始关键响应保存于 `real-write-flow.json`，其余 PNG 为实际浏览器截图。

本轮无聊天请求。只在退款完成与退货收货完成时请求模型最终回复，业务审批与执行证据以 JSON 中的服务端结果为准。
