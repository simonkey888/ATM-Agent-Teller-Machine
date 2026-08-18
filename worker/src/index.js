const TARGET = 500;
const REPO = "simonkey888/ATM-Agent-Teller-Machine";
const OBS_ISSUE = 7;
const OBS_MARKER = "ATM OBSERVATORY STATE";
const RADAR_MARKER = "ATM UNIVERSAL RADAR SNAPSHOT";
const SAFE_KEYS = new Set([
  "schema","authority","authority_epoch","cloud_heartbeat_at","runtime_sha","phase","state","paused",
  "pending_usd","accepted_usd","settled_usd","realized_withdrawable_usd","monthly_target_usd","monthly_gap_usd","required_daily_run_rate_usd",
  "payouts_count","human_gate_count","human_gate_categories","active_task","oci_capacity","swarm","money_board",
  "last_result_status","cycle","owner_pc_in_production_graph","windows_authority","zero_spend","cloud_run_id",
  "host_class","source_sha","updated_at","controller_state","supervisor_state","supervisor_pid",
  "monthly_realized_withdrawable_usd","observed_7d_run_rate_usd","observed_30d_run_rate_usd","last_payment",
  "radar","platform_health","rail_capabilities"
]);
const SAFE_OPP = new Set(["id","source","source_object_type","url","title","category","observed_at","deadline","payout_usd","payout_net","currency","funding_status","funding_state","competition","claims","submissions","open_prs","agent_policy","eligibility","human_gate","executor","executor_health","freshness_state","disposition","money_velocity","payment_rail","withdrawal_path","rejection_reasons","payment_state","authoritative_paid","source_state_hash"]);
const SAFE_PLATFORM = new Set(["source","state","checked_at","open_count","latency_ms","detail"]);
const SAFE_RAIL = new Set(["source","kind","job_counted","outgoing_spend_usd","autonomous_buying","authoritative_funding"]);
const SAFE_BOARD = new Set(["pending_usd","accepted_usd","settled_usd","withdrawable_usd","realized_withdrawable_usd","active_leases","states","eligible_provenance"]);
const SAFE_PROVENANCE = new Set(["canonical_id","source","status","verified_at","falsifier_verdict","scout_sources_json"]);
function headers(type){return {"content-type":type,"cache-control":"no-store","x-content-type-options":"nosniff","referrer-policy":"no-referrer"}}
function json(data,status=200){return new Response(JSON.stringify(data,null,2)+"\n",{status,headers:headers("application/json; charset=utf-8")})}
function pick(raw,allow){const out={};for(const [k,v] of Object.entries(raw||{})){if(allow.has(k))out[k]=v}return out}
function sanitizeBoard(raw){const out=pick(raw||{},SAFE_BOARD);if(Array.isArray(out.eligible_provenance))out.eligible_provenance=out.eligible_provenance.slice(0,24).map(x=>pick(x,SAFE_PROVENANCE));if(out.states&&typeof out.states==="object")out.states=Object.fromEntries(Object.entries(out.states).slice(0,24).map(([k,v])=>[String(k).slice(0,40),Number.isFinite(Number(v))?Number(v):String(v).slice(0,40)]));return out}
function sanitizeRadar(raw){
  if(!raw||typeof raw!=="object")return null;
  return {
    schema:String(raw.schema||"ATM_UNIVERSAL_RADAR_PUBLIC_V2"),source_sha:String(raw.source_sha||"UNKNOWN"),source_branch:String(raw.source_branch||""),generated_at:raw.generated_at||null,
    outgoing_spend_usd:String(raw.outgoing_spend_usd??"UNKNOWN"),source_count:Number(raw.source_count||0),radar_sources:Array.isArray(raw.radar_sources)?raw.radar_sources.slice(0,64).map(String):[],
    opportunity_count:Number(raw.opportunity_count||0),attack_now_count:Number(raw.attack_now_count||0),opportunities:Array.isArray(raw.opportunities)?raw.opportunities.slice(0,80).map(x=>pick(x,SAFE_OPP)):[]
  };
}
function sanitize(raw){
  const out={};for(const [k,v] of Object.entries(raw||{})){if(SAFE_KEYS.has(k))out[k]=v}
  if(out.money_board&&typeof out.money_board==="object")out.money_board=sanitizeBoard(out.money_board);
  if(out.radar&&typeof out.radar==="object")out.radar=sanitizeRadar(out.radar);
  if(Array.isArray(out.platform_health))out.platform_health=out.platform_health.slice(0,64).map(x=>pick(x,SAFE_PLATFORM));
  if(Array.isArray(out.rail_capabilities))out.rail_capabilities=out.rail_capabilities.slice(0,32).map(x=>pick(x,SAFE_RAIL));
  return out;
}
function fenced(body,marker){const text=String(body||"");if(!text.startsWith(marker))return null;const m=text.match(/```json\s*([\s\S]*?)\s*```/);if(!m)return null;try{return JSON.parse(m[1])}catch{return null}}
function cloudComment(body){const raw=fenced(body,OBS_MARKER);return raw?sanitize(raw):null}
function radarComment(body){const raw=fenced(body,RADAR_MARKER);if(!raw)return null;return {radar:sanitizeRadar(raw),platform_health:Array.isArray(raw.platform_health)?raw.platform_health.slice(0,64).map(x=>pick(x,SAFE_PLATFORM)):[],rail_capabilities:Array.isArray(raw.rail_capabilities)?raw.rail_capabilities.slice(0,32).map(x=>pick(x,SAFE_RAIL)):[]}}
function legacyIssue(body){const line=String(body||"").split("\n").find(x=>x.startsWith("STATUS_JSON="));if(!line)return null;try{return sanitize(JSON.parse(line.slice(12)))}catch{return null}}
async function gh(path){const r=await fetch(`https://api.github.com${path}`,{headers:{"Accept":"application/vnd.github+json","User-Agent":"atm-radar/2"},cf:{cacheTtl:20,cacheEverything:true}});if(!r.ok)throw new Error(`github_${r.status}`);return r.json()}
async function liveStatus(env){
  const fallback={authority:"UNOBSERVED",runtime_sha:"UNKNOWN",state:"UNKNOWN",monthly_target_usd:String(TARGET),zero_spend:true,money_board:{},radar:{schema:"ATM_UNIVERSAL_RADAR_PUBLIC_V2",source_sha:env.ATM_GIT_SHA||"UNKNOWN",opportunities:[],opportunity_count:0,attack_now_count:0},platform_health:[],rail_capabilities:[]};
  try{
    const comments=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}/comments?per_page=100&page=1`);let obs=null,radar=null;
    for(let i=comments.length-1;i>=0;i--){if(!obs)obs=cloudComment(comments[i].body);if(!radar)radar=radarComment(comments[i].body);if(obs&&radar)break}
    if(!obs){const issue=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}`);obs=legacyIssue(issue.body)}
    const merged={...(obs||fallback)};if(radar){merged.radar=radar.radar;merged.platform_health=radar.platform_health;merged.rail_capabilities=radar.rail_capabilities}return sanitize(merged);
  }catch{return fallback}
}
function known(...values){for(const v of values){if(v!==undefined&&v!==null&&String(v)!=="")return String(v)}return "UNKNOWN"}
function moneyTruth(s){const b=s.money_board&&typeof s.money_board==="object"?s.money_board:{};return {pending_usd:known(s.pending_usd,b.pending_usd),accepted_usd:known(s.accepted_usd,b.accepted_usd),settled_usd:known(s.settled_usd,b.settled_usd),withdrawable_usd:known(s.realized_withdrawable_usd,s.monthly_realized_withdrawable_usd,b.withdrawable_usd,b.realized_withdrawable_usd)}}
function esc(x){return String(x??"—").replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function compact(x){if(x===undefined||x===null)return "—";if(typeof x==="object")return JSON.stringify(x);return String(x)}
function amount(x){const n=Number(x?.payout_usd??x?.payout_net);return Number.isFinite(n)?n:null}
function bucket(opps,label){
  if(label==="ATTACK NOW")return opps.filter(x=>x.disposition==="ATTACK_NOW");
  if(label==="NEW")return opps.filter(x=>x.freshness_state==="CURRENT"&&x.disposition!=="REJECT").slice(0,12);
  if(label==="$5–20")return opps.filter(x=>amount(x)!==null&&amount(x)>=5&&amount(x)<20);
  if(label==="$20–50")return opps.filter(x=>amount(x)!==null&&amount(x)>=20&&amount(x)<50);
  if(label==="$50–100")return opps.filter(x=>amount(x)!==null&&amount(x)>=50&&amount(x)<100);
  if(label==="$100–500")return opps.filter(x=>amount(x)!==null&&amount(x)>=100&&amount(x)<500);
  if(label==="$500+")return opps.filter(x=>amount(x)!==null&&amount(x)>=500);
  if(label==="AGENT_ONLY")return opps.filter(x=>x.agent_policy==="AGENT_ONLY");
  if(label==="LOW COMPETITION")return opps.filter(x=>Number(x.competition||0)<=1&&x.disposition!=="REJECT");
  if(label==="HUMAN GATE")return opps.filter(x=>x.human_gate);
  if(label==="MONITORING")return opps.filter(x=>x.disposition==="MONITOR"||x.funding_state==="SIGNAL_ONLY");
  if(label==="PAID")return opps.filter(x=>x.authoritative_paid===true&&x.payment_state==="WITHDRAWABLE");
  if(label==="REJECTED/STALE")return opps.filter(x=>x.disposition==="REJECT"||x.freshness_state==="STALE"||x.freshness_state==="EXPIRED");
  return [];
}
function renderOpp(x){const a=amount(x);return `<article><div class="top"><b>${esc(x.title)}</b><span>${esc(a===null?"?":a)} USD</span></div><div class="meta">${esc(x.source)} · ${esc(x.executor)} · ${esc(x.funding_state||x.funding_status)} · c=${esc(x.competition??0)}</div><a href="${esc(x.url)}" rel="noreferrer noopener">source</a></article>`}
function renderHealth(rows,rails){const p=(rows||[]).slice(0,40).map(x=>`<div><b>${esc(x.source)}</b><span>${esc(x.state)} · open=${esc(x.open_count??"UNKNOWN")} · ${esc(x.detail||"")}</span></div>`).join("");const r=(rails||[]).slice(0,20).map(x=>`<div><b>${esc(x.source)}</b><span>${esc(x.kind)} · job_counted=${esc(x.job_counted)}</span></div>`).join("");return `<section><h2>PLATFORM HEALTH</h2><div class="health">${p||"<div>no platform evidence</div>"}</div><h2>RAIL CAPABILITIES</h2><div class="health">${r||"<div>no rail evidence</div>"}</div></section>`}
async function page(env){
  const s=await liveStatus(env),money=moneyTruth(s),runtime=s.runtime_sha??s.source_sha,heartbeat=s.cloud_heartbeat_at??s.updated_at,state=s.state??s.controller_state,opps=Array.isArray(s.radar?.opportunities)?s.radar.opportunities:[];
  const rows=[["Authority",s.authority??s.host_class],["State",state],["Phase",s.phase],["Heartbeat",heartbeat],["Economic runtime SHA",runtime],["Radar source SHA",s.radar?.source_sha],["Deployed Worker SHA",env.ATM_GIT_SHA||"UNKNOWN"],["Cycle",s.cycle],["PENDING USD",money.pending_usd],["ACCEPTED USD",money.accepted_usd],["SETTLED USD",money.settled_usd],["WITHDRAWABLE USD",money.withdrawable_usd],["Target USD",s.monthly_target_usd],["Gap USD",s.monthly_gap_usd],["Attack now",s.radar?.attack_now_count??0],["Radar opportunities",s.radar?.opportunity_count??opps.length],["Radar sources",s.radar?.source_count??0],["Human gates",s.human_gate_count],["Zero spend",s.zero_spend]];
  const labels=["ATTACK NOW","NEW","$5–20","$20–50","$50–100","$100–500","$500+","AGENT_ONLY","LOW COMPETITION","HUMAN GATE","MONITORING","PAID","REJECTED/STALE"];
  const sections=labels.map(label=>{const items=bucket(opps,label).slice(0,10);return `<section><h2>${esc(label)} <small>${items.length}</small></h2>${items.length?items.map(renderOpp).join(""):"<p class=muted>none observed</p>"}</section>`}).join("");
  const body=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>ATM Universal Money Radar</title><style>body{font:14px ui-monospace,SFMono-Regular,Consolas,monospace;max-width:1120px;margin:24px auto;padding:0 14px;line-height:1.4}h1{font-size:24px;margin-bottom:6px}.flag{font-weight:800}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:6px;margin:16px 0 24px}.grid div,article,.health div{border:1px solid #ddd;border-radius:8px;padding:9px}.grid b,.health b{display:block}.top{display:flex;gap:10px;justify-content:space-between}.top span{white-space:nowrap;font-weight:700}.meta,.muted,.health span{opacity:.72;font-size:12px}section{margin:24px 0}section h2{font-size:16px;border-bottom:1px solid #bbb;padding-bottom:5px}.health{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:6px}article{margin:7px 0}a{color:inherit}@media(max-width:620px){body{margin-top:14px}.top{display:block}.top span{display:block;margin-top:4px}}</style></head><body><h1>ATM Universal Money Radar</h1><p class="flag">SCAN → VERIFY → QUALIFY → ROUTE → EXECUTE → CHECK → PAYMENT VERIFY</p><div class="grid">${rows.map(([k,v])=>`<div>${esc(k)}<b>${esc(compact(v))}</b></div>`).join("")}</div>${sections}${renderHealth(s.platform_health,s.rail_capabilities)}<p class=muted>Sanitized read-only view. External descriptions, credentials, claim codes, private wallet material, prompts, local paths and raw third-party payloads are not published.</p></body></html>`;
  return new Response(body,{headers:{...headers("text/html; charset=utf-8"),"content-security-policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}})
}
function hex(bytes){return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,"0")).join("")}
async function verifyCoinPay(rawBytes,signature,secret){const parts={};for(const p of String(signature||"").split(",")){const i=p.indexOf("=");if(i>0)parts[p.slice(0,i)]=p.slice(i+1)}const t=Number(parts.t),expected=String(parts.v1||"").toLowerCase();if(!Number.isInteger(t)||!/^[0-9a-f]{64}$/.test(expected))return false;if(Math.abs(Math.floor(Date.now()/1000)-t)>300)return false;const prefix=new TextEncoder().encode(`${t}.`),payload=new Uint8Array(prefix.length+rawBytes.length);payload.set(prefix,0);payload.set(rawBytes,prefix.length);const key=await crypto.subtle.importKey("raw",new TextEncoder().encode(secret),{name:"HMAC",hash:"SHA-256"},false,["sign"]);const mac=await crypto.subtle.sign("HMAC",key,payload),actual=hex(mac);let diff=actual.length^expected.length;for(let i=0;i<Math.min(actual.length,expected.length);i++)diff|=actual.charCodeAt(i)^expected.charCodeAt(i);return diff===0}
async function coinpayWebhook(request,env){if(!env.COINPAY_WEBHOOK_SECRET)return json({ok:false,error:"coinpay_disabled"},503);if(!env.ATM_RADAR_QUEUE||typeof env.ATM_RADAR_QUEUE.send!=="function")return json({ok:false,error:"queue_unavailable"},503);const rawBytes=new Uint8Array(await request.arrayBuffer());if(!(await verifyCoinPay(rawBytes,request.headers.get("x-coinpay-signature"),env.COINPAY_WEBHOOK_SECRET)))return json({ok:false,error:"invalid_signature"},401);let event;try{event=JSON.parse(new TextDecoder().decode(rawBytes))}catch{return json({ok:false,error:"invalid_json"},400)}const allowed=new Set(["payment.confirmed","payment.forwarded","escrow.settled"]);if(!allowed.has(String(event.type||"")))return json({ok:true,ignored:true},202);const data=event.data&&typeof event.data==="object"?event.data:{};const safe={id:String(event.id||""),type:String(event.type||""),created_at:String(event.created_at||""),data:{payment_id:String(data.payment_id||data.escrow_id||""),status:String(data.status||""),amount_usd:String(data.amount_usd||""),currency:String(data.currency||""),tx_hash:String(data.tx_hash||"")}};await env.ATM_RADAR_QUEUE.send(safe);return json({ok:true,queued:true},202)}
export default{async fetch(request,env){const u=new URL(request.url);if(request.method==="POST"&&u.pathname==="/webhooks/coinpay")return coinpayWebhook(request,env);if(!["GET","HEAD"].includes(request.method))return json({ok:false,error:"method_not_allowed"},405);const s=async()=>await liveStatus(env);if(u.pathname==="/health")return json({ok:true,service:"atm",mode:"universal-money-radar",git_sha:env.ATM_GIT_SHA||"UNKNOWN",month1_target_usd:TARGET,control_issue:4,observatory_issue:OBS_ISSUE});if(u.pathname==="/api/status")return json(await s());if(u.pathname==="/api/opportunities"){const state=await s();return json({source_sha:state.radar?.source_sha||null,generated_at:state.radar?.generated_at||null,opportunities:state.radar?.opportunities||[]})}if(u.pathname==="/api/platform-health"){const state=await s();return json({source_sha:state.radar?.source_sha||null,platforms:state.platform_health||[],rails:state.rail_capabilities||[]})}if(u.pathname==="/api/money-board"){const state=await s();return json({stages:moneyTruth(state),evidence:sanitizeBoard(state.money_board||{})})}if(u.pathname==="/")return page(env);return json({ok:false,error:"not_found"},404)}};
