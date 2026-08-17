const TARGET = 500;
const REPO = "simonkey888/ATM-Agent-Teller-Machine";
const OBS_ISSUE = 7;
const OBS_MARKER = "ATM OBSERVATORY STATE";
const SAFE_KEYS = new Set([
  "schema","authority","authority_epoch","cloud_heartbeat_at","runtime_sha","phase","state","paused",
  "realized_withdrawable_usd","monthly_target_usd","monthly_gap_usd","required_daily_run_rate_usd",
  "payouts_count","human_gate_count","human_gate_categories","active_task","oci_capacity","swarm","money_board",
  "last_result_status","cycle","owner_pc_in_production_graph","windows_authority","zero_spend","cloud_run_id",
  "host_class","source_sha","updated_at","controller_state","supervisor_state","supervisor_pid",
  "monthly_realized_withdrawable_usd","observed_7d_run_rate_usd","observed_30d_run_rate_usd","last_payment"
]);
function headers(type){return {"content-type":type,"cache-control":"no-store","x-content-type-options":"nosniff","referrer-policy":"no-referrer"}}
function json(data,status=200){return new Response(JSON.stringify(data,null,2)+"\n",{status,headers:headers("application/json; charset=utf-8")})}
function sanitize(raw){const out={};for(const [k,v] of Object.entries(raw||{})){if(SAFE_KEYS.has(k))out[k]=v}return out}
function cloudComment(body){
  const text=String(body||""); if(!text.startsWith(OBS_MARKER))return null;
  const m=text.match(/```json\s*([\s\S]*?)\s*```/); if(!m)return null;
  try{return sanitize(JSON.parse(m[1]))}catch{return null}
}
function legacyIssue(body){
  const line=String(body||"").split("\n").find(x=>x.startsWith("STATUS_JSON="));
  if(!line)return null; try{return sanitize(JSON.parse(line.slice(12)))}catch{return null}
}
async function gh(path){
  const r=await fetch(`https://api.github.com${path}`,{headers:{"Accept":"application/vnd.github+json","User-Agent":"atm-observatory/2"},cf:{cacheTtl:30,cacheEverything:true}});
  if(!r.ok)throw new Error(`github_${r.status}`); return r.json();
}
async function liveStatus(env){
  const fallback={authority:"UNOBSERVED",runtime_sha:env.ATM_GIT_SHA||"UNKNOWN",state:"UNKNOWN",monthly_target_usd:String(TARGET),zero_spend:true};
  try{
    const comments=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}/comments?per_page=100&page=1`);
    for(let i=comments.length-1;i>=0;i--){const found=cloudComment(comments[i].body);if(found)return found}
    const issue=await gh(`/repos/${REPO}/issues/${OBS_ISSUE}`); return legacyIssue(issue.body)||fallback;
  }catch{return fallback}
}
function esc(x){return String(x??"—").replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function compact(x){if(x===undefined||x===null)return "—";if(typeof x==="object")return JSON.stringify(x);return String(x)}
async function page(env){
  const s=await liveStatus(env);
  const realized=s.realized_withdrawable_usd??s.monthly_realized_withdrawable_usd;
  const runtime=s.runtime_sha??s.source_sha;
  const heartbeat=s.cloud_heartbeat_at??s.updated_at;
  const state=s.state??s.controller_state;
  const rows=[
    ["Authority",s.authority??s.host_class],["Authority epoch",s.authority_epoch],["State",state],["Phase",s.phase],["Paused",s.paused],
    ["Last cloud heartbeat",heartbeat],["Runtime SHA",runtime],["Cloud run",s.cloud_run_id],["Cycle",s.cycle],
    ["Target USD",s.monthly_target_usd],["Realized/withdrawable USD",realized],["Gap USD",s.monthly_gap_usd],["Required/day USD",s.required_daily_run_rate_usd],
    ["Active task",compact(s.active_task)],["OCI capacity",s.oci_capacity],["Swarm",compact(s.swarm)],["Money Board",compact(s.money_board)],
    ["Payments",s.payouts_count],["Human gates",s.human_gate_count],["Human gate categories",compact(s.human_gate_categories)],
    ["Last result",s.last_result_status],["Owner PC in prod graph",s.owner_pc_in_production_graph],["Windows authority",s.windows_authority],["Zero spend",s.zero_spend],
    ["Observatory build",env.ATM_GIT_SHA||"UNKNOWN"]
  ];
  const body=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>ATM Observatory</title><style>body{font:15px ui-monospace,SFMono-Regular,Consolas,monospace;max-width:900px;margin:28px auto;padding:0 16px;line-height:1.45}h1{font-size:24px}p{overflow-wrap:anywhere}.flag{font-weight:800}.grid{display:grid;grid-template-columns:minmax(180px,1fr) minmax(0,2.2fr);border-top:1px solid #bbb}.grid div{padding:8px 0;border-bottom:1px solid #ddd}.v{font-weight:700;overflow-wrap:anywhere}@media(max-width:620px){.grid{grid-template-columns:1fr}.grid .v{padding-top:0}}</style></head><body><h1>ATM Observatory</h1><p class="flag">HURRY UP, TIME IS MONEY.</p><div class="grid">${rows.map(([k,v])=>`<div>${esc(k)}</div><div class="v">${esc(v)}</div>`).join("")}</div><p>Read-only sanitized state. No wallet, token, prompt, local path, PAR URL, or private runtime data is published.</p></body></html>`;
  return new Response(body,{headers:{...headers("text/html; charset=utf-8"),"content-security-policy":"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}})
}
export default{async fetch(request,env){const u=new URL(request.url);if(!["GET","HEAD"].includes(request.method))return json({ok:false,error:"method_not_allowed"},405);if(u.pathname==="/health")return json({ok:true,service:"atm",git_sha:env.ATM_GIT_SHA||"UNKNOWN",month1_target_usd:TARGET,control_issue:4,observatory_issue:OBS_ISSUE});if(u.pathname==="/api/status")return json(await liveStatus(env));if(u.pathname==="/")return page(env);return json({ok:false,error:"not_found"},404)}};
