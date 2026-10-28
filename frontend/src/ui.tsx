import type { ReactNode } from "react";
import type { Action, JsonRecord, Task } from "./types";
import { ApiError } from "./api";

const statuses: Record<string, string> = {
  paid: "已付款",
  shipped: "已发货",
  delivered: "已签收",
  cancelled: "已取消",
  pending: "待审批",
  pending_approval: "待审批",
  awaiting_approval: "等待审批",
  waiting_approval: "等待审批",
  awaiting_return: "等待退货收货",
  approved: "已批准",
  rejected: "已拒绝",
  completed: "已完成",
  succeeded: "业务已执行",
  executed: "业务已执行",
  failed: "处理失败",
  reply_failed: "业务已保存 · 回复失败",
  execution_failed: "业务执行失败 · 需核对原动作",
  recovery_required: "业务已保存 · 任务需要恢复",
  running: "处理中",
  ready: "可以继续补充",
  needs_input: "需要补充信息",
  human_required: "需要人工处理",
  open: "待处理",
  processing: "处理中",
  resolved: "已解决",
  closed: "已关闭",
  in_transit: "运输中",
  received: "已收货",
  not_required: "无需退货",
};
export const kindName = (kind: string) =>
  ({
    refund: "仅退款",
    reship: "申请补发",
    return_refund: "退货退款",
    logistics: "物流问题",
  })[kind] || kind;
export const statusName = (status: string) => statuses[status] || status || "—";
export const money = (value: unknown) =>
  value == null ? "—" : `¥${String(value)}`;
export const date = (value: unknown) =>
  typeof value === "string" && !Number.isNaN(Date.parse(value))
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "—";
export const pending = (status: string) =>
  [
    "pending",
    "pending_approval",
    "awaiting_approval",
    "waiting_approval",
  ].includes(status);
export function Badge({ status }: { status: string }) {
  return (
    <span
      className={`badge ${["failed", "reply_failed", "rejected"].includes(status) ? "bad" : pending(status) || ["running", "awaiting_return", "human_required"].includes(status) ? "warn" : ""}`}
    >
      {statusName(status)}
    </span>
  );
}
export function ErrorNotice({
  error,
  recover,
}: {
  error: unknown;
  recover?: () => void;
}) {
  if (!error) return null;
  const apiError =
    error instanceof ApiError
      ? error
      : new ApiError(
          "CLIENT_ERROR",
          error instanceof Error ? error.message : "操作未完成",
        );
  return (
    <div className="notice error" role="alert">
      <strong>{apiError.message}</strong>
      <code>{apiError.code}</code>
      <p>如果刚才提交过申请，请先读取已保存状态，核对原任务和执行结果。</p>
      {recover && (
        <button className="button small" onClick={recover}>
          读取已保存状态
        </button>
      )}
    </div>
  );
}
export function Empty({
  title = "暂无记录",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span aria-hidden="true">◇</span>
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      正在读取服务端数据…
    </div>
  );
}
export function Panel({
  title,
  extra,
  children,
  className = "",
}: {
  title?: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {extra}
        </div>
      )}
      {children}
    </section>
  );
}
export function RawDetails({
  title = "结构化详情",
  data,
}: {
  title?: string;
  data: unknown;
}) {
  return (
    <details className="raw">
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
export function ActionDetails({ action }: { action: Action }) {
  return (
    <div className="action-details">
      <div className="row spread">
        <h3>{kindName(action.kind)}</h3>
        <Badge status={action.status} />
      </div>
      <dl className="facts">
        <div>
          <dt>订单编号</dt>
          <dd>{action.order_id}</dd>
        </div>
        <div>
          <dt>申请编号</dt>
          <dd className="mono">{action.ticket_id || action.action_id}</dd>
        </div>
        <div>
          <dt>客户</dt>
          <dd>{action.customer_id}</dd>
        </div>
        <div>
          <dt>审批版本</dt>
          <dd>{action.version ? `v${action.version}` : "—"}</dd>
        </div>
        {action.amount != null && (
          <div>
            <dt>申请退款</dt>
            <dd>{money(action.amount)}</dd>
          </div>
        )}
        {action.kind === "reship" && (
          <div>
            <dt>补发数量</dt>
            <dd>{action.quantity} 件</dd>
          </div>
        )}
        {action.risk_level && (
          <div>
            <dt>风险级别</dt>
            <dd>
              {action.risk_level}
              {action.manual_review ? " · 需人工审核" : ""}
            </dd>
          </div>
        )}
        {action.return_status && (
          <div>
            <dt>退货状态</dt>
            <dd>{statusName(action.return_status)}</dd>
          </div>
        )}
      </dl>
      <p className="preserve">{action.description}</p>
      {action.approval_review && (
        <RawDetails title="审批记录与冻结参数" data={action.approval_review} />
      )}
      {action.result && (
        <div
          className={action.result.error ? "notice error" : "notice success"}
        >
          <strong>
            {action.result.error
              ? `业务未执行：${String(action.result.error)}`
              : "已保存的沙箱业务结果"}
          </strong>
          <RawDetails title="查看执行结果" data={action.result} />
        </div>
      )}
    </div>
  );
}
export function TaskProgress({ task }: { task: Task }) {
  const action = task.proposal;
  return (
    <>
      <div className="row spread">
        <span className="eyebrow">任务进度</span>
        <Badge status={task.status} />
      </div>
      <p className="mono muted">{task.thread_id}</p>
      <ol className="progress">
        <li className="active">任务已保存</li>
        <li className={action ? "active" : ""}>规则与参数校验</li>
        <li className={action && !pending(action.status) ? "active" : ""}>
          人工审批
        </li>
        <li className={action?.result && !action.result.error ? "active" : ""}>
          模拟执行
        </li>
      </ol>
      {action && pending(action.status) && (
        <div className="notice warning">
          申请已保存，正在等待人工审批；退款、补发尚未执行。
        </div>
      )}
      {(task.status === "awaiting_return" ||
        action?.status === "awaiting_return") && (
        <div className="notice warning">
          退货已获批准，等待管理员确认实物收货后才会模拟退款。
        </div>
      )}
      {task.status === "reply_failed" && (
        <div className="notice warning">
          最终回复未完成。请核对已保存业务结果，由管理员恢复原动作。
        </div>
      )}
      {task.status === "recovery_required" && (
        <div className="notice warning">
          业务结果已经保存，任务通知尚需恢复。请由管理员恢复原动作，不要重新提交申请。
        </div>
      )}
      {action && <ActionDetails action={action} />}
      <RawDetails
        title="工具轨迹与运行指标"
        data={{
          next_nodes: task.next_nodes,
          metrics: task.metrics,
          traces: task.messages?.filter(
            (item) => item.type === "tool" || item.tool_calls?.length,
          ),
        }}
      />
    </>
  );
}
export function RecordFacts({ record }: { record: JsonRecord }) {
  return <RawDetails data={record} />;
}
