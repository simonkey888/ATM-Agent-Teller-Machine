import assert from "node:assert/strict";
import { dispatchExactHeadCycle } from "../worker/src/index.js";

const sha = "a".repeat(40);
let captured;
const result = await dispatchExactHeadCycle(
  { scheduledTime: 1787242800000 },
  { ATM_GIT_SHA: sha, ATM_GITHUB_DISPATCH_TOKEN: "test-only-token" },
  async (url, init) => {
    captured = { url, init };
    return { status: 204 };
  },
);

assert.equal(result.controller, "CLOUDFLARE_CRON_V1");
assert.equal(result.expected_sha, sha);
assert.equal(result.outgoing_spend_usd, "0");
assert.match(captured.url, /atm-cloud-cycle\.yml\/dispatches$/);
const body = JSON.parse(captured.init.body);
assert.equal(body.ref, "pivot/universal-money-radar-r1");
assert.deepEqual(body.inputs, {
  expected_sha: sha,
  scheduler_source: "CLOUDFLARE_CRON_V1",
  scheduled_time: "1787242800000",
});
assert.equal(captured.init.headers.Authorization, "Bearer test-only-token");

await assert.rejects(
  () => dispatchExactHeadCycle({ scheduledTime: 1787242800000 }, { ATM_GIT_SHA: sha }, async () => ({ status: 204 })),
  /credential_missing/,
);
await assert.rejects(
  () => dispatchExactHeadCycle({ scheduledTime: 1787242800000 }, { ATM_GIT_SHA: "bad", ATM_GITHUB_DISPATCH_TOKEN: "x" }, async () => ({ status: 204 })),
  /exact_sha_invalid/,
);
await assert.rejects(
  () => dispatchExactHeadCycle({ scheduledTime: 1787242800000 }, { ATM_GIT_SHA: sha, ATM_GITHUB_DISPATCH_TOKEN: "x" }, async () => ({ status: 403 })),
  /dispatch_403/,
);

console.log("ATM_DURABLE_SCHEDULER_CONTRACT=PASS");
