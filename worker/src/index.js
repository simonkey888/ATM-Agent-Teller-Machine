const TARGET = 500;
const REPO = "simonkey888/ATM-Agent-Teller-Machine";
const OBS_ISSUE = 7;
const OBS_MARKER = "ATM OBSERVATORY STATE";
const SAFE_KEYS = new Set([
  "schema","authority","authority_epoch","cloud_heartbeat_at","runtime_sha","phase","state","paused",
  "pending_usd","accepted_usd","settled_usd","realized_withdrawable_usd","monthly_target_usd","monthly_gap_usd","required_daily_run_rate_usd",
  "payouts_count","human_gate_count","human_gate_categories","active_task","oci_capacity","swarm","money_board",
  "last_result_status","cycle","owner_pc_in_production_graph","windows_authority","zero_spend","cloud_run_id",
  "host_class","source_sha","updated_at","controller_state","supervisor_state","supervisor_pid",
  "monthly_realized_withdrawable_usd","observed_7d_run_rate_usd","observed_30d_run_rate_usd","last_payment",
  "radar","platform_health"
]);
const SAFE_OPP = new Set(["id","source","url","title","category","payout_usd","payout_net","funding_status","competition","agent_policy","executor","disposition","money_velocity","human_gate","rejection_reasons"]);
const SAFE_PLATFORM = new Set(["source","state","checked_at","open_count","latency_ms","detail"]);
const SAFE_MONEY = new Set(["pending_usd","accepted_usd","settled_usd","withdrawable_usd","realized_withdrawable_usd"]);
function headers(type){return {"content-type":type,"cache-control":"no-store","x-content-type-options":"nosniff","referrer-policy":"no-referrer"}}
function json(data,status=200){return new Response(JSON.stringify(data,null,2)+"\n",{status,headers:headers("application/json; charset=utf-8")})}
function pick(raw,allow){const out={};for(const [k,v] of Object.entries(raw||{})){if(allow.has(k))out[k]=v}return out}
function sanitize(raw){
  const out={};for(const [k,v] of Object.entries(raw||{})){if(SAFE_KEYS.has(k))out[k]=v}
  if(out.money_board&&typeof out.money_board==="object")out.money_board=pick(out.money_board,SAFE_MONEY);
  if(out.radar&&typeof out.radar==="object"){
    const r=out.radar;out.radar={schema:r.schema,generated_at:r.generated_at,opportunity_count:r.opportunity_count,attack_now_count:r.attack_now_count,opportunities:Array.isArray(r.opportunities)?r.opportunities.slice(0,80).map(x=>pick(x,SAFE_OPP)):[]};
  }
  if(Array.isArray(out.platform_health))out.platform_health=out.platform_health.slice(0,32).map(x=>pick(x,SAFE_PLATFORM));
  return out;
}
function cloudComment(body){
  const text=String(body||""); if(!text.startsWith(OBS_MARKER))return null;
  const m=text.match(/```json\s*([\s\S]*?)\s*```/); if(!m)return null;
  try{return sanitize(JSON.parse(m[1]))}catch{return null}
}
function legacyIssue(body){const line=String(body||"").split("\n").find(x=>x.startsWith("STATUS_JSON="));if(!line)return null;try{return sanitize(JSON.parse(line.slice(12)))}catch{return null}}
async function gh(path){const r=await fetch(`https://api.github.com${path}`,{headers:{"Accept":"application/vnd.github+json","User-Agent":"atm-radar/1"},cf:{cacheTtl:30,cacheEverything:true}});if(!r.ok)throw new Error(`github_${r.status}`);return r.json()}
async function liveStatus(env){
  const fallback={authority:"UNOBSERVED",runtime_sha:env.ATM_GIT_SHA||"UNKNOWN",state:"UNKNOWN",monthly_target_usd:String(TARGET),zero_spend:true,money_board:{pending_usd:"0",accepted_usd:"0",settled_usd:"0",withdrawable_usd:"0"},radar:{opportunities:[]},platform_health:[]};
  try{const comments=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}/comments?per_page=100&page=1`);for(let i=comments.length-1;i>=0;i--){const found=cloudComment(comments[i].body);if(found)return found}const issue=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}`);return legacyIssue(issue.body)||fallback}catch{return fallback}
}
function moneyTruth(s){const b=s.money_board&&typeof s.money_board==="object"?s.money_board:{};return {pending_usd:String(s.pending_usd??b.pending_usd??"0"),accepted_usd:String(s.accepted_usd??b.accepted_usd??"0"),settled_usd:String(s.settled_usd??b.settled_usd??"0"),withdrawable_usd:String(s.realized_withdrawable_usd??s.monthly_realized_withdrawable_usd??b.withdrawable_usd??b.realized_withdrawable_usd??"0")}}
function esc(x){return String(x??"—").replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function compact(x){if(x===undefined||x===null)return "—";if(typeof x==="object")return JSON.stringify(x);return String(x)}
function amount(x){const n=Number(x?.payout_usd??x?.payout_net??0);return Number.isFinite(n)?n:0}
function bucket(opps,label){
  if(label==="ATTACK NOW")return opps.filter(x=>x.disposition==="ATTACK_NOW");
  if(label==="NEW")return opps.filter(x=>!Array.isArray(x.rejection_reasons)||x.rejection_reasons.length===0).slice(0,12);
  if(label==="$5–20")return opps.filter(x=>amount(x)>=5&&amount(x)<20);
  if(label==="$20–50")return opps.filter(x=>amount(x)>=20&&amount(x)<50);
  if(label==="$50–100")return opps.filter(x=>amount(x)>=50&&amount(x)<100);
  if(label==="$100–500")return opps.filter(x=>amount(x)>=100&&amount(x)<500);
  if(label==="$500+")return opps.filter(x=>amount(x)>=500);
  if(label==="AGENT_ONLY")return opps.filter(x=>x.agent_policy==="AGENT_ONLY");
  if(label==="LOW COMPETITION")return opps.filter(x=>Number(x.competition||0)<=1&&x.disposition!=="REJECT");
  if(label==="HUMAN GATE")return opps.filter(x=>x.human_gate);
  if(label==="MONITORING")return opps.filter(x=>x.disposition==="MONITOR");
  if(label==="REJECTED/STALE")return opps.filter(x=>x.disposition==="REJECT");
  return [];
}
function renderOpp(x){return `<article><div class="top"><b>${esc(x.title)}</b><span>${esc(x.payout_usd??x.payout_net??"?")} USD</span></div><div class="meta">${esc(x.source)} · ${esc(x.executor)} · ${esc(x.funding_status)} · c=${esc(x.competition??0)}</div><a href="${esc(x.url)}" rel="noreferrer noopener">source</a></article>`}
async function page(env){
  const s=await liveStatus(env);const money=moneyTruth(s);const runtime=s.runtime_sha??s.source_sha;const heartbeat=s.cloud_heartbeat_at??s.updated_at;const state=s.state??s.controller_state;const opps=Array.isArray(s.radar?.opportunities)?s.radar.opportunities:[];
  const rows=[["Authority",s.authority??s.host_class],["State",state],["Phase",s.phase],["Heartbeat",heartbeat],["Runtime SHA",runtime],["Cycle",s.cycle],["PENDING USD",money.pending_usd],["ACCEPTED USD",money.accepted_usd],["SETTLED USD",money.settled_usd],["WITHDRAWABLE USD",money.withdrawable_usd],["Target USD",s.monthly_target_usd],["Gap USD",s.monthly_gap_usd],["Attack now",s.radar?.attack_now_count??0],["Radar opportunities",s.radar?.opportunity_count??opps.length],["Human gates",s.human_gate_count],["Zero spend",s.zero_spend],["Build",env.ATM_GIT_SHA||"UNKNOWN"]];
  const labels=["ATTACK NOW","NEW","$5–20","$20–50","$50–100","$100–500","$500+","AGENT_ONLY","LOW COMPETITION","HUMAN GATE","MONITORING","PAID","REJECTED/STALE"];
  const sections=labels.map(label=>{const items=bucket(opps,label).slice(0,10);return `<section><h2>${esc(label)} <small>${items.length}</small></h2>${items.length?items.map(renderOpp).join(""):"<p class=muted>none observed</p>"}</section>`}).join("");
  const body=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>ATM Money Radar</title><style>body{font:14px ui-monospace,SFMono-Regular,Consolas,monospace;max-width:1080px;margin:24px auto;padding:0 14px;line-height:1.4}h1{font-size:24px;margin-bottom:6px}.flag{font-weight:800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:6px;margin:16px 0 24px}.grid div,article{border:1px solid #ddd;border-radius:8px;padding:9px}.grid b{display:block}.top{display:flex;gap:10px;justify-content:space-between}.top span{white-space:nowrap;font-weight:700}.meta,.muted{opacity:.7;font-size:12px}section{margin:24px 0}section h2{font-size:16px;border-bottom:1px solid #bbb;padding-bottom:5px}article{margin:7px 0}a{color:inherit}@media(max-width:620px){body{margin-top:14px}.top{display:block}.top span{display:block;margin-top:4px}}</style></head><body><h1>ATM Universal Money Radar</h1><p class="flag">SCAN → VERIFY → QUALIFY → ROUTE → EXECUTE → CHECK → PAYMENT VERIFY</p><div class="grid">${rows.map(([k,v])=>`<div>${esc(k)}<b>${esc(compact(v))}</b></div>`).join("")}</div>${sections}<p class=muted>Sanitized read-only view. External descriptions, credentials, claim codes, private wallet material, prompts, local paths and unredacted payloads are never published.</p></body></html>`;
  return new Response(body,{headers:{...headers("text/html; charset=utf-8"),"content-security-policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}})
}
function hex(bytes){return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,"0")).join("")}
async function verifyCoinPay(rawBytes,signature,secret){
  const parts={};for(const p of String(signature||"").split(",")){const i=p.indexOf("=");if(i>0)parts[p.slice(0,i)]=p.slice(i+1)}
  const t=Number(parts.t);const expected=String(parts.v1||"").toLowerCase();if(!Number.isInteger(t)||!/^[0-9a-f]{64}$/.test(expected))return false;if(Math.abs(Math.floor(Date.now()/1000)-t)>300)return false;
  const prefix=new TextEncoder().encode(`${t}.`);const payload=new Uint8Array(prefix.length+rawBytes.length);payload.set(prefix,0);payload.set(rawBytes,prefix.length);
  const key=await crypto.subtle.importKey("raw",new TextEncoder().encode(secret),{name:"HMAC",hash:"SHA-256"},false,["sign"]);const mac=await crypto.subtle.sign("HMAC",key,payload);const actual=hex(mac);let diff=actual.length^expected.length;for(let i=0;i<Math.min(actual.length,expected.length);i++)diff|=actual.charCodeAt(i)^expected.charCodeAt(i);return diff===0;
}
async function coinpayWebhook(request,env){
  if(!env.COINPAY_WEBHOOK_SECRET)return json({ok:false,error:"coinpay_disabled"},503);if(!env.ATM_RADAR_QUEUE||typeof env.ATM_RADAR_QUEUE.send!=="function")return json({ok:false,error:"queue_unavailable"},503);
  const rawBytes=new Uint8Array(await request.arrayBuffer());if(!(await verifyCoinPay(rawBytes,request.headers.get("x-coinpay-signature"),env.COINPAY_WEBHOOK_SECRET)))return json({ok:false,error:"invalid_signature"},401);
  let event;try{event=JSON.parse(new TextDecoder().decode(rawBytes))}catch{return json({ok:false,error:"invalid_json"},400)}const allowed=new Set(["payment.confirmed","payment.forwarded","escrow.settled"]);if(!allowed.has(String(event.type||"")))return json({ok:true,ignored:true},202);
  const data=event.data&&typeof event.data==="object"?event.data:{};const safe={id:String(event.id||""),type:String(event.type||""),created_at:String(event.created_at||""),data:{payment_id:String(data.payment_id||data.escrow_id||""),status:String(data.status||""),amount_usd:String(data.amount_usd||""),currency:String(data.currency||""),tx_hash:String(data.tx_hash||"")}};await env.ATM_RADAR_QUEUE.send(safe);return json({ok:true,queued:true},202);
}
export default{async fetch(request,env){const u=new URL(request.url);if(request.method==="POST"&&u.pathname==="/webhooks/coinpay")return coinpayWebhook(request,env);if(!["GET","HEAD"].includes(request.method))return json({ok:false,error:"method_not_allowed"},405);const s=async()=>await liveStatus(env);if(u.pathname==="/health")return json({ok:true,service:"atm",mode:"radar",git_sha:env.ATM_GIT_SHA||"UNKNOWN",month1_target_usd:TARGET,control_issue:4,observatory_issue:OBS_ISSUE});if(u.pathname==="/api/status")return json(await s());if(u.pathname==="/api/opportunities"){const state=await s();return json({generated_at:state.radar?.generated_at||null,opportunities:state.radar?.opportunities||[]})}if(u.pathname==="/api/platform-health"){const state=await s();return json({platforms:state.platform_health||[]})}if(u.pathname==="/api/money-board"){const state=await s();return json(moneyTruth(state))}if(u.pathname==="/")return page(env);return json({ok:false,error:"not_found"},404)}};
