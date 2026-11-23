"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  BookOpen,
  Boxes,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  FileCheck2,
  FileText,
  Filter,
  Globe2,
  Inbox,
  LayoutDashboard,
  LifeBuoy,
  Loader2,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Package,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Upload,
  Users,
  X,
} from "lucide-react";

type Item = { id: string; [key: string]: unknown };
type CaseItem = Item & {
  subject: string;
  customer_name: string;
  kind: string;
  status: string;
  priority: string;
  order_id: string;
  sku: string;
  product_title: string;
  created_at: number;
  version: number;
};
type Plan = Item & {
  kind: string;
  version: number;
  quantity: number;
  amount_minor: number;
  rationale: string;
  currency: string;
};
type Action = Item & {
  kind: string;
  status: string;
  request_id: string;
  last_sequence: number;
};
type Detail = CaseItem & {
  order: Item;
  line: Item;
  plans: Plan[];
  actions: Action[];
  messages: Item[];
  choices: Item[];
  approvals: Item[];
  attachments: Item[];
  notifications: Item[];
  runs?: (Item & { steps: Item[] })[];
  audit?: Item[];
};
type Me = {
  id: string;
  name: string;
  roles: string[];
  tenant_id: string;
  store_id: string;
  customer_id: string | null;
  environment: string;
};
type Stats = {
  open_cases: number;
  waiting_approval: number;
  case_count: number;
  unknown_actions: number;
  order_count: number;
  issue_order_count: number;
  issue_rate: number | null;
  refunded_minor: number | null;
  currency: string;
  by_kind: Record<string, number>;
};
type Capabilities = {
  business: string;
  model: string;
  database: string;
  retrieval: string;
  integrations: { name: string; status: string }[];
};
const kindNames: Record<string, string> = {
  replacement: "破损补发",
  refund: "退款申请",
  return: "退货处理",
  delivery: "物流异常",
  cancellation: "取消订单",
  address: "修改地址",
  missing: "少件处理",
  wrong: "错件处理",
};
const statusNames: Record<string, string> = {
  open: "待处理",
  waiting_customer: "待客户确认",
  waiting_approval: "待审核",
  approved: "已批准",
  processing: "处理中",
  awaiting_fulfillment: "待履约",
  awaiting_resolution: "待确认解决",
  needs_review: "待复核",
  resolved: "已解决",
  closed: "已结案",
  prepared: "已准备",
  unknown: "结果未知",
  accepted: "已受理",
  shipped: "已出库",
  delivered: "已送达",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  received: "已收货",
  inspected: "已质检",
  investigated: "调查完成",
  waiting: "等待中",
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  draft: "草稿",
  published: "已发布",
  withdrawn: "已撤回",
  pending: "待处理",
  dead: "需人工处理",
};
const roleNames: Record<string, string> = {
  supervisor: "总调度",
  facts: "事实核查",
  rules: "规则检索",
  proposal: "处理方案",
  reply: "回复草稿",
};
const navigation = [
  { id: "overview", label: "工作概览", icon: LayoutDashboard },
  { id: "cases", label: "售后工单", icon: Inbox },
  { id: "approvals", label: "审批中心", icon: ShieldCheck },
  { id: "knowledge", label: "知识与政策", icon: BookOpen },
  { id: "operations", label: "经营分析", icon: TrendingUp },
  { id: "inventory", label: "库存台账", icon: Boxes },
  { id: "improvements", label: "整改与索赔", icon: FileCheck2 },
  { id: "jobs", label: "运行记录", icon: Activity },
];

async function api<T>(
  path: string,
  body?: unknown,
  method = "POST",
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error?.message || "请求未完成，请重试");
  return result;
}
const text = (v: unknown) => (v === null || v === undefined ? "—" : String(v));
const date = (n: unknown) =>
  new Date(Number(n) * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
const money = (n: number, currency = "GBP") =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(
    n / 100,
  );
function Badge({ status }: { status: string }) {
  return (
    <span className={`badge badge-${status}`}>
      {statusNames[status] || status}
    </span>
  );
}

export default function Workspace() {
  const [me, setMe] = useState<Me | null>(null),
    [checking, setChecking] = useState(true);
  const [page, setPage] = useState("overview"),
    [cases, setCases] = useState<CaseItem[]>([]);
  const [stats, setStats] = useState<Stats | null>(null),
    [caps, setCaps] = useState<Capabilities | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null),
    [tab, setTab] = useState("overview");
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all");
  const [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("demo"),
    [password, setPassword] = useState("");
  const [items, setItems] = useState<Item[]>([]),
    [claims, setClaims] = useState<Item[]>([]);
  const [rules, setRules] = useState<Item[]>([]),
    [facts, setFacts] = useState<Record<string, unknown> | null>(null);
  const [modal, setModal] = useState(""),
    [orders, setOrders] = useState<(Item & { lines: Item[] })[]>([]);
  const [form, setForm] = useState<Record<string, string>>({}),
    [message, setMessage] = useState("");
  const allowed = (...roles: string[]) =>
    Boolean(me?.roles.some((role) => roles.includes(role)));

  const reload = useCallback(async () => {
    const [list, currentCaps] = await Promise.all([
      api<CaseItem[]>("/cases"),
      api<Capabilities>("/capabilities"),
    ]);
    setCases(list);
    setCaps(currentCaps);
    if (!me?.customer_id) setStats(await api<Stats>("/operations"));
  }, [me?.customer_id]);
  useEffect(() => {
    api<Me>("/me")
      .then(setMe)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (me) reload().catch((e) => setError(e.message));
  }, [me, reload]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!me) return;
    const path: Record<string, string> = {
      knowledge: "/policies",
      inventory: "/inventory",
      jobs: "/jobs",
      improvements: "/improvements",
    };
    setItems([]);
    if (path[page])
      api<Item[]>(path[page])
        .then(setItems)
        .catch((e) => setError(e.message));
    if (page === "improvements" && allowed("operations"))
      api<Item[]>("/claims")
        .then(setClaims)
        .catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, me]);

  async function openCase(id: string) {
    setError("");
    setFacts(null);
    setRules([]);
    setTab("overview");
    try {
      setDetail(await api<Detail>(`/cases/${id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function act(
    path: string,
    body: unknown,
    success = "操作已保存",
    method = "POST",
  ) {
    setBusy(true);
    setError("");
    try {
      await api(path, body, method);
      setToast(success);
      await reload();
      if (detail) setDetail(await api<Detail>(`/cases/${detail.id}`));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    setBusy(true);
    try {
      await reload();
      if (detail) setDetail(await api<Detail>(`/cases/${detail.id}`));
      setToast("已同步最新业务记录");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function submitLogin(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", {
        username,
        password,
        tenant_id: username === "outsider" ? "other" : "northstar",
        store_id: username === "outsider" ? "de" : "uk",
      });
      setMe(await api<Me>("/me"));
      setPassword("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function beginModal(type: string) {
    setForm({});
    setModal(type);
    if (type === "case") {
      try {
        const data = await api<(Item & { lines: Item[] })[]>("/orders");
        setOrders(data);
        if (data[0])
          setForm({
            order_id: data[0].id,
            line_id: data[0].lines[0]?.id,
            kind: "replacement",
          });
      } catch (e) {
        setError((e as Error).message);
      }
    }
    if (type === "plan" && detail) {
      try {
        const data = await api<{ citations: Item[] }>(
          `/cases/${detail.id}/rules`,
        );
        setRules(data.citations);
        setForm({
          kind: detail.kind,
          quantity: "1",
          amount: "0",
          rationale: "根据订单记录和适用政策提出处理建议，待客户确认与审批。",
        });
      } catch (e) {
        setError((e as Error).message);
      }
    }
  }
  async function submitModal(e: React.FormEvent) {
    e.preventDefault();
    let ok = false;
    if (modal === "case")
      ok = await act(
        "/cases",
        { ...form, source_id: crypto.randomUUID() },
        "工单已创建",
      );
    if (modal === "plan" && detail)
      ok = await act(
        `/cases/${detail.id}/plans`,
        {
          version: detail.version,
          kind: form.kind,
          quantity: Number(form.quantity),
          amount_minor: Math.round(Number(form.amount || 0) * 100),
          address: form.address || "",
          rationale: form.rationale,
          citations: rules.map((rule) => rule.id),
        },
        "新方案已保存，需客户确认",
      );
    if (modal === "policy")
      ok = await act(
        "/policies",
        {
          family: form.family,
          title: form.title,
          body: form.body,
          kind: form.kind || "replacement",
          region: "GB",
          effective_from: Date.parse(form.start) / 1000,
          effective_until: Date.parse(form.end) / 1000,
        },
        "规则草稿已保存，待独立审核",
      );
    if (modal === "improvement")
      ok = await act(
        "/improvements",
        {
          title: form.title,
          hypothesis: form.hypothesis,
          case_ids: [form.case_id],
          due_at: Date.parse(form.due) / 1000,
        },
        "整改任务已创建",
      );
    if (modal === "claim")
      ok = await act(
        `/cases/${form.case_id}/claims`,
        {
          counterparty: form.counterparty,
          amount_minor: Math.round(Number(form.amount) * 100),
          evidence: form.evidence,
        },
        "索赔记录已创建",
      );
    if (modal === "review")
      ok = await act(
        `/improvements/${form.id}`,
        {
          version: Number(form.version),
          measure: form.measure,
          sample_size: Number(form.sample_size),
          observation: form.observation,
          close: form.close === "true",
        },
        "整改复查已保存",
        "PATCH",
      );
    if (modal === "settlement")
      ok = await act(
        `/claims/${form.id}`,
        {
          version: Number(form.version),
          status: form.status,
          settlement_ref: form.settlement_ref || "",
        },
        "索赔状态已保存",
        "PATCH",
      );
    if (ok) {
      setModal("");
      if (page === "knowledge") setItems(await api<Item[]>("/policies"));
      if (page === "improvements") {
        setItems(await api<Item[]>("/improvements"));
        setClaims(await api<Item[]>("/claims"));
      }
    }
  }
  const field = (key: string, value: string) =>
    setForm((old) => ({ ...old, [key]: value }));

  if (checking)
    return (
      <main className="loading-screen">
        <div className="brand-symbol">m</div>
        <Loader2 className="spin" />
        <p>正在打开工作台</p>
      </main>
    );
  if (!me)
    return (
      <main className="login-page">
        <section className="login-story">
          <div className="brand">
            <span className="brand-symbol">m</span>MercuryCare
            <span className="brand-dot" />
          </div>
          <span className="eyebrow">AFTER-SALES, WITH CARE.</span>
          <h1>
            每一次售后，
            <br />
            都有可信的
            <br />
            <em>下一步。</em>
          </h1>
          <p>
            连接客户、业务与团队。
            <br />
            让复杂的问题，得到清晰的回应。
          </p>
          <div className="login-orbit">
            <span>事实</span>
            <span>规则</span>
            <span>方案</span>
            <span>回复</span>
            <b>m</b>
          </div>
          <footer>MercuryCare · 跨境售后与经营协同</footer>
        </section>
        <section className="login-panel">
          <form onSubmit={submitLogin}>
            <span className="eyebrow">YOUR WORKSPACE</span>
            <h2>欢迎回来</h2>
            <p className="muted">登录后继续处理店铺售后事务。</p>
            <label>
              账号
              <input
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </label>
            <label>
              密码
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button className="primary wide" disabled={busy}>
              {busy ? (
                <Loader2 className="spin" size={18} />
              ) : (
                <>
                  登录工作台 <ArrowRight size={18} />
                </>
              )}
            </button>
            <div className="demo-note">
              <ShieldCheck size={20} />
              <div>
                <strong>本地演示环境</strong>
                <p>
                  默认账号 demo。运行 configure 后，演示密码为
                  MercuryDemo-2026!。客户账号 customer 用于确认方案。
                </p>
              </div>
            </div>
          </form>
        </section>
      </main>
    );

  const shown = cases.filter(
    (item) =>
      (page !== "approvals" ||
        ["waiting_approval", "approved"].includes(item.status)) &&
      (filter === "all" || item.status === filter) &&
      `${item.subject} ${item.order_id} ${item.customer_name}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const activePlan = detail?.plans[detail.plans.length - 1];
  const title = navigation.find((n) => n.id === page)?.label || "工作概览";
  const selectedChoice = detail?.choices.find(
    (c) => c.plan_id === activePlan?.id,
  );

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-symbol">m</span>MercuryCare
          <span className="brand-dot" />
        </div>
        <button
          className="store-switch"
          onClick={() =>
            setToast(
              `当前店铺：${me.tenant_id} / ${me.store_id}，访问范围由服务端授权`,
            )
          }
        >
          <span className="store-avatar">N</span>
          <span>
            <strong>Northstar Living</strong>
            <small>{me.store_id.toUpperCase()} 店铺工作空间</small>
          </span>
          <ChevronDown size={15} />
        </button>
        <span className="nav-caption">工作空间</span>
        <nav>
          {navigation
            .filter(
              (n) => !me.customer_id || ["overview", "cases"].includes(n.id),
            )
            .map((n) => (
              <button
                key={n.id}
                className={page === n.id ? "nav-item active" : "nav-item"}
                onClick={() => {
                  setPage(n.id);
                  setFilter("all");
                  setError("");
                }}
              >
                <n.icon size={19} />
                <span>{n.label}</span>
                {n.id === "cases" && <small>{cases.length}</small>}
                {n.id === "approvals" && Boolean(stats?.waiting_approval) && (
                  <i />
                )}
              </button>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="help-card">
            <span className="help-icon">
              <LifeBuoy size={19} />
            </span>
            <strong>让协作更有把握</strong>
            <p>
              每一步都有依据，每个动作
              <br />
              都可以追溯。
            </p>
            <a
              href="https://github.com/yxp-system/mercury-care"
              target="_blank"
              rel="noreferrer"
            >
              查看项目文档 <ArrowUpRight size={14} />
            </a>
          </div>
          <button
            className="profile"
            onClick={async () => {
              await api("/auth/logout", {});
              setMe(null);
              setDetail(null);
            }}
          >
            <span className="avatar">{me.name.slice(0, 1)}</span>
            <span>
              <strong>{me.name}</strong>
              <small>退出当前账号</small>
            </span>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <ChevronRight size={14} />
            <strong>{title}</strong>
          </div>
          <div className="top-actions">
            <span className="environment-dot" /> <span>{caps?.business === "synthetic" ? "合成数据演示" : "企业接入待验证"}</span>
            <span className="top-divider" />
            <button aria-label="帮助" onClick={() => setPage("jobs")}>
              <CircleHelp size={19} />
            </button>
            <button aria-label="待办通知" onClick={() => setPage("approvals")}>
              <Bell size={19} />
              <i />
            </button>
            <span className="avatar small">{me.name.slice(0, 1)}</span>
          </div>
        </header>
        <main className="content">
          <div className="page-title">
            <div>
              <span className="eyebrow">
                {page === "overview"
                  ? "WORKSPACE OVERVIEW"
                  : "MERCURYCARE WORKSPACE"}
              </span>
              <h1>
                {page === "overview" ? "把每一份关心，落到实处。" : title}
              </h1>
              <p>
                {page === "overview"
                  ? "欢迎回来，一起看看今天需要关注的售后与团队协作。"
                  : "基于实时业务记录，让处理进度清晰可见。"}
              </p>
            </div>
            <div className="page-actions">
              <button className="secondary" onClick={refresh} disabled={busy}>
                <RefreshCw size={16} className={busy ? "spin" : ""} />
                刷新
              </button>
              {allowed("agent", "customer") && (
                <button className="primary" onClick={() => beginModal("case")}>
                  <Plus size={17} />
                  新建工单
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="error global-error" role="alert">
              {error}
              <button aria-label="关闭错误" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {page === "overview" && (
            <>
              <div className="metrics-grid">
                {[
                  {
                    label: "待处理工单",
                    value: stats?.open_cases ?? cases.length,
                    icon: Inbox,
                    foot: "需要持续关注的客户问题",
                    color: "blue",
                  },
                  {
                    label: "等待人工审核",
                    value: stats?.waiting_approval ?? 0,
                    icon: ShieldCheck,
                    foot: "客户确认后进入审批流程",
                    color: "amber",
                  },
                  {
                    label: "已解决并结案",
                    value: cases.filter((c) => c.status === "closed").length,
                    icon: CheckCheck,
                    foot: "以客户解决确认作为依据",
                    color: "green",
                  },
                  {
                    label: "待核查的动作",
                    value: stats?.unknown_actions ?? 0,
                    icon: Activity,
                    foot: "沿原请求追踪，避免重复处理",
                    color: "purple",
                  },
                ].map((m) => (
                  <article className="metric" key={m.label}>
                    <div>
                      <span>{m.label}</span>
                      <span className={`metric-icon ${m.color}`}>
                        <m.icon size={18} />
                      </span>
                    </div>
                    <strong>{m.value.toString().padStart(2, "0")}</strong>
                    <p>
                      <span className="tiny-dot" />
                      {m.foot}
                    </p>
                  </article>
                ))}
              </div>
              <section className="attention-banner">
                <div className="banner-icon">
                  <Sparkles size={23} />
                </div>
                <div>
                  <h3>让 AI 帮你理清事实，让团队掌握每个决定。</h3>
                  <p>
                    从订单核查到回复草稿，五个专业角色协同推进。客户确认、人工审批和业务执行各有依据。
                  </p>
                </div>
                <button onClick={() => cases[0] && openCase(cases[0].id)}>
                  开始处理 <ArrowRight size={16} />
                </button>
              </section>
            </>
          )}

          {["overview", "cases", "approvals"].includes(page) && (
            <div className={page === "overview" ? "overview-grid" : ""}>
              <section className="panel case-panel">
                <div className="panel-heading">
                  <div>
                    <h2>
                      {page === "approvals" ? "等待决策" : "售后工单"}
                      <span className="count">{shown.length}</span>
                    </h2>
                    <p>每一个问题，都值得被认真回应。</p>
                  </div>
                  <button
                    className="icon-button"
                    aria-label="刷新工单"
                    onClick={refresh}
                  >
                    <MoreHorizontal size={20} />
                  </button>
                </div>
                <div className="table-toolbar">
                  <div className="tabs">
                    {[
                      ["all", "全部工单"],
                      ["open", "待处理"],
                      ["waiting_approval", "待审核"],
                      ["closed", "已结案"],
                    ].map(([key, label]) => (
                      <button
                        key={key}
                        className={filter === key ? "selected" : ""}
                        onClick={() => setFilter(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <label className="search">
                    <Search size={15} />
                    <input
                      placeholder="搜索工单、订单或客户"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>客户与问题</th>
                        <th>处理类型</th>
                        <th>状态</th>
                        <th>更新时间</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {shown
                        .slice(0, page === "overview" ? 6 : 200)
                        .map((item, i) => (
                          <tr
                            key={item.id}
                            onClick={() => openCase(item.id)}
                            tabIndex={0}
                            onKeyDown={(e) =>
                              e.key === "Enter" && openCase(item.id)
                            }
                          >
                            <td>
                              <div className="customer-cell">
                                <span className={`avatar tone-${i % 4}`}>
                                  {item.customer_name.slice(0, 1)}
                                </span>
                                <div>
                                  <strong>{item.subject}</strong>
                                  <small>
                                    {item.customer_name} <span>·</span>{" "}
                                    {item.order_id}
                                  </small>
                                </div>
                              </div>
                            </td>
                            <td>
                              <span className="kind-tag">
                                {kindNames[item.kind]}
                              </span>
                            </td>
                            <td>
                              <Badge status={item.status} />
                            </td>
                            <td className="muted nowrap">
                              {date(item.updated_at)}
                            </td>
                            <td>
                              <ChevronRight size={16} className="muted" />
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                  {!shown.length && (
                    <div className="empty">
                      <Inbox size={32} />
                      <strong>这里暂时没有工单</strong>
                      <p>可以调整筛选条件，或新建客户问题。</p>
                    </div>
                  )}
                </div>
                <div className="table-footer">
                  <span>
                    显示 {Math.min(shown.length, page === "overview" ? 6 : 200)}{" "}
                    条，共 {shown.length} 条工单
                  </span>
                  <button
                    onClick={() => {
                      setPage("cases");
                      setFilter("all");
                    }}
                  >
                    查看全部工单 <ArrowRight size={14} />
                  </button>
                </div>
              </section>
              {page === "overview" && (
                <aside className="right-column">
                  <section className="panel workflow-card">
                    <div className="panel-heading">
                      <h2>协同处理流程</h2>
                      <span className="soft-label">5 个角色</span>
                    </div>
                    <p className="muted intro">
                      专业协作，每一步都有清楚的边界。
                    </p>
                    {[
                      ["01", "事实核查", "查询订单、库存与历史处理", Package],
                      ["02", "规则检索", "找到有出处的适用依据", BookOpen],
                      ["03", "方案建议", "结合证据形成处理方案", FileCheck2],
                      ["04", "回复草稿", "准确传达当前业务进度", MessageSquare],
                    ].map(([num, label, desc, Icon]) => {
                      const I = Icon as typeof Inbox;
                      return (
                        <div className="flow-step" key={String(num)}>
                          <span>
                            <I size={17} />
                          </span>
                          <div>
                            <strong>{String(label)}</strong>
                            <p>{String(desc)}</p>
                          </div>
                          <small>{String(num)}</small>
                        </div>
                      );
                    })}
                    <div className="supervisor-note">
                      <Sparkles size={16} />由 Supervisor 按需安排与核对
                    </div>
                  </section>
                  <section className="panel health-card">
                    <div className="panel-heading">
                      <h2>工作空间状态</h2>
                      <Globe2 size={17} />
                    </div>
                    <div className="health-row">
                      <span>业务数据</span>
                      <span className="green-text">合成演示</span>
                    </div>
                    <div className="health-row">
                      <span>模型运行</span>
                      <span>
                        {caps?.model === "real"
                          ? "真实模型配置"
                          : "确定性测试模式"}
                      </span>
                    </div>
                    <div className="health-row">
                      <span>外部系统</span>
                      <span className="muted">等待企业接入</span>
                    </div>
                    <p>当前演示不会执行真实退款或向客户发送消息。</p>
                  </section>
                </aside>
              )}
            </div>
          )}

          {page === "knowledge" && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>政策与知识库</h2>
                  <p>保留版本、出处、有效期与独立审核记录。</p>
                </div>
                {allowed("knowledge") && (
                  <button
                    className="primary"
                    onClick={() => beginModal("policy")}
                  >
                    <Plus size={16} />
                    新建规则
                  </button>
                )}
              </div>
              <div className="policy-grid">
                {items.map((item) => (
                  <article className="policy-card" key={item.id}>
                    <div className="policy-top">
                      <span className="doc-icon">
                        <FileText size={22} />
                      </span>
                      <Badge status={text(item.status)} />
                    </div>
                    <h3>{text(item.title)}</h3>
                    <p>{text(item.body)}</p>
                    <div className="policy-meta">
                      <span>
                        v{text(item.version)} · {text(item.region)}
                      </span>
                      <span>{kindNames[text(item.kind)]}</span>
                    </div>
                    <small>
                      生效：{date(item.effective_from)} · 来源：
                      {item.source === "synthetic"
                        ? "合成政策"
                        : text(item.source)}
                    </small>
                    {allowed("knowledge_reviewer") && (
                      <button
                        className="secondary"
                        onClick={async () => {
                          const match = cases.find((c) => c.kind === item.kind);
                          const ok = await act(
                            `/policies/${item.id}/${item.status === "published" ? "withdraw" : "publish"}`,
                            {
                              validation_cases: match ? [match.id] : [],
                              note: "审核人已核对代表性案例与适用范围",
                            },
                          );
                          if (ok) setItems(await api<Item[]>("/policies"));
                        }}
                      >
                        {item.status === "published"
                          ? "撤回规则"
                          : "审核并发布"}
                      </button>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

          {page === "operations" && stats && (
            <>
              <div className="metrics-grid">
                {[
                  ["订单总数", stats.order_count],
                  ["问题订单", stats.issue_order_count],
                  [
                    "问题订单占比",
                    stats.issue_rate === null
                      ? "无分母"
                      : `${Math.round(stats.issue_rate * 100)}%`,
                  ],
                  [
                    "已完成退款",
                    stats.refunded_minor === null
                      ? "多币种，请分别核对"
                      : money(stats.refunded_minor, stats.currency),
                  ],
                ].map(([label, value]) => (
                  <article className="metric" key={String(label)}>
                    <div>
                      {label}
                      <TrendingUp size={18} />
                    </div>
                    <strong>{value}</strong>
                    <p>当前店铺 · 全部时间 · 合成记录</p>
                  </article>
                ))}
              </div>
              <section className="panel analytics">
                <div className="panel-heading">
                  <div>
                    <h2>问题类型分布</h2>
                    <p>按工单统计；问题订单按订单去重，重复咨询不增加分母。</p>
                  </div>
                </div>
                {Object.entries(stats.by_kind).map(([kind, count]) => (
                  <div className="bar-row" key={kind}>
                    <span>{kindNames[kind]}</span>
                    <div>
                      <i
                        style={{
                          width: `${(count / Math.max(...Object.values(stats.by_kind))) * 100}%`,
                        }}
                      />
                    </div>
                    <b>{count}</b>
                  </div>
                ))}
                <p className="muted">
                  统计反映当前合成数据，不代表企业业务收益或生产容量。
                </p>
              </section>
            </>
          )}

          {page === "inventory" && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>库存与可用量</h2>
                  <p>预留占用可用量，出库结转预留，不重复扣减。</p>
                </div>
                <Boxes size={22} />
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>SKU</th>
                      <th>在库</th>
                      <th>已预留</th>
                      <th>可用</th>
                      <th>已出库</th>
                      <th>版本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <strong>{text(item.sku)}</strong>
                        </td>
                        <td>{text(item.on_hand)}</td>
                        <td>{text(item.reserved)}</td>
                        <td className="green-text">
                          <strong>{text(item.available)}</strong>
                        </td>
                        <td>{text(item.shipped)}</td>
                        <td>v{text(item.version)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {page === "improvements" && (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>经营整改</h2>
                    <p>把信号转成调查，把改进落实到复查。</p>
                  </div>
                  <button
                    className="primary"
                    onClick={() => beginModal("improvement")}
                  >
                    <Plus size={16} />
                    创建整改
                  </button>
                </div>
                {!items.length ? (
                  <div className="empty">
                    <TrendingUp size={32} />
                    <strong>尚无整改任务</strong>
                    <p>从工单证据出发，记录假设、措施和复查口径。</p>
                  </div>
                ) : (
                  items.map((item) => (
                    <div className="record-card" key={item.id}>
                      <h3>{text(item.title)}</h3>
                      <p>{text(item.hypothesis)}</p>
                      <small>
                        负责人 {text(item.owner_id)} · 截止 {date(item.due_at)}
                      </small>
                      <Badge status={text(item.status)} />
                      <div className="action-row">
                        <button
                          className="secondary"
                          onClick={() => {
                            setForm({
                              id: item.id,
                              version: text(item.version),
                              measure:
                                text(item.measure) === "—"
                                  ? ""
                                  : text(item.measure),
                              close: "false",
                            });
                            setModal("review");
                          }}
                        >
                          记录措施与复查
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </section>
              <section className="panel spaced">
                <div className="panel-heading">
                  <div>
                    <h2>内部索赔</h2>
                    <p>客户解决与内部追偿分别推进。</p>
                  </div>
                  <button
                    className="secondary"
                    onClick={() => beginModal("claim")}
                  >
                    <Plus size={16} />
                    登记索赔
                  </button>
                </div>
                {!claims.length ? (
                  <div className="empty compact">
                    <FileCheck2 size={30} />
                    <p>还没有内部索赔记录</p>
                  </div>
                ) : (
                  claims.map((item) => (
                    <div className="record-card" key={item.id}>
                      <h3>{text(item.counterparty)}</h3>
                      <p>{text(item.evidence)}</p>
                      <strong>
                        {money(Number(item.amount_minor), text(item.currency))}
                      </strong>
                      <Badge status={text(item.status)} />
                      <div className="action-row">
                        <button
                          className="secondary"
                          onClick={() => {
                            setForm({
                              id: item.id,
                              version: text(item.version),
                            });
                            setModal("settlement");
                          }}
                        >
                          更新索赔进度
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </section>
            </>
          )}

          {page === "jobs" && (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>持久化任务</h2>
                  <p>查看执行、恢复与错误；失败重试沿用原业务关联。</p>
                </div>
                <Activity size={22} />
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>任务类型</th>
                      <th>关联标识</th>
                      <th>状态</th>
                      <th>尝试次数</th>
                      <th>错误</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id}>
                        <td>{text(item.kind)}</td>
                        <td className="mono">
                          {text(item.target_id).slice(0, 16)}
                        </td>
                        <td>
                          <Badge status={text(item.status)} />
                        </td>
                        <td>{text(item.attempts)}</td>
                        <td>{text(item.error)}</td>
                        <td>
                          {item.status === "dead" && allowed("executor") && (
                            <button
                              className="secondary"
                              onClick={async () => {
                                await act(`/jobs/${item.id}/retry`, {});
                                setItems(await api<Item[]>("/jobs"));
                              }}
                            >
                              重试原任务
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!items.length && (
                  <div className="empty">
                    <Activity size={30} />
                    <p>分析或执行工单后，运行记录会出现在这里。</p>
                  </div>
                )}
              </div>
            </section>
          )}
          <footer className="page-footer">
            <span>MercuryCare · 每一步，都有迹可循。</span>
            <span>
              <span className="tiny-dot" />
              {me.tenant_id} / {me.store_id.toUpperCase()}
            </span>
          </footer>
        </main>
      </div>

      {detail && (
        <div className="drawer-backdrop" onClick={() => setDetail(null)}>
          <section
            className="drawer"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="工单详情"
          >
            <header className="drawer-header">
              <div>
                <span className="eyebrow">{detail.order_id}</span>
                <h2>{detail.subject}</h2>
                <div className="detail-meta">
                  <Badge status={detail.status} />
                  <span>{kindNames[detail.kind]}</span>
                  <span>v{detail.version}</span>
                </div>
              </div>
              <button
                className="icon-button"
                aria-label="关闭详情"
                onClick={() => setDetail(null)}
              >
                <X size={21} />
              </button>
            </header>
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            <div className="drawer-tabs">
              {[
                ["overview", "处理概览"],
                ["evidence", "事实与依据"],
                ["activity", "运行与审计"],
              ].map(([key, label]) => (
                <button
                  className={tab === key ? "selected" : ""}
                  key={key}
                  onClick={async () => {
                    setTab(key);
                    if (key === "evidence" && !me.customer_id) {
                      try {
                        const [f, r] = await Promise.all([
                          api<Record<string, unknown>>(
                            `/cases/${detail.id}/facts`,
                          ),
                          api<{ citations: Item[] }>(
                            `/cases/${detail.id}/rules`,
                          ),
                        ]);
                        setFacts(f);
                        setRules(r.citations);
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="drawer-body">
              {tab === "overview" && (
                <>
                  <div className="order-summary">
                    <span className="product-icon">
                      <Package size={29} />
                    </span>
                    <div>
                      <strong>{text(detail.line.title)}</strong>
                      <p>
                        {text(detail.line.sku)} · {text(detail.line.quantity)}{" "}
                        件
                      </p>
                      <small>
                        {text(detail.order.customer_name)} ·{" "}
                        {text(detail.order.region)}
                      </small>
                    </div>
                    <b>
                      {money(
                        Number(detail.order.paid_minor),
                        text(detail.order.currency),
                      )}
                    </b>
                  </div>
                  <section className="detail-section">
                    <h3>
                      客户沟通 <MessageSquare size={16} />
                    </h3>
                    {detail.messages.map((m) => (
                      <div className="message" key={m.id}>
                        <div>
                          <strong>{text(m.author_id)}</strong>
                          <small>{date(m.created_at)}</small>
                        </div>
                        <p>{text(m.body)}</p>
                      </div>
                    ))}
                    {allowed("agent", "customer") && (
                      <form
                        className="message-compose"
                        onSubmit={async (e) => {
                          e.preventDefault();
                          if (
                            await act(`/cases/${detail.id}/messages`, {
                              version: detail.version,
                              body: message,
                              source_id: crypto.randomUUID(),
                            })
                          )
                            setMessage("");
                        }}
                      >
                        <input
                          placeholder="补充客户材料或处理说明…"
                          value={message}
                          onChange={(e) => setMessage(e.target.value)}
                          required
                        />
                        <button aria-label="保存沟通" disabled={busy}>
                          <Send size={17} />
                        </button>
                      </form>
                    )}
                  </section>
                  <section className="detail-section">
                    <div className="section-title">
                      <h3>处理方案</h3>
                      {allowed("agent") && (
                        <button
                          className="text-button"
                          onClick={() => beginModal("plan")}
                        >
                          <Plus size={14} />
                          {activePlan ? "新版本" : "手动制定"}
                        </button>
                      )}
                    </div>
                    {activePlan ? (
                      <div className="plan-card">
                        <div>
                          <span className="plan-icon">
                            <FileCheck2 size={19} />
                          </span>
                          <strong>{kindNames[activePlan.kind]}</strong>
                          <span className="muted">
                            方案 v{activePlan.version}
                          </span>
                        </div>
                        <p>{activePlan.rationale}</p>
                        <div className="plan-values">
                          <span>
                            数量 <b>{activePlan.quantity}</b>
                          </span>
                          {activePlan.kind === "refund" && (
                            <span>
                              退款{" "}
                              <b>
                                {money(
                                  activePlan.amount_minor,
                                  activePlan.currency,
                                )}
                              </b>
                            </span>
                          )}
                        </div>
                        <div className="approval-status">
                          <Check size={15} />
                          {selectedChoice?.accepted
                            ? "客户已确认"
                            : "等待客户确认"}
                          <span>·</span>
                          {detail.approvals.some(
                            (a) =>
                              a.plan_id === activePlan.id &&
                              a.decision === "approved",
                          )
                            ? "已记录审批决定"
                            : "尚未批准"}
                        </div>
                        <div className="action-row">
                          {allowed("customer") && !selectedChoice && (
                            <>
                              <button
                                className="primary"
                                disabled={busy}
                                onClick={() =>
                                  act(
                                    `/cases/${detail.id}/choice`,
                                    { plan_id: activePlan.id, accepted: true },
                                    "客户确认已保存",
                                  )
                                }
                              >
                                确认方案
                              </button>
                              <button
                                className="secondary"
                                disabled={busy}
                                onClick={() =>
                                  act(`/cases/${detail.id}/choice`, {
                                    plan_id: activePlan.id,
                                    accepted: false,
                                  })
                                }
                              >
                                不同意
                              </button>
                            </>
                          )}
                          {allowed("approver") && (
                            <>
                              <button
                                className="primary"
                                disabled={busy}
                                onClick={() =>
                                  act(
                                    `/cases/${detail.id}/approvals`,
                                    {
                                      plan_id: activePlan.id,
                                      decision: "approved",
                                      reason:
                                        "已核对当前方案、客户确认和业务依据",
                                    },
                                    "审批决定已保存",
                                  )
                                }
                              >
                                批准方案
                              </button>
                              <button
                                className="secondary"
                                disabled={busy}
                                onClick={() =>
                                  act(`/cases/${detail.id}/approvals`, {
                                    plan_id: activePlan.id,
                                    decision: "rejected",
                                    reason: "当前方案尚不满足处理要求，请复核",
                                  })
                                }
                              >
                                拒绝
                              </button>
                            </>
                          )}
                          {allowed("executor") && (
                            <button
                              className="secondary"
                              disabled={busy}
                              onClick={() =>
                                act(
                                  `/cases/${detail.id}/actions`,
                                  {
                                    plan_id: activePlan.id,
                                    business_action_id: crypto.randomUUID(),
                                  },
                                  "执行意图已保存，等待 worker 处理",
                                )
                              }
                            >
                              执行已批准方案 <ArrowRight size={15} />
                            </button>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="empty compact">
                        <Sparkles size={28} />
                        <p>先核查事实和依据，再形成可审批的方案。</p>
                        {allowed("agent") && (
                          <button
                            className="primary"
                            disabled={busy}
                            onClick={() =>
                              act(
                                `/cases/${detail.id}/analyze`,
                                {},
                                "分析任务已入队，请稍后刷新",
                              )
                            }
                          >
                            <Sparkles size={16} />
                            开始协同分析
                          </button>
                        )}
                      </div>
                    )}
                  </section>
                  {detail.actions.length > 0 && (
                    <section className="detail-section">
                      <h3>业务动作</h3>
                      {detail.actions.map((action) => (
                        <div className="record-card" key={action.id}>
                          <div className="section-title">
                            <strong>{kindNames[action.kind]}</strong>
                            <Badge status={action.status} />
                          </div>
                          <small className="mono">
                            原请求 {action.request_id}
                          </small>
                          <div className="action-row">
                            {allowed("executor") && (
                              <>
                                <button
                                  className="secondary"
                                  disabled={busy}
                                  onClick={() =>
                                    act(
                                      `/actions/${action.id}/reconcile`,
                                      {},
                                      "已核查原请求",
                                    )
                                  }
                                >
                                  核查原请求
                                </button>
                                {(["replacement", "missing", "wrong"].includes(
                                  action.kind,
                                )
                                  ? action.status === "accepted"
                                    ? ["shipped"]
                                    : action.status === "shipped"
                                      ? ["delivered"]
                                      : []
                                  : action.kind === "return"
                                    ? action.status === "accepted"
                                      ? ["received"]
                                      : action.status === "received"
                                        ? ["inspected"]
                                        : []
                                    : action.status === "accepted"
                                      ? [
                                          action.kind === "delivery"
                                            ? "investigated"
                                            : "succeeded",
                                        ]
                                      : []
                                ).map((next) => (
                                  <button
                                    className="secondary"
                                    key={next}
                                    disabled={busy}
                                    onClick={() =>
                                      act(
                                        `/demo/actions/${action.id}/events`,
                                        {
                                          source_id: crypto.randomUUID(),
                                          sequence: action.last_sequence + 1,
                                          kind: next,
                                          evidence:
                                            "操作员提交的本地合成履约事件",
                                        },
                                        "合成事件已记录",
                                      )
                                    }
                                  >
                                    模拟{statusNames[next]}
                                  </button>
                                ))}
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </section>
                  )}
                  {!!detail.runs?.length && detail.runs.at(-1)?.draft ? (
                    <section className="detail-section">
                      <h3>
                        回复草稿 <span className="soft-label">待人工核对</span>
                      </h3>
                      <div className="draft">
                        {text(detail.runs.at(-1)?.draft)}
                      </div>
                      {allowed("agent") && (
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              `/cases/${detail.id}/notifications`,
                              { body: text(detail.runs?.at(-1)?.draft) },
                              "已提交本地通知任务",
                            )
                          }
                        >
                          确认并记录通知 <Send size={15} />
                        </button>
                      )}
                    </section>
                  ) : null}
                  <div className="action-row closure-actions">
                    {allowed("customer") && (
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() =>
                          act(`/cases/${detail.id}/resolve`, {
                            version: detail.version,
                            reason: "客户确认问题已得到解决",
                          })
                        }
                      >
                        确认问题已解决
                      </button>
                    )}
                    {allowed("agent") && (
                      <>
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              `/cases/${detail.id}/${detail.status === "closed" ? "reopen" : "close"}`,
                              {
                                version: detail.version,
                                reason: "客服核查当前业务证据与客户反馈",
                              },
                            )
                          }
                        >
                          {detail.status === "closed"
                            ? "重开工单"
                            : "核查并结案"}
                        </button>
                        <button
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            act(`/cases/${detail.id}/analyze`, {}, "运行已入队")
                          }
                        >
                          继续分析
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
              {tab === "evidence" && (
                <>
                  <section className="detail-section">
                    <h3>事实来源</h3>
                    {facts ? (
                      <>
                        <div className="facts-grid">
                          <div>
                            <small>业务来源</small>
                            <strong>
                              {facts.source === "synthetic"
                                ? "本地合成记录"
                                : text(facts.source)}
                            </strong>
                          </div>
                          <div>
                            <small>查询时间</small>
                            <strong>{date(facts.queried_at)}</strong>
                          </div>
                          <div>
                            <small>源记录时间</small>
                            <strong>{date(facts.source_updated_at)}</strong>
                          </div>
                          <div>
                            <small>可用库存</small>
                            <strong>
                              {text(
                                (
                                  facts.inventory as Record<
                                    string,
                                    unknown
                                  > | null
                                )?.available,
                              )}
                            </strong>
                          </div>
                        </div>
                        <p className="muted">
                          库存查询不表示已经预留；客户陈述与工具事实分别保留。
                        </p>
                      </>
                    ) : (
                      <p className="muted">
                        当前身份可查看附件与客户记录；内部事实工具仅限授权岗位。
                      </p>
                    )}
                  </section>
                  <section className="detail-section">
                    <h3>适用规则与出处</h3>
                    {rules.map((rule) => (
                      <div className="record-card" key={rule.id}>
                        <strong>
                          {text(rule.title)} · v{text(rule.version)}
                        </strong>
                        <p>{text(rule.body)}</p>
                        <small className="mono">{rule.id}</small>
                      </div>
                    ))}
                  </section>
                  <section className="detail-section">
                    <h3>证据附件</h3>
                    {detail.attachments.map((file) => (
                      <a
                        className="attachment"
                        href={`/api/attachments/${file.id}`}
                        key={file.id}
                      >
                        <FileText size={18} />
                        {text(file.filename)}
                        <small>{text(file.scan_status)}</small>
                      </a>
                    ))}
                    {allowed("agent", "customer") && (
                      <label className="upload">
                        <Upload size={18} />
                        上传材料（最大5MB）
                        <input
                          type="file"
                          accept="image/png,image/jpeg,application/pdf,text/plain"
                          onChange={async (e) => {
                            const file = e.target.files?.[0];
                            if (!file) return;
                            const body = new FormData();
                            body.append("file", file);
                            setBusy(true);
                            try {
                              const r = await fetch(
                                `/api/cases/${detail.id}/attachments`,
                                { method: "POST", body },
                              );
                              const data = await r.json();
                              if (!r.ok) throw new Error(data.error?.message);
                              setDetail(
                                await api<Detail>(`/cases/${detail.id}`),
                              );
                              setToast("附件已保存，安全扫描状态可见");
                            } catch (e) {
                              setError((e as Error).message);
                            } finally {
                              setBusy(false);
                            }
                          }}
                        />
                      </label>
                    )}
                  </section>
                </>
              )}
              {tab === "activity" && (
                <>
                  <section className="detail-section">
                    <h3>协同运行</h3>
                    {!detail.runs?.length && (
                      <p className="muted">
                        尚无运行记录，或当前身份无权查看。
                      </p>
                    )}
                    {detail.runs?.map((run) => (
                      <div className="run-card" key={run.id}>
                        <div className="section-title">
                          <span className="mono">{run.id.slice(0, 12)}</span>
                          <Badge status={text(run.status)} />
                        </div>
                        <small>
                          模型调用 {text(run.calls)} 次 · 未知用量{" "}
                          {run.unknown_usage ? "是" : "否"}
                        </small>
                        {run.error ? (
                          <p className="error">{text(run.error)}</p>
                        ) : null}
                        {run.steps.map((step) => (
                          <div className="trace-step" key={step.id}>
                            <span className="trace-dot" />
                            <div>
                              <strong>
                                {roleNames[text(step.role)] || text(step.role)}
                              </strong>
                              <p>{text(step.summary)}</p>
                              <small>
                                {text(step.mode)} · {text(step.duration_ms)} ms
                              </small>
                            </div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </section>
                  <section className="detail-section">
                    <h3>业务审计</h3>
                    {detail.audit?.map((event) => (
                      <div className="audit-row" key={event.id}>
                        <Clock3 size={14} />
                        <div>
                          <strong>{text(event.verb)}</strong>
                          <small>
                            {text(event.actor_id)} · {date(event.created_at)}
                          </small>
                        </div>
                      </div>
                    ))}
                  </section>
                </>
              )}
            </div>
            <footer className="drawer-footer">
              <span>
                <ShieldCheck size={14} />
                操作由服务端复核权限与版本
              </span>
              <button className="secondary" onClick={refresh} disabled={busy}>
                <RefreshCw size={15} />
                刷新记录
              </button>
            </footer>
          </section>
        </div>
      )}

      {modal && (
        <div className="modal-backdrop">
          <form className="modal" onSubmit={submitModal}>
            <div className="section-title">
              <h2>
                {
                  {
                    case: "新建售后工单",
                    plan: "制定处理方案",
                    policy: "创建规则版本",
                    improvement: "登记整改任务",
                    claim: "登记内部索赔",
                    review: "整改复查",
                    settlement: "索赔进度与对账",
                  }[modal]
                }
              </h2>
              <button
                type="button"
                className="icon-button"
                onClick={() => setModal("")}
                aria-label="关闭表单"
              >
                <X size={20} />
              </button>
            </div>
            {error && <p className="error">{error}</p>}
            {modal === "review" && (
              <>
                <label>
                  已实施措施
                  <textarea
                    required
                    minLength={5}
                    value={form.measure || ""}
                    onChange={(e) => field("measure", e.target.value)}
                  />
                </label>
                <label>
                  可比样本数
                  <input
                    type="number"
                    required
                    min="1"
                    value={form.sample_size || ""}
                    onChange={(e) => field("sample_size", e.target.value)}
                  />
                </label>
                <label>
                  观察结果及限制
                  <textarea
                    required
                    minLength={10}
                    value={form.observation || ""}
                    onChange={(e) => field("observation", e.target.value)}
                  />
                </label>
                <label>
                  复查决定
                  <select
                    value={form.close || "false"}
                    onChange={(e) => field("close", e.target.value)}
                  >
                    <option value="false">继续观察</option>
                    <option value="true">确认关闭（须满足样本条件）</option>
                  </select>
                </label>
              </>
            )}
            {modal === "settlement" && (
              <>
                <label>
                  目标状态
                  <select
                    required
                    value={form.status || ""}
                    onChange={(e) => field("status", e.target.value)}
                  >
                    <option value="">选择状态</option>
                    <option value="submitted">已提交</option>
                    <option value="accepted">已受理</option>
                    <option value="settled">已到账并对账</option>
                    <option value="rejected">被拒绝</option>
                  </select>
                </label>
                <label>
                  结算凭证（到账必填）
                  <input
                    value={form.settlement_ref || ""}
                    onChange={(e) => field("settlement_ref", e.target.value)}
                  />
                </label>
                <p className="muted">
                  状态将由服务端核对转换条件；不会改变客户工单结案状态。
                </p>
              </>
            )}
            {modal === "case" && (
              <>
                <label>
                  订单
                  <select
                    value={form.order_id || ""}
                    onChange={(e) => {
                      const order = orders.find((o) => o.id === e.target.value);
                      setForm((old) => ({
                        ...old,
                        order_id: e.target.value,
                        line_id: order?.lines[0]?.id || "",
                      }));
                    }}
                  >
                    {orders.map((o) => (
                      <option value={o.id} key={o.id}>
                        {o.id} · {text(o.customer_name)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  商品
                  <select
                    value={form.line_id || ""}
                    onChange={(e) => field("line_id", e.target.value)}
                  >
                    {orders
                      .find((o) => o.id === form.order_id)
                      ?.lines.map((line) => (
                        <option key={line.id} value={line.id}>
                          {text(line.title)}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  问题标题
                  <input
                    required
                    minLength={3}
                    value={form.subject || ""}
                    onChange={(e) => field("subject", e.target.value)}
                  />
                </label>
                <label>
                  客户原始反馈
                  <textarea
                    required
                    value={form.message || ""}
                    onChange={(e) => field("message", e.target.value)}
                  />
                </label>
              </>
            )}
            {["case", "plan", "policy"].includes(modal) && (
              <label>
                处理类型
                <select
                  value={form.kind || "replacement"}
                  onChange={(e) => field("kind", e.target.value)}
                >
                  {Object.entries(kindNames).map(([key, value]) => (
                    <option value={key} key={key}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {modal === "plan" && (
              <>
                <div className="two-fields">
                  <label>
                    数量
                    <input
                      type="number"
                      min="1"
                      required
                      value={form.quantity || "1"}
                      onChange={(e) => field("quantity", e.target.value)}
                    />
                  </label>
                  <label>
                    退款金额（主币单位）
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      value={form.amount || "0"}
                      onChange={(e) => field("amount", e.target.value)}
                    />
                  </label>
                </div>
                {form.kind === "address" && (
                  <label>
                    新地址
                    <input
                      required
                      minLength={8}
                      value={form.address || ""}
                      onChange={(e) => field("address", e.target.value)}
                    />
                  </label>
                )}
                <label>
                  方案依据
                  <textarea
                    required
                    minLength={3}
                    value={form.rationale || ""}
                    onChange={(e) => field("rationale", e.target.value)}
                  />
                </label>
                <p className="muted">
                  已关联 {rules.length}{" "}
                  条当前适用规则；保存后需要重新取得客户选择与审批。
                </p>
              </>
            )}
            {modal === "policy" && (
              <>
                <label>
                  规则族标识
                  <input
                    required
                    minLength={3}
                    placeholder="例如：damage-uk"
                    onChange={(e) => field("family", e.target.value)}
                  />
                </label>
                <label>
                  标题
                  <input
                    required
                    minLength={3}
                    onChange={(e) => field("title", e.target.value)}
                  />
                </label>
                <label>
                  政策原文
                  <textarea
                    required
                    minLength={20}
                    onChange={(e) => field("body", e.target.value)}
                  />
                </label>
                <div className="two-fields">
                  <label>
                    生效日期
                    <input
                      type="date"
                      required
                      onChange={(e) => field("start", e.target.value)}
                    />
                  </label>
                  <label>
                    失效日期
                    <input
                      type="date"
                      required
                      onChange={(e) => field("end", e.target.value)}
                    />
                  </label>
                </div>
              </>
            )}
            {["improvement", "claim"].includes(modal) && (
              <label>
                关联工单
                <select
                  required
                  value={form.case_id || ""}
                  onChange={(e) => field("case_id", e.target.value)}
                >
                  <option value="">选择证据工单</option>
                  {cases.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.order_id} · {c.subject}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {modal === "improvement" && (
              <>
                <label>
                  整改标题
                  <input
                    required
                    minLength={3}
                    onChange={(e) => field("title", e.target.value)}
                  />
                </label>
                <label>
                  待验证的原因假设
                  <textarea
                    required
                    minLength={5}
                    onChange={(e) => field("hypothesis", e.target.value)}
                  />
                </label>
                <label>
                  复查日期
                  <input
                    type="date"
                    required
                    onChange={(e) => field("due", e.target.value)}
                  />
                </label>
              </>
            )}
            {modal === "claim" && (
              <>
                <label>
                  追偿对象
                  <input
                    required
                    minLength={2}
                    onChange={(e) => field("counterparty", e.target.value)}
                  />
                </label>
                <label>
                  索赔金额
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    required
                    onChange={(e) => field("amount", e.target.value)}
                  />
                </label>
                <label>
                  证据说明
                  <textarea
                    required
                    minLength={5}
                    onChange={(e) => field("evidence", e.target.value)}
                  />
                </label>
              </>
            )}
            <div className="modal-footer">
              <button
                type="button"
                className="secondary"
                onClick={() => setModal("")}
              >
                取消
              </button>
              <button className="primary" disabled={busy}>
                {busy ? <Loader2 size={16} className="spin" /> : "保存并校验"}
              </button>
            </div>
          </form>
        </div>
      )}
      {toast && (
        <div className="toast" role="status">
          <CheckCheck size={18} />
          {toast}
        </div>
      )}
    </div>
  );
}
