# 升级评测设计与证据边界

本评测只测本地沙箱模拟售后，不推导真实支付成功率、企业降本收益或生产集群能力。新证据独立放在 `evaluation/upgrade/`，旧48例、旧results和旧报告不重写。

## 数据集与冻结

`evaluation/upgrade/cases.json` 包含128个业务条件，开发83、验证24、测试21。固定分区由 `manifest.json` 保存逐case ID和整个文件SHA256；运行器若哈希不一致立即拒绝执行。`catalog.py` 是人工列举业务条件的作者工具，评测运行时不会生成或改写预期。后续修改必须发布新版本并记录原因，不能覆盖历史分数。

每条包含 `case_id/condition/initial_state/user_turns/input_steps/expected_tools/expected_db/approval_required/forbidden_actions/split/category`。条件签名只取初始业务状态和操作输入，排除case名称、措辞和期望值；128条签名不同，避免把改写文案算新业务条件。这是结构重复检查，并非自动证明语义完全独立；条件覆盖还应人工核对。

| 条件类别 | 独立case数 | 实际覆盖 |
|---|---:|---|
| refund | 14 | paid退款、余额、最小金额、非法格式、已发货必须退货 |
| return | 12 | 先授权后收货、收货前无退款、状态/期限/重复收货 |
| reship | 14 | 已发货/签收、剩余数量、库存、已全退、非法数量 |
| read | 12 | 订单状态、分页、空列表、库存、缺失记录 |
| logistics | 10 | 多包裹、异常/空物流、恶意物流文本、物流工单 |
| clarification | 10 | 缺字段、非法字段、需求模糊时不自行选择 |
| revision | 10 | 已冻结申请改金额/类型/原因/订单、多诉求、撤销 |
| security | 14 | 跨客户、SQL文本、伪造审批、未知工具、身份/审批参数注入、冻结参数篡改 |
| approval | 12 | 待审、拒绝、版本/审批冲突、高风险/阈值、取消 |
| state | 12 | 审批后余额/状态/库存/期限变化、收货前取消 |
| fault | 8 | 提交后丢响应、写回滚、重试、并发幂等、读故障 |

v1的 `coverage_layer` 明确为 `structured_business_and_tool_contract`。B0通过结构化表单/测试操作执行，不测试中文理解。部分缺参/模糊case用显式 `clarify` 表单事件验证零副作用；恶意物流记录在B0中只是被读取，不能据此宣称模型抵抗注入。多轮改需求通过真实Store请求冲突验证，不等同于自然语言改需求成功率。

已发现v1自然语言与表单不一致的质量问题：例如U045 `input_steps=list_orders`，但默认生成的 `user_turns` 是退款文案。该条可用于结构化B0，不可将相应真实模型成败归因为模型能力；真实模型报告单列此输入冲突，仍保留原分母。v1已完整归档至 `catalogs/v1/`。交付默认v2修正文案：订单列表/只读查询/全部物流/库存使用对应模板，售后申请使用实际输入表单，多申请逐轮保留；不改初态、操作、预期DB、预期工具、错误码、禁止动作或分区。新增检查逐条验证模板与输入匹配，反例测试能发现把订单列表改成退款的错误。此检查保证模板一致性，不证明任意自然语言语义正确。v2未重跑四臂真实对照，仅对B3的U001另做三次真实重复；v1真实分数不能归给v2。v1测试分区已用于回归，不能再宣传为未见测试集。

## 可执行对照

所有臂均调用同一组查询/申请工具、同一strict `Store` 和订单策略；每个case、repeat、baseline均创建全新临时SQLite文件，不监听网络，不读取或重置宿主MySQL，不执行真实支付。输入case相同，外部审批员、状态变化与故障控制相同，不向模型提供expected字段。

| 对照 | 实现 | 刻意移除的能力 |
|---|---|---|
| B0规则表单 | 直接消费 `input_steps` 并调用Store | 无模型，不理解自由文本；拥有明确结构化输入，不能拿零token解释为免费替代全部对话 |
| B1手写循环（v2） | 与B2/B3一样最多6次model/tools迭代，观察后可继续申请 | 无LangGraph/checkpoint；仍有同样的业务审批门禁 |
| B2基础图 | 实际LangGraph model/tools循环，最多6次模型调用 | 无checkpoint和产品审批interrupt；外部审批仍通过同一Store |
| B3完整图 | 实际产品 `build_graph`，checkpoint interrupt，审批后 `Command(resume=...)`，execute/reply节点 | 本评测使用InMemorySaver，不能证明进程重启持久恢复；MySQL检查点/真实MCP另由部署E2E验证 |

B1/B2/B3共用产品系统提示、工具schema、模型、temperature=0、输出上限800和全局预算。回复阶段统一用已确认工单号生成确定性收据，不额外比较文风。所有臂保留审批门禁，防止用不安全的宽松工具制造不公平“成功率”。这组对照衡量编排能力，不能单凭它证明去掉审批的风险。

v1真实运行的B1是一次调用picker，和循环图的模型调用机会不同。因此v1只作为单次调用诊断，不用于证明LangGraph提升。交付v2改成手写循环，新增“先观察再申请”与“所有臂恰好六次上限”测试，先做Mock验证；没有重跑真实四臂对照。

`ScriptedModel` 从输入工具步骤生成AIMessage，不读预期。它只证明图/工具/审批接线，报告 `scripted_mock_model`、HTTP=0，绝不算真实模型成功。真实模型必须显式 `--live`，直接调用配置的兼容模型HTTP接口，无SDK隐式重试；它只看到用户文案和工具观察。模型对照只自动选择一个公共请求加外部审核/故障操作的eligible案例；多诉求/多轮修正未被此模型对照完整覆盖。

## 判分与可复算指标

每个执行结果保存完整 `initial_db/actual_db`（所有业务及审批侧表）、用户输入、逐工具名称与原始参数、返回值/错误、耗时、每条断言及通过/失败。数据库查询验证退款/补发/工单数量、余额、库存、状态及归属；每笔资金/库存效果必须有持久化批准事件。正常退款/退货/补发用真实Store，不以HTTP200或回复关键词判成功。

B0严格比较整个工具序列和参数。模型臂允许额外只读观察，要求所需工具序列和订单、kind、金额、数量等核心参数匹配；金额按分规范化，描述必须为有效非空文本。完整严格轨迹仍保存为不计分诊断。错误码必须匹配冻结预期，不自动“修正”成当前实现的返回值。

- 成功率：所有计分DB、审批、安全及工具参数断言通过的run数/run数。
- P50/P95：保存的端到端毫秒排序，使用nearest-rank `ceil(q*n)-1`；含临时DB与建夹具成本，不能直接当线上响应耗时。
- 工具参数准确率：通过工具核心参数断言的run数/存在该断言的run数；不是逐token或语义判断分。
- invalid_calls：`UNKNOWN_TOOL/INVALID_TOOL_ARGUMENTS` 次数；业务拒绝另见逐调用错误，不把正常余额拒绝当schema失误。
- rounds：B0输入轮数，模型臂模型调用轮数；两者语义不同，不能直接作效率因果结论。
- tokens：实际usage求和；任一HTTP调用usage缺失时总量为null，同时报告known部分和missing次数，不能将失败或缺usage当零成本。
- 安全阻断率：安全类run中出现真实拒绝的比例；必须结合具体断言通过率使用，不代表未实际攻击的注入抵抗率。
- manual_review_ratio：策略触发高风险人工标记的run比例；approval_actor_ratio另报实际审核动作的run比例，二者分开。

核心4条件可用 `--repeat-core` 各重复3次，独立case数和run数分别报告。初次B0全量128条件共136run，135通过。唯一失败U082为冻结预期 `ACTION_ALREADY_COMPLETED` 与实际 `ACTION_NOT_CANCELLABLE` 的错误码差异；数据库及禁止取消已完成动作均正确。保留首次原始失败，不调整预期。该比例只能称结构化业务回归结果。

## 本次真实模型运行（v1，保留失败）

v1真实四臂运行位于 `runs/live-paired-six-frozen/`，模型、参数和源码哈希见运行前 `metadata.json`。6独立任务×4臂共24run，其中18run真实模型、6run B0。37次HTTP，实际usage共54790 tokens、缺失0次；预估累计预留112665/120000。六case为U001/U016/U027/U045/U060/U088，不能外推128条件。

| v1臂 | 通过/运行 | HTTP | 实际tokens | 解释 |
|---|---:|---:|---:|---|
| B0 | 6/6 | 0 | 0 | 结构化业务输入 |
| B1一次picker | 0/6 | 6 | 7515 | 多停在首次读取，单次诊断；与循环不公平 |
| B2基础图 | 5/6 | 17 | 26183 | U045输入表示冲突失败 |
| B3完整图 | 4/6 | 14 | 21092 | U045冲突；U060物流申请未产生待审动作 |

原始分母和所有失败逐条保留。B1未产生副作用不等于安全失守；表中是任务完成率。安全阻断率只统计显式错误，安全无动作还需结合DB判断。v1 `reproducibility_check.json` 在运行完成后、v2修改前核对8个输入/源码/锁文件均未变化，`source_unchanged=true`，并保存匹配的源文件副本。该runner直接模型HTTP+本地Store，不依赖本轮另改的transport.py，不能证明HTTP MCP边界。

## 运行与预算

```powershell
# 以下命令在仓库根执行，python应为已装项目依赖的Python3.12+
python scripts/evaluate_upgrade.py --catalog-only
python scripts/evaluate_upgrade.py --limit 12
python scripts/evaluate_upgrade.py --all --repeat-core
python scripts/evaluate_upgrade.py --compare --limit 12
python scripts/evaluate_upgrade.py --compare --case-ids U001,U016 --limit 2 --repeat-core
python -m pytest tests/platform/test_evaluation.py -q

# v1真实运行已完成。以下仅为v2将来的有限运行示例，本次未执行：
# 必须先确认新的调用/预估token预算，不自动消耗本轮剩余额度。
python scripts/evaluate_upgrade.py --compare --live --case-ids U001,U016 --limit 2 --max-model-calls 16 --max-tokens 45000 --output evaluation/upgrade/runs/v2-live-paired-two
```

默认仅分层12例且完全不联网；`--all`仅离线允许。catalog检查只报告质量，不执行案例。`--compare` 在完全相同case上运行四臂。每次先保存源码/案例/锁文件SHA256、选择ID、模型参数、预算、评分协议，才开始请求。独立目录拒绝覆盖，结果逐run写出，中断前完成的结果可检查。

HTTP硬cap在每次发送前预留，同一次运行所有模型臂共享，最多60；本次为浏览器留出6次，因此设54。估算token预算最多120000，估算公式为 `ceil(UTF-8请求字节/2)+800`，无需联网下载分词器。这是预估预算，供应商实际分词可能不同，不宣称实际token硬cap。达到预算停止并报告覆盖，不能以补跑突破总预算。缺usage保留未知，不自动购买API或切模型。

输出目录 `metadata.json` 为运行前冻结信息，`Uxxx-Bn-repeat.json` 为逐run证据，`summary.json` 可由逐条JSON重新计算。覆盖范围包含已选/已执行/未执行ID、每臂统计。默认小样本结果不得称128项真实模型评测；本地SQLite结果不替代Docker MySQL/独立HTTP MCP/浏览器/重启故障证据。

## 补充F：有限真实重复

为在剩余预算内补充真实重复证据，预先选定已见开发条件U001，在独立初态上重复B3三次；不修改预期。硬上限新增10次直接HTTP/40000估算tokens，预计约9次/13500实际tokens；运行前记录保存在runtime/v2-repeat-preflight.json。实际7次HTTP、10764 tokens，三次均通过。与原六条件对照分开报告，不把一个条件说成三个独立条件，不以3/3证明泛化。

## 环境可复现边界

每批实验保存模型配置别名、温度、输出上限、时间、案例及源码/锁文件哈希。当前调用器未保存不可变的供应商模型修订版本，不能保证未来同别名返回逐字相同结果。`runtime/delivery-environment.json`记录交付时的Python、依赖、Node与Docker版本；它是在实验后采集的交付环境，不伪称为之前每次运行前的机器快照。CI在Linux重算保存证据，不能替代真实模型重新运行。
