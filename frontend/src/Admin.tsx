import { useState } from "react";
import { ApiClient } from "./api";
import { useResource } from "./hooks";
import type { Action, AuditEvent, Overview, Task } from "./types";
import {
  ActionDetails,
  Badge,
  date,
  Empty,
  ErrorNotice,
  kindName,
  Loading,
  Panel,
  pending,
  RawDetails,
  TaskProgress,
} from "./ui";
import { PageTitle } from "./Customer";
import { Tickets } from "./Tickets";

export function AdminWorkspace({
  api,
  page,
  navigate,
}: {
  api: ApiClient;
  page: string;
  navigate: (page: string) => void;
}) {
  return (
    <>
      {page === "overview" && <Dashboard api={api} navigate={navigate} />}
      {page === "approvals" && <Approvals api={api} />}
      {page === "tickets" && <Tickets api={api} admin />}
      {page === "audit" && <Approvals api={api} audit />}
    </>
  );
}
function Dashboard({
  api,
  navigate,
}: {
  api: ApiClient;
  navigate: (page: string) => void;
}) {
  const overview = useResource<Overview>(api, "/admin/overview");
  return (
    <>
      <PageTitle
        eyebrow="OPERATIONS OVERVIEW"
        title="服务运营总览"
        description="查看服务端任务、审批积压和失败告警，及时跟进需要人工处理的申请。"
        extra={
          <button className="button" onClick={() => void overview.refresh()}>
            ↻ 刷新总览
          </button>
        }
      />
      <ErrorNotice
        error={overview.error}
        recover={() => void overview.refresh()}
      />
      {overview.loading && !overview.data ? (
        <Loading />
      ) : (
        overview.data && (
          <>
            <div className="stats-grid">
              {[
                ["pending", "待审批申请", "等待人工审核"],
                ["completed", "已完成动作", "已保存业务结果"],
                ["failed", "失败动作", "需核对原任务"],
                ["tickets", "售后申请", "包含处理历史"],
              ].map(([key, title, subtitle]) => (
                <button
                  key={key}
                  className={`stat-card ${key === "pending" ? "featured" : ""}`}
                  onClick={() =>
                    navigate(
                      key === "tickets"
                        ? "tickets"
                        : key === "failed"
                          ? "audit"
                          : "approvals",
                    )
                  }
                >
                  <span>
                    {title}
                    <i>↗</i>
                  </span>
                  <strong>{overview.data?.counts[key] ?? "—"}</strong>
                  <small>{subtitle}</small>
                </button>
              ))}
            </div>
            <div className="two-column">
              <Panel
                title="需要关注"
                extra={
                  <span className="badge warn">
                    {overview.data.alerts?.length || 0} 条告警
                  </span>
                }
              >
                {overview.data.alerts?.length ? (
                  overview.data.alerts.map((alert, index) => (
                    <div className="alert-row" key={index}>
                      <span className="alert-icon">!</span>
                      <div>
                        <strong>
                          {String(
                            alert.message ||
                              alert.code ||
                              alert.status ||
                              "任务需要人工跟进",
                          )}
                        </strong>
                        <p className="mono">
                          {String(alert.thread_id || alert.action_id || "")}
                        </p>
                        <RawDetails title="告警详情" data={alert} />
                      </div>
                    </div>
                  ))
                ) : (
                  <Empty title="当前没有失败告警">
                    继续关注审批及退货收货进度。
                  </Empty>
                )}
                <button
                  className="button full"
                  onClick={() => navigate("audit")}
                >
                  查看审计与恢复 →
                </button>
              </Panel>
              <Panel title="业务概览">
                <dl className="overview-facts">
                  {Object.entries(overview.data.counts).map(([key, value]) => (
                    <div key={key}>
                      <dt>
                        {{
                          orders: "订单总数",
                          actions: "业务动作",
                          pending: "待审批",
                          completed: "已完成",
                          failed: "失败",
                          tickets: "售后申请",
                          tasks: "任务总数",
                          cases: "业务工单",
                          awaiting_return: "待收货",
                        }[key] || key}
                      </dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              </Panel>
            </div>
            <Panel title="最近任务">
              <TaskTable tasks={overview.data.tasks || []} />
            </Panel>
          </>
        )
      )}
    </>
  );
}
function TaskTable({ tasks }: { tasks: Task[] }) {
  return tasks.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务编号</th>
            <th>客户</th>
            <th>状态</th>
            <th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={`${task.customer_id}:${task.thread_id}`}>
              <td className="mono">{task.thread_id}</td>
              <td>{task.customer_id}</td>
              <td>
                <Badge status={task.status} />
              </td>
              <td>{date(task.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="暂无任务记录" />
  );
}
function Approvals({
  api,
  audit = false,
}: {
  api: ApiClient;
  audit?: boolean;
}) {
  const actions = useResource<Action[]>(api, "/approvals");
  const [selected, setSelected] = useState<Action>();
  const [filter, setFilter] = useState(audit ? "all" : "pending");
  const items =
    actions.data?.filter(
      (action) =>
        filter === "all" ||
        (filter === "pending" && pending(action.status)) ||
        (filter === "return" && action.status === "awaiting_return") ||
        (filter === "failed" && ["failed", "approved"].includes(action.status)),
    ) || [];
  return (
    <>
      <PageTitle
        eyebrow={audit ? "AUDIT & RECOVERY" : "APPROVAL INBOX"}
        title={audit ? "审计与恢复" : "申请审批"}
        description={
          audit
            ? "读取原任务和审计记录，沿用原动作恢复中断的执行或最终回复。"
            : "核对订单、金额、数量和冻结参数，填写理由后再作出审批决定。"
        }
        extra={
          <button className="button" onClick={() => void actions.refresh()}>
              <span aria-hidden="true">↻ </span>读取已保存状态
          </button>
        }
      />
      <ErrorNotice
        error={actions.error}
        recover={() => void actions.refresh()}
      />
      <div className="filter-bar" aria-label="申请状态筛选">
        {[
          ["pending", "待审批"],
          ["return", "待退货收货"],
          ["failed", "失败 / 待恢复"],
          ["all", "全部记录"],
        ].map(([value, title]) => (
          <button
            key={value}
            className={filter === value ? "selected" : ""}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {title}
            {value === "pending" && (
              <span>
                {actions.data?.filter((action) => pending(action.status))
                  .length || 0}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="approval-layout">
        <Panel className="approval-list" title={`申请列表 · ${items.length}`}>
          {actions.loading && !actions.data ? (
            <Loading />
          ) : items.length ? (
            items.map((action) => (
              <button
                key={action.action_id}
                className={`approval-item ${selected?.action_id === action.action_id ? "selected" : ""}`}
                onClick={() => setSelected(action)}
              >
                <div className="row spread">
                  <strong>{kindName(action.kind)}</strong>
                  <Badge status={action.status} />
                </div>
                <h3>{action.order_id}</h3>
                <p>{action.description}</p>
                <small>
                  {action.customer_id} · 审批版本 v{action.version}
                </small>
              </button>
            ))
          ) : (
            !actions.error && <Empty title="当前筛选下没有申请" />
          )}
        </Panel>
        {selected ? (
          <Review
            key={selected.action_id}
            api={api}
            initial={
              actions.data?.find(
                (action) => action.action_id === selected.action_id,
              ) || selected
            }
            refresh={() => void actions.refresh()}
          />
        ) : (
          <Panel className="review-empty">
            <Empty title="选择一项申请查看详情">
              在左侧打开申请，核对冻结参数、审批记录和真实执行状态。
            </Empty>
          </Panel>
        )}
      </div>
    </>
  );
}
function Review({
  api,
  initial,
  refresh,
}: {
  api: ApiClient;
  initial: Action;
  refresh: () => void;
}) {
  const [reason, setReason] = useState("");
  const [task, setTask] = useState<Task>();
  const [events, setEvents] = useState<AuditEvent[]>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [action, setAction] = useState(initial);
  async function read() {
    setBusy(true);
    setError(undefined);
    try {
      const results = await Promise.allSettled([
        api.get<Task>(
          `/tasks/${encodeURIComponent(action.thread_id)}?customer_id=${encodeURIComponent(action.customer_id)}`,
        ),
        api.get<AuditEvent[]>(
          `/actions/${encodeURIComponent(action.action_id)}/events`,
        ),
        api.get<Action[]>("/approvals"),
      ]);
      if (results[0].status === "fulfilled") setTask(results[0].value);
      if (results[1].status === "fulfilled") setEvents(results[1].value);
      if (results[2].status === "fulfilled") {
        const saved = results[2].value.find(
          (item) => item.action_id === action.action_id,
        );
        if (saved) setAction(saved);
      }
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
      setUncertain(false);
      refresh();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  async function mutate(operation: "approve" | "reject" | "retry" | "receive") {
    if (
      busy ||
      uncertain ||
      (["approve", "reject"].includes(operation) && !reason.trim())
    )
      return;
    setBusy(true);
    setError(undefined);
    setUncertain(true);
    const route =
      operation === "receive"
        ? `/returns/${action.action_id}/receive`
        : `/approvals/${action.action_id}/${operation === "retry" ? "retry" : "decision"}`;
    const body = ["approve", "reject"].includes(operation)
      ? {
          approved: operation === "approve",
          version: action.version,
          reason: reason.trim(),
        }
      : undefined;
    try {
      const result = await api.post<Task>(route, body);
      setTask(result);
      if (result.proposal) setAction(result.proposal);
      // Always re-read after writes; never infer business success from HTTP alone.
      const saved = await api.get<Action[]>("/approvals");
      const updated = saved.find((item) => item.action_id === action.action_id);
      if (updated) setAction(updated);
      setUncertain(false);
      refresh();
      setReason("");
      setEvents(
        await api.get<AuditEvent[]>(`/actions/${action.action_id}/events`),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="审核详情"
      extra={
        <button
          className="button small"
          disabled={busy}
          onClick={() => void read()}
        >
          读取原任务与审计
        </button>
      }
    >
      <ActionDetails action={action} />
      <ErrorNotice error={error} recover={() => void read()} />
      {uncertain && (
        <div className="notice warning">
          上次提交结果尚未完全核对。请先读取原任务与审计，再决定下一步。
        </div>
      )}
      {pending(action.status) && (
        <div className="review-box">
          <h3>记录审核决定</h3>
          <p>
            批准仅执行已保存的参数。高风险或超额申请应结合冻结规则和人工审查结论处理。
          </p>
          <label>
            审批理由（必填）
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={1000}
              rows={3}
              placeholder="说明核对依据与批准 / 拒绝原因"
              disabled={busy || uncertain}
            />
          </label>
          <div className="row wrap">
            <button
              className="button primary"
              disabled={busy || uncertain || !reason.trim()}
              onClick={() => void mutate("approve")}
            >
              {busy ? "保存中…" : "批准并继续处理"}
            </button>
            <button
              className="button danger"
              disabled={busy || uncertain || !reason.trim()}
              onClick={() => void mutate("reject")}
            >
              拒绝申请
            </button>
          </div>
        </div>
      )}
      {action.status === "awaiting_return" && (
        <div className="review-box">
          <h3>退货收货确认</h3>
          <p>
            仅在确认该申请对应退货已收到后操作。确认后按批准时冻结的金额执行模拟退款。
          </p>
          <button
            className="button primary"
            disabled={busy || uncertain}
            onClick={() => void mutate("receive")}
          >
            确认退货已收到并模拟退款
          </button>
        </div>
      )}
      {["approved", "completed", "failed", "awaiting_return"].includes(action.status) && (
        <div className="review-box">
          <h3>恢复已批准的原动作</h3>
          <p>
            沿用动作编号和已保存业务结果；请先读取结果，再恢复中断执行或最终回复。
          </p>
          <button
            className="button"
            disabled={busy || uncertain}
            onClick={() => void mutate("retry")}
          >
            恢复原动作 / 最终回复
          </button>
        </div>
      )}
      {busy && <Loading />}
      {task && (
        <div className="detail-divider">
          <TaskProgress task={task} />
        </div>
      )}
      {events && (
        <div className="detail-divider">
          <h3>业务审计轨迹</h3>
          {events.length ? (
            <ol className="timeline">
              {events.map((event) => (
                <li key={event.event_id}>
                  <div className="row spread">
                    <strong>{event.event_type}</strong>
                    <small>{date(event.created_at)}</small>
                  </div>
                  <p>操作者：{event.actor}</p>
                  <RawDetails title="审计内容" data={event.details} />
                </li>
              ))}
            </ol>
          ) : (
            <Empty title="暂无审计事件" />
          )}
        </div>
      )}
    </Panel>
  );
}
