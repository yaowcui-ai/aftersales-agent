# 首轮真实接口集成记录

日期：2026-10-28；FastAPI `127.0.0.1:18100`，真实独立 MySQL，Edge，无业务接口 Mock。

- 首次表单提交成功，待审批真实状态是 `waiting_approval`；测试最初错误预期 `pending`，断言失败，没有产生审批或退款。
- 复用时 `/tickets` 旧响应不包含 `thread_id`，测试错误请求 `/tasks/undefined`，返回 `TASK_NOT_FOUND`；随后改为从 `/approvals` 读取原动作中的真实任务编号。
- 前端已把原 `request_id` 和服务端返回的 `thread_id` 分开持久化；成功响应后用任务编号读取，响应丢失时用 `GET /requests/{request_id}` 恢复。
- 本目录 JSON 和 PNG 是该原申请的实际服务端状态及界面证据，不是前端业务数据源。后续测试复用该申请，不重复创建退款申请。
