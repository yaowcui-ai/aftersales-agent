import { useState, type FormEvent } from "react";
import { ApiClient } from "./api";
import { useResource } from "./hooks";
import type { Action, Case, Task, Ticket } from "./types";
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

export function Tickets({ api, admin }: { api: ApiClient; admin: boolean }) {
  const tickets = useResource<Ticket[]>(api, "/tickets");
  const cases = useResource<Case[]>(api, "/cases");
  const [selected, setSelected] = useState("");
  return (
    <>
      <PageTitle
        eyebrow="SERVICE RECORDS"
        title={admin ? "售后工单" : "申请与工单"}
        description="区分待审核申请与已创建工单，核对每项申请的执行结果。"
        extra={
          <button
            className="button"
            onClick={() => {
              void tickets.refresh();
              void cases.refresh();
            }}
          >
            ↻ 刷新记录
          </button>
        }
      />
      <ErrorNotice
        error={tickets.error}
        recover={() => void tickets.refresh()}
      />
      <Panel title="售后申请">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>申请 / 订单</th>
                <th>服务类型</th>
                <th>处理状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tickets.data?.map((ticket) => (
                <tr key={ticket.ticket_id || ticket.action_id}>
                  <td>
                    <strong className="mono">{ticket.order_id}</strong>
                    <small className="mono">
                      {ticket.ticket_id || ticket.action_id}
                    </small>
                  </td>
                  <td>{kindName(ticket.kind)}</td>
                  <td>
                    <Badge status={ticket.status} />
                  </td>
                  <td>{date(ticket.created_at)}</td>
                  <td>
                    <button
                      className="button small"
                      onClick={() =>
                        setSelected(ticket.ticket_id || ticket.action_id)
                      }
                    >
                      查看申请
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {tickets.loading && !tickets.data ? (
          <Loading />
        ) : (
          tickets.data?.length === 0 && (
            <Empty title="暂无售后申请">
              提交的申请会在这里保留，包括审批中和已拒绝记录。
            </Empty>
          )
        )}
      </Panel>
      {selected && (
        <TicketDetails
          key={selected}
          api={api}
          id={selected}
          admin={admin}
          updated={() => void tickets.refresh()}
          close={() => setSelected("")}
        />
      )}
      <ErrorNotice error={cases.error} recover={() => void cases.refresh()} />
      <Panel title="已创建的业务工单">
        <p className="panel-description">
          申请获批并进入执行流程后形成的业务记录。具体结果以工单及原动作状态为准。
        </p>
        {cases.loading && !cases.data ? (
          <Loading />
        ) : cases.data?.length ? (
          <div className="case-grid">
            {cases.data.map((item) => (
              <CaseCard
                key={item.case_id}
                api={api}
                initial={item}
                admin={admin}
              />
            ))}
          </div>
        ) : (
          !cases.error && <Empty title="暂无已创建的工单" />
        )}
      </Panel>
    </>
  );
}
function TicketDetails({
  api,
  id,
  admin,
  updated,
  close,
}: {
  api: ApiClient;
  id: string;
  admin: boolean;
  updated: () => void;
  close: () => void;
}) {
  const ticket = useResource<Ticket>(api, `/tickets/${encodeURIComponent(id)}`);
  const [task, setTask] = useState<Task>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function readTask() {
    if (!ticket.data) return;
    setBusy(true);
    setError(undefined);
    try {
      setTask(
        await api.get<Task>(
          `/tasks/${encodeURIComponent(ticket.data.thread_id)}${admin ? `?customer_id=${encodeURIComponent(ticket.data.customer_id)}` : ""}`,
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  async function cancel() {
    if (!ticket.data || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.post<{ action: Action }>(
        `/actions/${encodeURIComponent(ticket.data.action_id)}/cancel`,
      );
      await ticket.refresh();
      updated();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel
      title="申请详情"
      extra={
        <button className="button small" onClick={close}>
          收起详情
        </button>
      }
    >
      <ErrorNotice
        error={ticket.error || error}
        recover={() => void ticket.refresh()}
      />
      {ticket.loading && !ticket.data ? (
        <Loading />
      ) : (
        ticket.data && (
          <>
            <ActionDetails action={ticket.data} />
            <div className="row wrap">
              <button
                className="button"
                disabled={busy}
                onClick={() => void readTask()}
              >
                读取任务与执行进度
              </button>
              <button
                className="button"
                disabled={busy || ticket.loading}
                onClick={() => void ticket.refresh()}
              >
                刷新申请状态
              </button>
              {!admin && pending(ticket.data.status) && (
                <button
                  className="button danger"
                  disabled={busy}
                  onClick={() => void cancel()}
                >
                  取消此申请
                </button>
              )}
            </div>
            {task && (
              <div className="detail-divider">
                <TaskProgress task={task} />
              </div>
            )}
          </>
        )
      )}
    </Panel>
  );
}
function CaseCard({
  api,
  initial,
  admin,
}: {
  api: ApiClient;
  initial: Case;
  admin: boolean;
}) {
  const [details, setDetails] = useState<Case>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const item = details || initial;
  async function read() {
    setBusy(true);
    setError(undefined);
    try {
      setDetails(await api.get<Case>(`/cases/${item.case_id}`));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  async function update(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(undefined);
    try {
      const result = await api.post<Case>(`/cases/${item.case_id}/status`, {
        status: data.get("status"),
        resolution: String(data.get("resolution")).trim(),
      });
      setDetails(result);
      setEditing(false);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="case-card">
      <div className="row spread">
        <span className="eyebrow">{kindName(item.kind)}</span>
        <Badge status={item.status} />
      </div>
      <h3>{item.order_id}</h3>
      <p className="mono muted">{item.case_id}</p>
      <p className="preserve">{item.description}</p>
      {item.resolution && (
        <div className="notice success">{item.resolution}</div>
      )}
      <small className="muted">更新于 {date(item.updated_at)}</small>
      <div className="row wrap">
        <button
          className="button small"
          disabled={busy}
          onClick={() => void read()}
        >
          读取工单结果
        </button>
        {admin && (
          <button className="button small" onClick={() => setEditing(!editing)}>
            更新进度
          </button>
        )}
      </div>
      <ErrorNotice error={error} recover={() => void read()} />
      {details && <RawDetails title="完整工单记录" data={details} />}
      {editing && (
        <form onSubmit={update}>
          <label>
            处理状态
            <select name="status" defaultValue="processing">
              <option value="processing">处理中</option>
              <option value="resolved">已解决</option>
              <option value="closed">已关闭</option>
            </select>
          </label>
          <label>
            处理说明
            <textarea name="resolution" required maxLength={4000} rows={3} />
          </label>
          <button className="button primary small" disabled={busy}>
            保存处理进度
          </button>
        </form>
      )}
    </article>
  );
}
