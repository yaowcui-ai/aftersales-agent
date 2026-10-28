# 故障注入与恢复报告

只对新沙箱、临时SQLite、loopback HTTP及 `aftersales-upgrade` 容器主动注入。旧项目、宿主MySQL和历史实验不重置。

| 故障 | 注入/判定 | 证据层 |
|---|---|---|
| 数据库写入失败 | 在退款INSERT触发异常；余额/账本/case回滚，移除故障后一次执行 | SQLite真实SQL事件 |
| 提交成功丢响应 | 丢弃执行结果并断开连接池；读取并重放原动作，退款记录仍一条 | SQLite与MySQL分别运行 |
| 工具超时/断连 | 读调用有限重试，最终明确失败，无业务写入 | 图+受控错误 |
| 审批后MCP停机/API重启 | 停独立MCP、批准入库但执行失败，替换API进程，恢复原动作 | 真实Docker进程，`runtime/restart-*.json` |
| 收货/取消后checkpoint故障 | 业务已提交后故障，读取真实状态并恢复正确通知 | API集成故障注入 |
| 并发重复退款 | 同action同时执行，结果相同，唯一退款记录 | MySQL真实并发 |
| 不同动作竞争金额/库存 | 余额上限/共享库存竞争，一方执行，一方业务拒绝，无负库存 | MySQL真实并发 |
| 审批参数/订单变化 | 冻结摘要、版本、状态和余额重检，不执行变更对象 | 规则及安全测试 |

运行命令：

```powershell
python -m pytest tests/platform/test_fault_recovery.py tests/platform/test_security_boundaries.py -q
docker compose --env-file .env.delivery -f compose.upgrade.yaml cp scripts/verify_platform_mysql.py api:/app/verify_platform_mysql.py
docker compose --env-file .env.delivery -f compose.upgrade.yaml exec -T api python /app/verify_platform_mysql.py
python scripts/verify_platform_restart.py
```

MySQL脚本实测7/7机制检查通过；它包含事务、权限与连接池恢复，不能称7次真实进程重启。进程级恢复单独记录停止命令、HTTP错误、批准快照、替换前后container_id/PID/StartedAt与最终金额差；不把dispose连接池当作进程重启。

不同故障层的分母不同，当前不合成笼统“系统恢复率100%”。需要人工介入的业务拒绝、模型不可用或冻结参数冲突应保留失败/转人工，不能无限重试或新建重复退款。恢复率只可针对同层实际注入次数计算。

真实进程运行 `runtime/restart-4ff86e30241549628549.json` 实测1次注入、1次恢复：MCP停机后HTTP失败但批准记录保留，API的container_id及StartedAt发生变化；恢复同action后模拟退款增加0.10元，重放结果相同，冻结摘要不变。最终回复实际1次模型调用、234 tokens。该1/1只适用于这次进程故障，不外推其他故障。

HTTP500通过ASGI HTTP传输主动返回三次500，验证MCP会话错误和读工具重试上限及零写入；它不是公网/物理断网测试。见 `test_actual_http500_mcp_response_is_bounded_without_business_effect`。
