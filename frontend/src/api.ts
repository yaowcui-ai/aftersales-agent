import type { Session } from "./types";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export class ApiClient {
  constructor(private token?: string) {}
  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }
  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(
      () => controller.abort(),
      method === "GET" ? 30000 : 180000,
    );
    try {
      const response = await fetch(`/api${path}`, {
        method,
        cache: "no-store",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const detail = payload?.detail;
        throw new ApiError(
          typeof detail?.code === "string"
            ? detail.code
            : `HTTP_${response.status}`,
          typeof detail?.message === "string"
            ? detail.message
            : response.status === 401
              ? "登录已失效，请退出后重新登录。"
              : "服务未完成请求，请读取已保存状态。",
          response.status,
        );
      }
      if (payload === null)
        throw new ApiError(
          "INVALID_RESPONSE",
          "服务响应格式异常，请读取已保存状态。",
          response.status,
        );
      return payload as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        "NETWORK_UNCERTAIN",
        "连接中断或请求超时，提交结果尚未确认。请读取已保存状态。",
      );
    } finally {
      clearTimeout(timer);
    }
  }
}
export const SESSION_KEY = "aftersales.session";
export function requestReadPath(requestId: string, threadId?: string): string {
  return threadId
    ? `/tasks/${encodeURIComponent(threadId)}`
    : `/requests/${encodeURIComponent(requestId)}`;
}
export function loadSession(): Session | null {
  try {
    const session = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    return typeof session?.token === "string" &&
      typeof session?.user?.username === "string" &&
      ["customer", "approver"].includes(session.user.role)
      ? session
      : null;
  } catch {
    return null;
  }
}
export function saveSession(session: Session | null) {
  if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else {
    for (const key of Object.keys(sessionStorage))
      if (key.startsWith("aftersales.")) sessionStorage.removeItem(key);
  }
}
export function list<T>(
  payload: T[] | { items?: T[]; [key: string]: unknown },
  key: string,
): T[] {
  if (Array.isArray(payload)) return payload;
  return (
    Array.isArray(payload[key])
      ? payload[key]
      : Array.isArray(payload.items)
        ? payload.items
        : []
  ) as T[];
}
