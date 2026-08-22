import x402 from "./x402-entry.js";
import {handleHumanGateRequest,humanGateCss,publicHumanGateSummary,renderHumanGatePanel,runtimeHumanGates} from "./order021-human-gate.js";
import {handleOwnerInbox} from "./order021-owner-inbox.js";

const HUMAN_GATE_ORIGIN="https://atm.simondalmasso44.workers.dev";
function noStore(response,body,type){const h=new Headers(response.headers);h.set("content-type",type);h.set("cache-control","no-store");h.set("x-content-type-options","nosniff");h.set("referrer-policy","no-referrer");return new Response(body,{status:response.status,statusText:response.statusText,headers:h})}
function boundaryError(error,status=400){return new Response(JSON.stringify({ok:false,error})+"\n",{status,headers:{"content-type":"application/json; charset=utf-8","cache-control":"no-store","x-content-type-options":"nosniff","referrer-policy":"no-referrer"}})}
function isHumanGateMutation(path,method){return method==="POST"&&path.startsWith("/api/human-gates/")}
async function enforceOwnerMutationBoundary(request,nowMs=Date.now()){
  const url=new URL(request.url);
  if(!isHumanGateMutation(url.pathname,request.method))return null;
  const origin=String(request.headers.get("origin")||"");
  if(origin&&origin!==HUMAN_GATE_ORIGIN)return boundaryError("owner_origin_mismatch",403);
  if(!/^\/api\/human-gates\/[^/]+\/(?:opened|owner-action)$/.test(url.pathname))return null;
  if(!String(request.headers.get("authorization")||"").startsWith("Bearer "))return null;
  let body;try{body=await request.clone().json()}catch{return null}
  const exp=Number(body?.expires_at||0),now=Math.floor(nowMs/1000);
  if(!Number.isInteger(exp)||exp<now||exp>now+180)return boundaryError("owner_action_envelope_expired",400);
  return null;
}
async function dashboard(request,env,ctx){const base=await x402.fetch(request,env,ctx);if(!base.ok)return base;let html=await base.text();const summary=publicHumanGateSummary(await runtimeHumanGates(env));html=html.replace("</style>",`${humanGateCss()}</style>`);html=html.replace("</div><p class=\"meta\">",`${renderHumanGatePanel(summary)}</div><p class=\"meta\">`);if(summary.pending_count>0)html=html.replace("</body>",'<script src="/human-gate-owner.js"></script></body>');const r=noStore(base,html,"text/html; charset=utf-8");r.headers.set("content-security-policy","default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");return r}
async function status(request,env,ctx){const base=await x402.fetch(request,env,ctx);if(!base.ok)return base;let data;try{data=await base.json()}catch{return base}const gates=await runtimeHumanGates(env);data.human_gate=publicHumanGateSummary(gates);data.human_gate_pending=data.human_gate.pending_count;data.human_gate_oldest_age_seconds=0;data.human_gate_last_verified_at=gates.map(g=>g.last_verified_at).filter(Boolean).sort().at(-1)||null;data.human_gate_last_resume_at=gates.filter(g=>g.resume_event).map(g=>g.updated_at).filter(Boolean).sort().at(-1)||null;return new Response(JSON.stringify(data,null,2)+"\n",{headers:{"content-type":"application/json; charset=utf-8","cache-control":"no-store","x-content-type-options":"nosniff","referrer-policy":"no-referrer"}})}
export default{async fetch(request,env,ctx){const url=new URL(request.url);const boundary=await enforceOwnerMutationBoundary(request);if(boundary)return boundary;const owner=await handleOwnerInbox(request,env);if(owner)return owner;const handled=await handleHumanGateRequest(request,env,ctx);if(handled)return handled;if(url.pathname==="/"&&(request.method==="GET"||request.method==="HEAD"))return dashboard(request,env,ctx);if(url.pathname==="/api/status"&&request.method==="GET")return status(request,env,ctx);return x402.fetch(request,env,ctx)},async scheduled(controller,env,ctx){return x402.scheduled(controller,env,ctx)}};

export {enforceOwnerMutationBoundary};
