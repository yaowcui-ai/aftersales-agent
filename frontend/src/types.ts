export type JsonRecord = Record<string, unknown>;
export interface User {
  username: string;
  role: "customer" | "approver";
  customer_id: string;
}
export interface Session {
  token: string;
  user: User;
}
export interface Order extends JsonRecord {
  order_id: string;
  sku: string;
  quantity: number;
  status: string;
  paid_amount: string;
  refunded_amount: string;
  reshipped_quantity: number;
  policy?: JsonRecord;
}
export interface Shipment extends JsonRecord {
  shipment_id: string;
  carrier: string;
  tracking_number: string;
  status: string;
  description: string;
  updated_at: string;
}
export interface Action extends JsonRecord {
  action_id: string;
  ticket_id?: string;
  customer_id: string;
  thread_id: string;
  kind: string;
  order_id: string;
  description: string;
  status: string;
  version: number;
  amount?: string;
  quantity?: number;
  result?: JsonRecord;
  risk_level?: string;
  manual_review?: boolean;
  return_status?: string;
  approval_review?: JsonRecord;
}
export interface Message {
  type: string;
  content: unknown;
  tool_calls?: unknown[];
}
export interface Task {
  thread_id: string;
  customer_id: string;
  status: string;
  messages?: Message[];
  final_answer?: string;
  proposal?: Action;
  next_nodes?: string[];
  metrics?: JsonRecord;
  updated_at?: string;
}
export interface Ticket extends Action {
  case_id?: string;
  created_at?: string;
  updated_at?: string;
}
export interface Case extends JsonRecord {
  case_id: string;
  action_id: string;
  order_id: string;
  kind: string;
  description: string;
  status: string;
  resolution?: string;
  updated_at: string;
}
export interface AuditEvent extends JsonRecord {
  event_id: string;
  event_type: string;
  actor: string;
  created_at: string;
  details: JsonRecord;
}
export interface Overview {
  counts: Record<string, number>;
  tasks: Task[];
  alerts: JsonRecord[];
}
