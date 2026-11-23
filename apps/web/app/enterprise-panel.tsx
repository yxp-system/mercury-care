"use client";

import { useCallback, useEffect, useState } from "react";

type Row = { id: string; [key: string]: unknown };
type Check = { name: string; passed: boolean; detail: string };

async function request<T>(path: string, body?: unknown): Promise<T> {
  const result = await fetch(`/api${path}`, { method: body ? "POST" : "GET", cache: "no-store",
    headers: body ? {"Content-Type":"application/json"} : undefined,
    body: body ? JSON.stringify(body) : undefined });
  const data = await result.json();
  if (!result.ok) throw new Error(data.error?.message || "请求失败");
  return data;
}

export default function EnterprisePanel({mode,roles}: {mode: "knowledge" | "operations"; roles: string[]}) {
  const [rows,setRows]=useState<Row[]>([]), [checks,setChecks]=useState<Check[]>([]);
  const [error,setError]=useState(""), [busy,setBusy]=useState(false), [notice,setNotice]=useState("");
  const [family,setFamily]=useState(""), [title,setTitle]=useState(""), [content,setContent]=useState("");
  const [kind,setKind]=useState("replacement"), [region,setRegion]=useState("GB");
  const [from,setFrom]=useState(""), [until,setUntil]=useState(""), [users,setUsers]=useState("");
  const [validationCases,setValidationCases]=useState(""), [note,setNote]=useState("");
  const read=useCallback(async () => {
    try {
      setRows(await request<Row[]>(mode === "knowledge" ? "/knowledge/documents" : "/escalations"));
      if (mode === "operations") setChecks((await request<{checks:Check[]}>("/release-readiness")).checks);
    } catch (e) { setError((e as Error).message); }
  },[mode]);
  useEffect(()=>{void read();},[read]);
  async function act(path:string,body:unknown) {
    setBusy(true);setError("");setNotice("");
    try {await request(path,body);setNotice("操作已保存，后台任务与审核状态可刷新查看。");await read();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section className="panel enterprise-panel">
    <div className="panel-heading"><div><h2>{mode === "knowledge" ? "文档导入与版本审核" : "超时交接与上线条件"}</h2>
    <p>{mode === "knowledge" ? "Markdown / 纯文本按章节建立索引，独立审核后发布。" : "超时交接不会撤销已经发生的业务动作。"}</p></div>
    <button className="secondary" onClick={()=>void read()}>刷新状态</button></div>
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
      <strong>{String(row.title || row.case_id)}</strong><p>{String(row.status)} · {mode === "knowledge" ? `版本 ${row.version}` : String(row.reason)}</p>
      {mode === "knowledge" && roles.includes("knowledge_reviewer") && ["draft","published"].includes(String(row.status)) &&
        <button className="secondary" disabled={busy || note.length<5 || !validationCases.trim()}
          onClick={()=>void act(`/knowledge/documents/${row.id}/${row.status === "published" ? "withdraw" : "publish"}`,
          {validation_cases:validationCases.split(",").map(value=>value.trim()).filter(Boolean),note})}>
          {row.status === "published" ? "撤回文档" : "审核发布"}</button>}
      {mode === "operations" && row.status === "open" && <button className="secondary" disabled={busy || note.length<5}
         onClick={()=>void act(`/escalations/${row.id}/acknowledge`,{reason:note})}>确认接管</button>}
    </div>)}
    {mode === "operations" && <><h3>上线门禁</h3><p className="muted">未验证的企业集成及部署验收保持阻塞，配置存在不等于已通过联调。</p>
      {checks.map(check=><div className="record-card" key={check.name}><strong>{check.passed ? "已具备配置" : "待完成"} · {check.name}</strong><p>{check.detail}</p></div>)}</>}
  </section>;
}
