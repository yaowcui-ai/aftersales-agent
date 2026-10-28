# 循服 React 业务平台

真实 React + TypeScript + Vite 客户端。所有订单、物流、任务、售后申请、审批、工单和审计记录来自 FastAPI；无静态业务数据。沙箱退款与补发不会调用真实支付或仓储。

```powershell
npm ci
$env:API_PROXY_TARGET = 'http://127.0.0.1:18100'
npm run dev
```

开发地址为 `http://127.0.0.1:5173`。开发代理把同源 `/api/*` 转发到指定 FastAPI，并移除 `/api` 前缀。生产反向代理须采用同样规则。浏览器只保存当前标签页的 sessionStorage 会话，不使用 localStorage。

```powershell
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Playwright 使用本机 Edge (`channel: msedge`)，无需下载浏览器。默认 E2E 检查桌面/移动登录界面；设置独立沙箱的 `E2E_CUSTOMER_USERNAME` / `E2E_CUSTOMER_PASSWORD`、`E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD` 后，额外执行真实 HTTP 客户与管理员读取流程。可用 `E2E_BASE_URL` 指向已部署前端。不要将密码写入代码、截图或报告。

设置 `E2E_WRITE_FLOW=1` 后才运行真实沙箱写入测试：`UP-PAID` 模拟退款 1 元，以及 `UP-SHIPPED` 退货批准 → 收货确认 → 模拟退款 1 元。每笔完成后读取原任务，断言动作编号不变、完成状态、退款金额与退款编号。脚本优先复用测试产生的待审批原申请，不自动重放写入；完成后的再次运行会创建新的明确申请。审批和收货可能各消耗一次真实模型最终回复调用，因此仅在专用沙箱和剩余调用预算允许时启用。

建议完整验收：客户登录 → 订单分页与物流 → 表单创建待审批申请 → 管理员填写理由批准/拒绝 → 客户读取原任务与工单结果。退货退款批准后先显示等待收货，再由管理员确认收货。注入执行或最终回复故障后，读取原任务、审计记录并恢复原动作；确认没有产生第二笔退款/补发。

写请求从不自动重试。聊天在提交前保留原任务编号；表单分别保留幂等 `request_id` 和成功响应中的 `thread_id`。响应丢失时通过 `GET /requests/{request_id}` 恢复，已知任务编号时读取 `GET /tasks/{thread_id}`。模型输出作为 React 文本节点展示，不使用 HTML 注入。
