# 公开验证记录

最新完整回归复测：140/144，97.2%。历史完整评测：129/144，89.6%。这是两个独立完整批次，不跨批次选择成功结果，也不把所有历史运行称为共144次。

- latest.json / latest.jsonl：2026-10-21新轮逐次任务与数据库证据，原始字节保留。
- historical-20261020.json / .jsonl：2026-10-20历史轮，保留全部15次失败。
- manifest.json：原始文件SHA256、冻结案例/判据哈希、并发参数和本轮4条失败索引。
- offline-tests.txt：80项应用离线测试实际输出。
- chat-ui-browser.json：新对话、追问、历史切换及刷新重登的实际浏览器记录；本机截图路径是当时记录，网页提供对应公开素材。
- docker-end-to-end.json、mysql-concurrency.json、mcp-http.json：原Docker/真实数据库/MCP验收记录。

复算使用标准库：`python tools/verify_evaluation.py`。程序重新执行冻结判据，不调用模型、数据库或运行中的API。真实重跑方法见docs/delivery/evaluation.md。

边界：历史heldout已查看，仅作回归分组；本轮并发2、旧轮并发3。语义关键词有覆盖局限，成功率不等于所有答复质量或生产泛化能力。
