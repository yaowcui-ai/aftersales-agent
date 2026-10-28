import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ApiClient, ApiError, loadSession, saveSession } from "./api";
import type { Session, User } from "./types";
import { ErrorNotice } from "./ui";
import { CustomerWorkspace } from "./Customer";
import { AdminWorkspace } from "./Admin";

function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [role, setRole] = useState("customer");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(undefined);
    try {
      const result = await new ApiClient().post<Session>("/auth/login", {
        username: String(form.get("username")).trim(),
        password: form.get("password"),
      });
      if (
        !result.token ||
        !["customer", "approver"].includes(result.user?.role)
      )
        throw new ApiError("INVALID_LOGIN_RESPONSE", "登录响应不完整。");
      if (result.user.role !== role)
        throw new ApiError(
          "ROLE_MISMATCH",
          "该账号与所选工作台角色不符，请选择对应入口。",
        );
      onLogin(result);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-screen">
      <section className="login-story">
        <a className="brand" href="#">
          <span className="brand-mark">循</span>循服 <small>AFTERCARE</small>
        </a>
        <div>
          <span className="eyebrow">SERVICE, WITH CLARITY</span>
          <h1>
            让每一次售后，
            <br />
            都有清晰的回应。
          </h1>
          <p>
            订单查询、申请审批、执行结果。
            <br />
            在同一条服务链路中，安心跟进每一步。
          </p>
          <div className="login-steps">
            <span>01 / 提交诉求</span>
            <span>02 / 人工审核</span>
            <span>03 / 跟进结果</span>
          </div>
        </div>
        <small>本地沙箱 · 所有退款、补发和物流操作均为模拟业务</small>
      </section>
      <main className="login-main">
        <div className="login-card">
          <span className="eyebrow">WELCOME BACK</span>
          <h2>登录服务工作台</h2>
          <p className="muted">使用已配置的演示账号继续。</p>
          <div className="segmented" aria-label="选择工作台">
            {[
              ["customer", "客户服务"],
              ["approver", "管理员审批"],
            ].map(([value, text]) => (
              <button
                key={value}
                type="button"
                aria-pressed={role === value}
                className={role === value ? "selected" : ""}
                onClick={() => setRole(value)}
              >
                {text}
              </button>
            ))}
          </div>
          <form onSubmit={submit}>
            <label>
              用户名
              <input
                name="username"
                autoComplete="username"
                required
                maxLength={80}
                placeholder="输入账号"
                disabled={busy}
              />
            </label>
            <label>
              密码
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                maxLength={200}
                placeholder="输入密码"
                disabled={busy}
              />
            </label>
            <ErrorNotice error={error} />
            <button className="button primary full" disabled={busy}>
              {busy ? "正在验证…" : "登录工作台 →"}
            </button>
          </form>
          <p className="login-note">
            账号权限由服务端校验。会话仅保留在当前浏览器标签页。
          </p>
        </div>
      </main>
    </div>
  );
}
export default function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [page, setPage] = useState(() => window.location.hash.slice(1));
  const [authError, setAuthError] = useState<unknown>();
  const api = useMemo(() => new ApiClient(session?.token), [session?.token]);
  useEffect(() => {
    window.history.replaceState(null, "", window.location.pathname + window.location.search + (page ? `#${page}` : ""));
  }, [page]);
  useEffect(() => {
    if (!session) return;
    let active = true;
    api
      .get<User>("/me")
      .then((user) => {
        if (
          active &&
          (user.username !== session.user.username ||
            user.role !== session.user.role)
        ) {
          saveSession(null);
          setSession(null);
        }
      })
      .catch((error) => {
        if (active) {
          if (error instanceof ApiError && error.status === 401) {
            saveSession(null);
            setSession(null);
          } else setAuthError(error);
        }
      });
    return () => {
      active = false;
    };
  }, [api, session]);
  function login(next: Session) {
    saveSession(next);
    setSession(next);
    setPage("");
    setAuthError(undefined);
  }
  function logout() {
    saveSession(null);
    setSession(null);
    setAuthError(undefined);
    setPage("");
  }
  if (!session) return <Login onLogin={login} />;
  const admin = session.user.role === "approver";
  const nav = admin
    ? [
        ["overview", "◫", "工作台总览"],
        ["approvals", "✓", "申请审批"],
        ["tickets", "▤", "售后工单"],
        ["audit", "≋", "审计与恢复"],
      ]
    : [
        ["orders", "▦", "我的订单"],
        ["chat", "◌", "智能售后"],
        ["request", "＋", "提交申请"],
        ["tickets", "▤", "申请与工单"],
      ];
  const active = nav.some(([id]) => id === page) ? page : nav[0][0];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setPage(nav[0][0]);
          }}
        >
          <span className="brand-mark">循</span>循服 <small>AFTERCARE</small>
        </a>
        <p className="nav-label">
          {admin ? "OPERATIONS / 管理员" : "WORKSPACE / 客户服务"}
        </p>
        <nav aria-label={admin ? "管理员导航" : "客户导航"}>
          {nav.map(([id, icon, title]) => (
            <button
              key={id}
              className={active === id ? "active" : ""}
              aria-current={active === id ? "page" : undefined}
              onClick={() => setPage(id)}
            >
              <span aria-hidden="true">{icon}</span>
              {title}
              {active === id && <i />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sandbox-side">
            <span className="status-dot" />
            本地沙箱环境<p>退款与补发均为模拟执行</p>
          </div>
          <div className="user-info">
            <span className="avatar">
              {session.user.username.slice(0, 1).toUpperCase()}
            </span>
            <div>
              <strong>{session.user.username}</strong>
              <small>{admin ? "审批管理员" : session.user.customer_id}</small>
            </div>
            <button onClick={logout} aria-label="退出登录" title="退出登录">
              ↗
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div>
            <span className="muted">{admin ? "运营管理" : "客户服务"}</span>
            <span className="slash">/</span>
            <strong>{nav.find((item) => item[0] === active)?.[2]}</strong>
          </div>
          <div className="row">
            <span className="sandbox-pill">● 沙箱模拟业务</span>
            <button
              className="mobile-logout"
              onClick={logout}
              aria-label="退出登录"
            >
              退出
            </button>
          </div>
        </header>
        <main className="main-content">
          <ErrorNotice error={authError} />
          {admin ? (
            <AdminWorkspace
              key={session.user.username}
              api={api}
              page={active}
              navigate={setPage}
            />
          ) : (
            <CustomerWorkspace
              key={session.user.username}
              api={api}
              page={active}
              user={session.user}
              navigate={setPage}
            />
          )}
        </main>
        <footer className="footer">
          循服 AFTERCARE <span>每项业务写入均经人工审批 · 服务端记录为准</span>
        </footer>
      </div>
    </div>
  );
}
