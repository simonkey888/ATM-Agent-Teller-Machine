import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const source = await readFile(new URL("../worker/src/index.js", import.meta.url), "utf8");
const worker = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const sha = "a".repeat(40);
const now = Date.parse("2026-08-20T12:00:00Z");
const base = {
  schema: "ATM_CLOUD_STATUS_V1",
  state: "RUNNING",
  phase: "DISCOVER",
  paused: false,
  cloud_heartbeat_at: "2026-08-20T11:59:00Z",
  last_result_status: "NO_ELIGIBLE_OPPORTUNITY",
  zero_spend: true,
  radar: {
    schema: "ATM_UNIVERSAL_RADAR_PUBLIC_V3",
    source_sha: sha,
    generated_at: "2026-08-20T11:59:10Z",
    source_count: 3,
    healthy_source_count: 2,
    current_candidates: 1,
    opportunities: [
      {id:"market:1",title:"real",source:"market",url:"https://example.test/1",executor:"HTTP_RESEARCH",operational_state:"EXECUTABLE",payout_net:"10",currency:"USDC"},
      {id:"fixture:2",title:"fake",source:"fixture",url:"https://example.test/2",executor:"TEST_FIX",operational_state:"REJECTED_SLOP",payout_net:"99",currency:"USDC"},
    ],
  },
  money_board: {submitted_net_usdc:"5.55",state:"SUBMITTED",outgoing_spend_usd:"0",in_flight:true},
};

assert.equal(worker.deriveOperationalStatus(base,{ATM_GIT_SHA:sha},now).code,"SEARCH");
assert.equal(worker.deriveOperationalStatus({...base,phase:"WORK"},{ATM_GIT_SHA:sha},now).code,"ACTIVE");
assert.equal(worker.deriveOperationalStatus({...base,phase:"SUBMIT"},{ATM_GIT_SHA:sha},now).code,"ACTIVE");
assert.equal(worker.deriveOperationalStatus({...base,cloud_heartbeat_at:"2026-08-20T11:40:00Z"},{ATM_GIT_SHA:sha},now).code,"FAULT");
assert.equal(worker.deriveOperationalStatus(base,{ATM_GIT_SHA:"b".repeat(40)},now).code,"FAULT");
assert.equal(worker.deriveOperationalStatus({...base,paused:true},{ATM_GIT_SHA:sha},now).code,"FAULT");
assert.equal(worker.deriveOperationalStatus({...base,last_result_status:"CRITICAL_WATCHER_FAILED"},{ATM_GIT_SHA:sha},now).code,"FAULT");
assert.equal(worker.deriveOperationalStatus({...base,state:"CRASHED"},{ATM_GIT_SHA:sha},now).code,"FAULT");
assert.equal(worker.deriveOperationalStatus({}, {ATM_GIT_SHA:sha},now).code,"FAULT");

const html = worker.renderDashboard(base,{ATM_GIT_SHA:sha});
assert.equal((html.match(/class="status-pill"/g)||[]).length,1);
assert.equal((html.match(/class="card"/g)||[]).length,12);
assert.match(html,/CURRENT WORK/);
assert.match(html,/EXECUTABLE OPPORTUNITIES/);
assert.match(html,/PAYOUT DESTINATIONS \/ EARNINGS/);
assert.match(html,/MetaMask \/ Base USDC/);
assert.match(html,/Santander/);
assert.match(html,/NOT_CONNECTED/);
assert.match(html,/market:1|real/);
assert.doesNotMatch(html,/fixture:2|fake/);
assert.match(html,/overflow-wrap:anywhere/);
assert.match(html,/min-width:0/);
assert.doesNotMatch(html,/Radar source SHA|Deployed Worker SHA|Economic runtime SHA/);
console.log("ATM_ORDER013_DASHBOARD_CONTRACT=PASS");
