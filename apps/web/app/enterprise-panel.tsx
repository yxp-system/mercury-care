"use client";

import { useCallback, useEffect, useState } from "react";

type Row = { id: string; [key: string]: unknown };
type Check = {
  name: string; passed: boolean; configured: boolean; detail: string; evidence_status: string;
  owner?: string | null; provider?: string | null; evidence_ref?: string | null; expires_at?: number | null;
};
type Evidence = Row & {
  check_name: string; environment: string; provider: string; owner_id: string;
  evidence_ref: string; summary: string; status: string; expires_at: number;
  reviewer_id?: string | null; reviewed_at?: number | null; review_note?: string;
};
const statusLabels: Record<string, string> = {
  queued: "排队中", indexing: "索引中", draft: "待审核", published: "已发布",
  withdrawn: "已撤回", failed: "导入失败", open: "待接管", acknowledged: "已接管",
  submitted: "待独立审核", accepted: "验收通过", rejected: "已退回", missing: "缺少凭据", expired: "已过期",
};
const checkLabels: Record<string, string> = {
  database_migrations: "数据库迁移", runtime_database_role: "数据库运行权限",
  enterprise_identity: "企业登录", attachment_scanner: "附件安全扫描", real_models: "真实模型与检索",
  integration_OMS: "订单系统", integration_WMS: "仓储系统", integration_payments: "支付与退款",
  integration_logistics: "物流服务", integration_notifications: "客户通知", deployment_acceptance: "部署与业务验收",
  data_governance: "数据保留与安全责任", model_quality: "模型质量评测",
};
function checkDetail(check: Check) {
  if (check.name === "database_migrations") return check.passed ? `当前迁移版本：${check.detail}` : "数据库不可用或迁移版本未达到要求。";
  if (check.name.startsWith("integration_")) return "服务商与官方接口尚未选定，真实业务接入待完成。";
  return ({
    runtime_database_role: check.passed ? "已使用受限数据库账号，仍需验证目标环境的数据隔离。" : "需部署 PostgreSQL，并使用非所有者、不能绕过行级权限的运行账号。",
    enterprise_identity: check.passed ? "企业登录配置已具备，提供方联调与验收仍需完成。" : "企业登录尚未配置，需选定身份提供方并完成联调。",
    attachment_scanner: check.passed ? "已配置 ClamAV，仍需验证部署可用性与病毒库更新。" : "需配置 ClamAV，并验证扫描服务与隔离流程。",
    real_models: check.passed ? "已配置真实模型与嵌入，质量与成本验收仍需完成。" : "需配置真实模型与嵌入，并完成质量、检索与成本评测。",
    deployment_acceptance: "目标环境未定，需完成实际部署、备份恢复、故障切换和业务验收。",
    data_governance: "需提交经组织批准的分类保留/删除期限、法律保留流程及安全事故负责人。",
    model_quality: "需用至少 30 条脱敏、人工复核的代表性样本评测事实引用、规则适用和越权/未批准动作率。",
  } as Record<string,string>)[check.name] || check.detail;
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const result = await fetch(`/api${path}`, { method: body ? "POST" : "GET", cache: "no-store",
    headers: body ? {"Content-Type":"application/json"} : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const data = await result.json();
  if (!result.ok) throw new Error(data.error?.message || "请求失败");
  return data;
}

export default function EnterprisePanel({mode,roles,onUpdated}: {
  mode: "knowledge" | "operations"; roles: string[]; onUpdated?: () => Promise<void>;
}) {
  const [rows,setRows]=useState<Row[]>([]), [checks,setChecks]=useState<Check[]>([]);
  const [evidence,setEvidence]=useState<Evidence[]>([]);
  const [error,setError]=useState(""), [busy,setBusy]=useState(false), [notice,setNotice]=useState("");
  const [family,setFamily]=useState(""), [title,setTitle]=useState(""), [content,setContent]=useState("");
  const [kind,setKind]=useState("replacement"), [region,setRegion]=useState("GB");
  const [from,setFrom]=useState(""), [until,setUntil]=useState(""), [users,setUsers]=useState("");
  const [validationCases,setValidationCases]=useState(""), [note,setNote]=useState("");
  const [evidenceForm,setEvidenceForm]=useState({check_name:"deployment_acceptance",environment:"staging",provider:"",owner_id:"ops",evidence_ref:"",summary:"",tested_date:new Date().toISOString().slice(0,10)});
  const read=useCallback(async () => {
    try {
      if (mode === "knowledge") setRows(await request<Row[]>("/knowledge/documents"));
      if (mode === "operations") {
        const requests: Promise<unknown>[] = [
          request<{checks:Check[]}>("/release-readiness"),request<Evidence[]>("/release-readiness/evidence")
        ];
        if (roles.includes("operations")) requests.push(request<Row[]>("/escalations"));
        const [readiness,records,escalations] = await Promise.all(requests) as [
          {checks:Check[]}, Evidence[], Row[] | undefined
        ];
        setChecks(readiness.checks);setEvidence(records);setRows(escalations || []);
      }
      setError("");
    } catch (e) { setError((e as Error).message); }
  },[mode,roles]);
  useEffect(()=>{void read();},[read]);
  async function act(path:string,body:unknown) {
    setBusy(true);setError("");setNotice("");
    try {await request(path,body);setNotice("操作已保存，后台任务与审核状态可刷新查看。");await read();await onUpdated?.();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section className="panel enterprise-panel">
    <div className="panel-heading"><div><h2>{mode === "knowledge" ? "文档导入与版本审核" : "超时交接与上线条件"}</h2>
    <p>{mode === "knowledge" ? "Markdown / 纯文本按章节建立索引，独立审核后发布。" : "超时交接不会撤销已经发生的业务动作。"}</p></div>
    <button className="secondary" onClick={()=>void (async()=>{
      await read();try{await onUpdated?.();}catch(e){setError((e as Error).message);}
    })()}>刷新状态</button></div>
    {error && <p className="error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {mode === "knowledge" && roles.includes("knowledge") && <form onSubmit={(event)=>{
      event.preventDefault();void act("/knowledge/documents",{family,title,content,kind,region,
      effective_from:Date.parse(from)/1000,effective_until:Date.parse(until)/1000,
      allowed_users:users.split(",").map(value=>value.trim()).filter(Boolean),allowed_roles:[]});}}>
      <div className="enterprise-grid">
      <label>文档标识<input required minLength={3} maxLength={80} value={family} onChange={e=>setFamily(e.target.value)}/></label>
      <label>标题<input required minLength={3} maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label>
      <label>适用类型<select value={kind} onChange={e=>setKind(e.target.value)}>
        {Object.entries({replacement:"破损补发",refund:"退款",return:"退货",delivery:"物流",missing:"少件",wrong:"错件",cancellation:"取消",address:"改址"}).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
      <label>地区代码<input required pattern="[A-Z]{2}" value={region} onChange={e=>setRegion(e.target.value)}/></label>
      <label>生效日期<input required type="date" value={from} onChange={e=>setFrom(e.target.value)}/></label>
      <label>失效日期<input required type="date" value={until} onChange={e=>setUntil(e.target.value)}/></label>
      </div>
      <label>允许的人员 ID（逗号分隔，留空为本店铺授权人员）<input value={users} onChange={e=>setUsers(e.target.value)}/></label>
      <label>文档正文<textarea required minLength={20} maxLength={200000} rows={8} value={content} onChange={e=>setContent(e.target.value)}/></label>
      <button className="primary" disabled={busy}>提交文档导入</button>
    </form>}
    <div className="enterprise-grid">
      {mode === "knowledge" && roles.includes("knowledge_reviewer") && <label>验证工单 ID（逗号分隔）<input value={validationCases} onChange={e=>setValidationCases(e.target.value)}/></label>}
      <label>审核或交接说明<input minLength={5} value={note} onChange={e=>setNote(e.target.value)}/></label>
    </div>
    {rows.map(row=><div className="record-card" key={row.id}>
      <strong>{String(row.title || row.case_id)}</strong><p>{statusLabels[String(row.status)] || String(row.status)} · {mode === "knowledge" ? `版本 ${row.version}` : String(row.reason)}</p>
      {mode === "knowledge" && roles.includes("knowledge_reviewer") && ["draft","published"].includes(String(row.status)) &&
        <button className="secondary" disabled={busy || note.length<5 || !validationCases.trim()}
          onClick={()=>void act(`/knowledge/documents/${row.id}/${row.status === "published" ? "withdraw" : "publish"}`,
          {validation_cases:validationCases.split(",").map(value=>value.trim()).filter(Boolean),note})}>
          {row.status === "published" ? "撤回文档" : "审核发布"}</button>}
      {mode === "operations" && row.status === "open" && <button className="secondary" disabled={busy || note.length<5}
         onClick={()=>void act(`/escalations/${row.id}/acknowledge`,{reason:note})}>确认接管</button>}
    </div>)}
    {mode === "operations" && <>
      {roles.includes("operations") && <form onSubmit={event=>{
        event.preventDefault();const tested=new Date(evidenceForm.tested_date+"T12:00:00Z").getTime()/1000;
        void act("/release-readiness/evidence",{...evidenceForm,tested_at:tested,expires_at:tested+90*86400});
      }}>
        <h3>提交可复核的上线证据</h3><p className="muted">每项证据须指定有效成员负责，由独立的上线审核岗位复核。凭据编号使用受控系统中的引用，不填写密钥或客户数据。</p>
        <div className="enterprise-grid">
          <label>验收检查项<select value={evidenceForm.check_name} onChange={e=>setEvidenceForm({...evidenceForm,check_name:e.target.value})}>
            {Object.entries(checkLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}
          </select></label>
          <label>目标环境<select value={evidenceForm.environment} onChange={e=>setEvidenceForm({...evidenceForm,environment:e.target.value})}>
            <option value="staging">预发布</option><option value="production">生产</option>
          </select></label>
          <label>供应商或部署项<input required minLength={2} maxLength={160} value={evidenceForm.provider} onChange={e=>setEvidenceForm({...evidenceForm,provider:e.target.value})}/></label>
          <label>责任成员 ID<input required minLength={2} maxLength={80} value={evidenceForm.owner_id} onChange={e=>setEvidenceForm({...evidenceForm,owner_id:e.target.value})}/></label>
          <label>测试日期<input required type="date" value={evidenceForm.tested_date} onChange={e=>setEvidenceForm({...evidenceForm,tested_date:e.target.value})}/></label>
          <label>凭据有效期<input readOnly value="提交后 90 天"/></label>
        </div>
        <label>受控工单或资料编号<input required minLength={3} maxLength={300} value={evidenceForm.evidence_ref} onChange={e=>setEvidenceForm({...evidenceForm,evidence_ref:e.target.value})}/></label>
        <label>测试范围与结果摘要<textarea required minLength={10} maxLength={2000} rows={3} value={evidenceForm.summary} onChange={e=>setEvidenceForm({...evidenceForm,summary:e.target.value})}/></label>
        <button className="primary" disabled={busy}>提交供独立复核</button>
      </form>}
      <h3>审核队列与历史凭据</h3>
      {evidence.map(item=><div className="record-card" key={item.id}>
        <strong>{statusLabels[item.status] || item.status} · {checkLabels[item.check_name] || item.check_name}</strong>
        <p>{item.environment === "production" ? "生产" : "预发布"} · {item.provider} · 责任人 {item.owner_id} · {item.evidence_ref}</p>
        <p>{item.summary}</p><small>有效至 {new Date(item.expires_at*1000).toLocaleDateString()}</small>
        {roles.includes("release_reviewer") && item.status === "submitted" && <div className="button-row">
          <button className="secondary" disabled={busy || note.length<10} onClick={()=>void act(`/release-readiness/evidence/${item.id}/review`,{decision:"accepted",note})}>独立验收通过</button>
          <button className="secondary" disabled={busy || note.length<10} onClick={()=>void act(`/release-readiness/evidence/${item.id}/review`,{decision:"rejected",note})}>退回整改</button>
        </div>}
        {item.reviewer_id && <small>复核人 {item.reviewer_id} · {String(item.reviewed_at ? new Date(item.reviewed_at*1000).toLocaleString() : "")} · {String(item.review_note)}</small>}
      </div>)}
      <h3>上线门禁</h3><p className="muted">“凭据通过”和“能力已接入”分别核验；未完成提供方适配的项目不会因审核资料而解锁。</p>
      {checks.map(check=><div className="record-card" key={check.name}><strong>{check.passed ? "验收通过" : "待完成"} · {checkLabels[check.name] || check.name}</strong><p>{checkDetail(check)} · 证据：{statusLabels[check.evidence_status] || check.evidence_status}</p>{check.owner && <small>责任人 {check.owner} · {check.provider} · {check.evidence_ref} · 有效至 {new Date((check.expires_at||0)*1000).toLocaleDateString()}</small>}</div>)}</>}
  </section>;
}
