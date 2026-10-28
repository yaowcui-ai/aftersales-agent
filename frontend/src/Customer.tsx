import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiClient, requestReadPath } from "./api";
import { useResource } from "./hooks";
import type { Order, Shipment, Task, User } from "./types";
import {
  Badge,
  date,
  Empty,
  ErrorNotice,
  kindName,
  Loading,
  money,
  Panel,
  RawDetails,
  TaskProgress,
} from "./ui";
import { Tickets } from "./Tickets";

export function CustomerWorkspace({
  api,
  page,
  user,
  navigate,
}: {
  api: ApiClient;
  page: string;
  user: User;
  navigate: (page: string) => void;
}) {
  const [orderId, setOrderId] = useState("");
  const [requestTask, setRequestTask] = useState<Task>();
  return (
    <>
      {page === "orders" && (
        <Orders
          api={api}
          onRequest={(id) => {
            setOrderId(id);
            navigate("request");
          }}
        />
      )}
      {page === "chat" && <Chat api={api} user={user} />}
      {page === "request" && (
        <RequestForm
          api={api}
          orderId={orderId}
          user={user}
          task={requestTask}
          setTask={setRequestTask}
        />
      )}
      {page === "tickets" && <Tickets api={api} admin={false} />}
    </>
  );
}
function Orders({
  api,
  onRequest,
}: {
  api: ApiClient;
  onRequest: (id: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const orders = useResource<{ orders: Order[]; total: number }>(
    api,
    `/orders?limit=10&offset=${offset}`,
  );
  const [selected, setSelected] = useState("");
  return (
    <>
      <PageTitle
        eyebrow="YOUR ORDERS"
        title="我的订单"
        description="查看订单与真实保存的物流记录，选择需要帮助的订单。"
        extra={
          <button className="button" onClick={() => void orders.refresh()}>
            ↻ 刷新订单
          </button>
        }
      />
      <ErrorNotice error={orders.error} recover={() => void orders.refresh()} />
      {orders.loading && !orders.data ? (
        <Loading />
      ) : (
        <Panel>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>订单 / 商品</th>
                  <th>付款金额</th>
                  <th>退款 / 补发</th>
                  <th>订单状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {orders.data?.orders.map((order) => (
                  <tr key={order.order_id}>
                    <td>
                      <button
                        className="text-link mono"
                        onClick={() => setSelected(order.order_id)}
                      >
                        {order.order_id}
                      </button>
                      <small>
                        {order.sku} · {order.quantity} 件
                      </small>
                    </td>
                    <td className="amount">{money(order.paid_amount)}</td>
                    <td>
                      {money(order.refunded_amount)}
                      <small>已补发 {order.reshipped_quantity} 件</small>
                    </td>
                    <td>
                      <Badge status={order.status} />
                    </td>
                    <td>
                      <div className="row">
                        <button
                          className="button small"
                          onClick={() => setSelected(order.order_id)}
                        >
                          物流详情
                        </button>
                        <button
                          className="button small primary-soft"
                          onClick={() => onRequest(order.order_id)}
                        >
                          申请售后
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {orders.data?.orders.length === 0 && (
            <Empty title="当前没有订单">
              此处仅显示当前账号所属的服务端订单。
            </Empty>
          )}
          <div className="pagination">
            <span>共 {orders.data?.total ?? "—"} 笔 · 每页 10 笔</span>
            <div className="row">
              <button
                className="button small"
                disabled={offset === 0 || orders.loading}
                onClick={() => setOffset(Math.max(0, offset - 10))}
              >
                上一页
              </button>
              <span>{Math.floor(offset / 10) + 1}</span>
              <button
                className="button small"
                disabled={
                  !orders.data ||
                  offset + 10 >= orders.data.total ||
                  orders.loading
                }
                onClick={() => setOffset(offset + 10)}
              >
                下一页
              </button>
            </div>
          </div>
        </Panel>
      )}
      {selected && (
        <OrderDetail
          key={selected}
          id={selected}
          api={api}
          close={() => setSelected("")}
          onRequest={onRequest}
        />
      )}
      <div className="notice subtle">
        <strong>服务小贴士</strong>
        <p>
          已发货或签收的订单通常需要先退货。申请会由服务端校验售后时限、可退金额与库存，再进入人工审批。
        </p>
      </div>
    </>
  );
}
function OrderDetail({
  api,
  id,
  close,
  onRequest,
}: {
  api: ApiClient;
  id: string;
  close: () => void;
  onRequest: (id: string) => void;
}) {
  const order = useResource<Order>(api, `/orders/${encodeURIComponent(id)}`);
  const shipments = useResource<{ shipments: Shipment[] }>(
    api,
    `/orders/${encodeURIComponent(id)}/shipments`,
  );
  return (
    <Panel
      title={`订单详情 · ${id}`}
      extra={
        <button className="button small" onClick={close}>
          收起详情
        </button>
      }
    >
      <ErrorNotice error={order.error || shipments.error} />
      {order.loading ? (
        <Loading />
      ) : (
        order.data && (
          <>
            <div className="row spread">
              <div>
                <h3>{order.data.sku}</h3>
                <p>
                  {order.data.quantity} 件 · 实付{" "}
                  {money(order.data.paid_amount)}
                </p>
              </div>
              <button className="button primary" onClick={() => onRequest(id)}>
                为此订单申请售后
              </button>
            </div>
            {order.data.policy && (
              <RawDetails title="订单售后规则" data={order.data.policy} />
            )}
          </>
        )
      )}
      <h3 className="section-label">物流记录</h3>
      {shipments.loading ? (
        <Loading />
      ) : shipments.data?.shipments.length ? (
        <ol className="timeline">
          {shipments.data.shipments.map((shipment) => (
            <li key={shipment.shipment_id}>
              <div className="row spread">
                <strong>{shipment.description}</strong>
                <Badge status={shipment.status} />
              </div>
              <p>
                {shipment.carrier} · {shipment.tracking_number}
              </p>
              <small>{date(shipment.updated_at)}</small>
            </li>
          ))}
        </ol>
      ) : (
        !shipments.error && (
          <Empty title="尚无物流记录">付款后尚未发货的订单可能没有物流。</Empty>
        )
      )}
    </Panel>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  extra,
}: {
  eyebrow: string;
  title: string;
  description: string;
  extra?: React.ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {extra}
    </div>
  );
}
function Chat({ api, user }: { api: ApiClient; user: User }) {
  const transcript = useRef<HTMLDivElement>(null);
  const key = `aftersales.chat.${user.username}`;
  const [thread, setThread] = useState(() => sessionStorage.getItem(key) || "");
  const [task, setTask] = useState<Task>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(!!thread);
  const [text, setText] = useState("");
  const history = useResource<Task[]>(api, "/tasks");
  useEffect(() => {
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [task?.messages?.length, task?.final_answer, busy]);
  async function read(id = thread) {
    if (!id) return;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.get<Task>(`/tasks/${encodeURIComponent(id)}`);
      setTask(saved);
      setThread(id);
      sessionStorage.setItem(key, id);
      setUncertain(false);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (thread) void read(thread);
  }, []);
  useEffect(() => {
    if (
      !thread ||
      !task ||
      ![
        "running",
        "awaiting_approval",
        "waiting_approval",
        "pending_approval",
        "awaiting_return",
      ].includes(task.status)
    )
      return;
    const timer = window.setInterval(() => {
      void read(thread);
    }, 7000);
    return () => clearInterval(timer);
  }, [thread, task?.status]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!text.trim() || busy || uncertain || task?.proposal) return;
    const id = thread || `web-${crypto.randomUUID()}`;
    setThread(id);
    sessionStorage.setItem(key, id);
    setBusy(true);
    setError(undefined);
    setUncertain(true);
    try {
      const saved = await api.post<Task>(`/tasks/${id}/messages`, {
        message: text.trim(),
      });
      setTask(saved);
      setText("");
      setUncertain(false);
      void history.refresh();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  function newChat() {
    if (busy) return;
    setThread("");
    setTask(undefined);
    setError(undefined);
    setUncertain(false);
    setText("");
    sessionStorage.removeItem(key);
  }
  const visible = task?.messages?.filter(
    (message) =>
      ["human", "user", "ai", "assistant"].includes(message.type) &&
      typeof message.content === "string" &&
      message.content,
  );
  return (
    <>
      <PageTitle
        eyebrow="AFTERCARE ASSISTANT"
        title="智能售后"
        description="描述订单问题，助手会在信息不足时继续询问；所有业务写入都需要人工审批。"
        extra={
          <button className="button" disabled={busy} onClick={newChat}>
            ＋ 新建对话
          </button>
        }
      />
      <div className="chat-layout">
        <Panel title="历史对话" className="history-panel">
          <ErrorNotice error={history.error} />
          {history.loading && !history.data ? (
            <Loading />
          ) : history.data?.length ? (
            history.data.map((item) => (
              <button
                key={item.thread_id}
                className={`history-item ${thread === item.thread_id ? "selected" : ""}`}
                disabled={busy}
                onClick={() => void read(item.thread_id)}
              >
                <strong>
                  {item.final_answer?.slice(0, 38) || item.thread_id}
                </strong>
                <Badge status={item.status} />
                <small>{date(item.updated_at)}</small>
              </button>
            ))
          ) : (
            <Empty title="暂无对话" />
          )}
          <form
            className="restore-form"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              void read(String(data.get("thread")).trim());
            }}
          >
            <label>
              按原任务编号恢复
              <input
                name="thread"
                required
                maxLength={64}
                placeholder="输入已保存的编号"
              />
            </label>
            <button className="button small full" disabled={busy}>
              读取任务
            </button>
          </form>
        </Panel>
        <div>
          <Panel className="chat-panel">
            <div className="chat-heading">
              <span className="assistant-avatar">循</span>
              <div>
                <strong>售后服务助手</strong>
                <small>连接订单、物流与人工审批</small>
              </div>
              {thread && (
                <button
                  className="button small"
                  disabled={busy}
                  onClick={() => void read()}
                >
                  读取已保存状态
                </button>
              )}
            </div>
            <div className="messages" aria-live="polite" ref={transcript}>
              {!task && (
                <div className="welcome-message">
                  <span className="assistant-avatar large">循</span>
                  <h2>您好，需要什么帮助？</h2>
                  <p>
                    请提供订单号和具体问题。您可以查询物流，
                    <br />
                    或申请模拟退款、退货退款与补发。
                  </p>
                  <div className="suggestions">
                    {["查询我的订单", "我想查询物流", "如何申请退货退款？"].map(
                      (prompt) => (
                        <button key={prompt} onClick={() => setText(prompt)}>
                          {prompt} ↗
                        </button>
                      ),
                    )}
                  </div>
                </div>
              )}
              {visible?.map((message, index) => (
                <div
                  key={index}
                  className={`message ${["human", "user"].includes(message.type) ? "human" : "assistant"}`}
                >
                  <small>
                    {["human", "user"].includes(message.type)
                      ? "我"
                      : "服务助手"}
                  </small>
                  <p>{message.content as string}</p>
                </div>
              ))}
              {task?.final_answer &&
                !visible?.some(
                  (message) => message.content === task.final_answer,
                ) && (
                  <div className="message assistant">
                    <small>服务助手</small>
                    <p>{task.final_answer}</p>
                  </div>
                )}
              {busy && <Loading />}
            </div>
            <ErrorNotice error={error} recover={() => void read()} />
            {(uncertain || task?.proposal) && (
              <div className="composer-note">
                {uncertain
                  ? "请读取原任务状态后再继续，避免重复提交。"
                  : "此任务已生成申请。请跟进审批与执行结果，其他问题可新建对话。"}
              </div>
            )}
            <form className="composer" onSubmit={send}>
              <label className="sr-only" htmlFor="chat-message">
                售后消息
              </label>
              <textarea
                id="chat-message"
                value={text}
                onChange={(event) => setText(event.target.value)}
                maxLength={4000}
                rows={2}
                placeholder="输入订单号和售后问题…"
                disabled={busy || uncertain || !!task?.proposal}
              />
              <div className="row spread">
                <small>{text.length}/4000 · 提交后等待服务端处理</small>
                <button
                  className="button primary"
                  disabled={
                    busy || uncertain || !!task?.proposal || !text.trim()
                  }
                >
                  发送消息 ↑
                </button>
              </div>
            </form>
          </Panel>
          {task && (
            <Panel title="申请与任务状态">
              <TaskProgress task={task} />
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
function RequestForm({
  api,
  user,
  orderId,
  task,
  setTask,
}: {
  api: ApiClient;
  user: User;
  orderId: string;
  task?: Task;
  setTask: (task: Task | undefined) => void;
}) {
  const key = `aftersales.request.${user.username}`;
  const [requestId, setRequestId] = useState(
    () => sessionStorage.getItem(key) || "",
  );
  const [threadId, setThreadId] = useState(
    () => sessionStorage.getItem(`${key}.thread`) || "",
  );
  const [kind, setKind] = useState("refund");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function recover() {
    if (!requestId) return;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.get<Task>(requestReadPath(requestId, threadId));
      setTask(saved);
      setThreadId(saved.thread_id);
      sessionStorage.setItem(`${key}.thread`, saved.thread_id);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || requestId) return;
    const data = new FormData(event.currentTarget);
    const id = `req-${crypto.randomUUID()}`;
    setRequestId(id);
    sessionStorage.setItem(key, id);
    setBusy(true);
    setError(undefined);
    const body = {
      request_id: id,
      kind,
      order_id: String(data.get("order_id")).trim(),
      description: String(data.get("description")).trim(),
      ...(["refund", "return_refund"].includes(kind)
        ? { amount: String(data.get("amount")) }
        : {}),
      ...(kind === "reship" ? { quantity: Number(data.get("quantity")) } : {}),
    };
    try {
      const saved = await api.post<Task>("/requests", body);
      setTask(saved);
      setThreadId(saved.thread_id);
      sessionStorage.setItem(`${key}.thread`, saved.thread_id);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        eyebrow="NEW SERVICE REQUEST"
        title="提交售后申请"
        description="选择服务类型并填写真实诉求；提交后会保存申请并等待人工审核。"
      />
      <div className="two-column">
        <Panel title="申请信息">
          <form onSubmit={submit} className="request-form">
            <fieldset disabled={busy || !!requestId}>
              <label>
                订单编号
                <input
                  key={orderId}
                  name="order_id"
                  required
                  defaultValue={orderId}
                  maxLength={80}
                  placeholder="填写您的订单编号"
                />
              </label>
              <label>
                服务类型
                <select
                  value={kind}
                  onChange={(event) => setKind(event.target.value)}
                >
                  {["refund", "return_refund", "reship", "logistics"].map(
                    (value) => (
                      <option key={value} value={value}>
                        {kindName(value)}
                      </option>
                    ),
                  )}
                </select>
              </label>
              {["refund", "return_refund"].includes(kind) && (
                <label>
                  申请退款金额（元）
                  <input
                    name="amount"
                    type="number"
                    min="0.01"
                    step="0.01"
                    required
                    placeholder="0.00"
                  />
                  <small>实际金额以服务端可退余额与冻结审批参数为准。</small>
                </label>
              )}
              {kind === "reship" && (
                <label>
                  申请补发数量
                  <input
                    name="quantity"
                    type="number"
                    min="1"
                    max="1000"
                    step="1"
                    defaultValue="1"
                    required
                  />
                </label>
              )}
              <label>
                问题描述
                <textarea
                  name="description"
                  required
                  minLength={1}
                  maxLength={4000}
                  rows={5}
                  placeholder="请说明问题、期望的处理方式及必要的信息。"
                />
              </label>
              <button className="button primary full">
                {busy ? "正在提交并保存…" : "提交申请 →"}
              </button>
            </fieldset>
          </form>
          {requestId && (
            <div className="notice subtle">
              <strong>本次申请编号已保留</strong>
              <p className="mono">{requestId}</p>
              <div className="row wrap">
                <button
                  className="button small"
                  disabled={busy}
                  onClick={() => void recover()}
                >
                  读取已保存状态
                </button>
                <button
                  className="button small"
                  disabled={busy}
                  onClick={() => {
                    setRequestId("");
                    setThreadId("");
                    setTask(undefined);
                    setError(undefined);
                    sessionStorage.removeItem(key);
                    sessionStorage.removeItem(`${key}.thread`);
                  }}
                >
                  另建申请
                </button>
              </div>
              <small>
                另建前请先核对原任务与工单，避免对同一问题重复申请。
              </small>
            </div>
          )}
          <ErrorNotice error={error} recover={() => void recover()} />
        </Panel>
        <div>
          <Panel title="处理流程">
            <ol className="guide">
              <li>
                <span>01</span>
                <div>
                  <strong>保存申请与规则校验</strong>
                  <p>校验订单归属、时限、金额、数量和库存。</p>
                </div>
              </li>
              <li>
                <span>02</span>
                <div>
                  <strong>人工审核</strong>
                  <p>管理员核对冻结参数和风险提示，记录审批理由。</p>
                </div>
              </li>
              <li>
                <span>03</span>
                <div>
                  <strong>沙箱模拟执行</strong>
                  <p>仅在批准后执行；退货退款须先确认收货。</p>
                </div>
              </li>
            </ol>
          </Panel>
          {task && (
            <Panel title="已保存任务">
              <TaskProgress task={task} />
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
