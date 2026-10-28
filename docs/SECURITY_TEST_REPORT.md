# 安全验证报告

范围：本地沙箱、受控测试账户、隔离SQLite与新Docker MySQL。不对外部目标发起攻击。测试请求不是人工可用性数据。

## 服务端边界

- session与MCP令牌用途不同，HMAC签名/过期/角色/配置账户归属均检查。
- 模型参数不能选择客户；MCP执行还绑定action_id。GET其他客户的订单、任务、ticket、case不泄露存在性或金额。
- 业务在锁内重检订单/余额/库存；模型提供“已批准”不能替代approval_reviews。
- 审批快照冻结对象、金额、数量、SKU、版本与理由；修改数据库执行字段也不能绕过摘要检查。
- React按文本渲染模型内容，无dangerouslySetInnerHTML；代理只访问固定上游，不接受客户端URL，不跟随跨域重定向传递Authorization。

## 实际证据

| 检查 | 原始位置 |
|---|---|
| 11项独立权限、恢复及代理攻击回归 | `tests/platform/test_security_boundaries.py` |
| 冻结对象/参数、并发、规则变化及退货门禁 | `tests/platform/test_rules.py` |
| MCP错误码在SDK会话退出后仍保留 | `tests/platform/test_transport_errors.py` |
| HTTP MCP真实身份隔离/未批执行拒绝 | `evaluation/upgrade/runtime/mysql-http-mcp.json` |
| 提示注入/伪造审批条件 | `evaluation/upgrade/cases.json` security类及真实v1 U088轨迹 |

11项独立测试包含：跨客户request恢复、会话purpose/role/customer伪造、额外审批参数、模型身份注入、ticket对应原任务、收货/取消提交后的图故障，以及loopback真实代理的SSRF、路径穿越和头部污染。伪造请求必须被服务端拒绝；不会仅以模型口头拒绝判安全。

## 本次发现并修复

1. 退货收货已提交但checkpoint失败时，completed分支错误返回旧“尚未退款”通知：改为按原动作恢复图及回复。
2. 退货授权已提交丢响应，awaiting_return不在恢复白名单：补齐恢复状态，不重复执行授权。
3. 取消已提交但图保存失败仍显示旧退货授权：读状态以数据库取消事实重整最终通知。
4. 客户端request_id与canonical thread混用，ticket缺关联thread：新增按request_id恢复与经授权的action关联字段。
5. MCP SDK ExceptionGroup隐藏业务拒绝码：仅拆出单一BusinessError，其他并发异常保持失败。
6. 工单处理与动作完成状态混用：ticket_status/action_status分别展示。

## 未宣称覆盖

未接企业身份系统、SSO、真实支付/仓储；本机演示账户不等于企业用户管理。没有证明对所有提示注入都有效，也未开展公网渗透或多实例分布式锁验证。依赖漏洞扫描与业务授权测试是不同证据。历史缺审批快照的已批准动作安全拒绝，不自动补造审计。
