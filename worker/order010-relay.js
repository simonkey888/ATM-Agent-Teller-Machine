const JOB_ID = "f82a9ca9-4b7f-4bdf-91c1-b5ae0516b4eb";
const BASE = "https://workprotocol.ai";

function json(data, status = 200) {
  return new Response(JSON.stringify(data) + "\n", {
    status,
    headers: {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
  });
}

async function getJob() {
  const response = await fetch(`${BASE}/api/jobs/${JOB_ID}`, {
    headers: {"Accept": "application/json", "User-Agent": "ATM-Order010-Relay/1.0"},
  });
  if (!response.ok) return {ok: false, http: response.status};
  const payload = await response.json();
  const job = payload && typeof payload.job === "object" ? payload.job : {};
  const claims = Array.isArray(payload.claims) ? payload.claims : [];
  const payments = Array.isArray(payload.payments) ? payload.payments : [];
  return {
    ok: true,
    http: response.status,
    job: {
      id: String(job.id || ""),
      status: String(job.status || ""),
      paymentAmount: String(job.paymentAmount || ""),
      paymentCurrency: String(job.paymentCurrency || ""),
      paymentRail: String(job.paymentRail || ""),
      escrowFunded: job.escrowFunded === true,
      escrowTxHash: String(job.escrowTxHash || ""),
      maxWorkers: Number(job.maxWorkers || 0),
      competitionMode: String(job.competitionMode || ""),
    },
    claims: claims.map((row) => ({id: String(row.id || ""), status: String(row.status || ""), agentId: String(row.agentId || "")})),
    payments: payments.map((row) => ({
      id: String(row.id || ""),
      escrowStatus: String(row.escrowStatus || ""),
      onchainTxHash: String(row.onchainTxHash || ""),
      settlementTxHash: row.settlementTxHash ? String(row.settlementTxHash) : null,
    })),
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET") return json({ok: false, error: "method_not_allowed"}, 405);
    if (url.pathname === "/health") return json({ok: true, service: "atm-order010-relay", git_sha: env.ATM_GIT_SHA || "UNKNOWN", mode: "read_only_probe"});
    if (url.pathname === "/probe") {
      const result = await getJob();
      return json({schema: "ATM_ORDER010_WORKPROTOCOL_RELAY_PROBE_V1", git_sha: env.ATM_GIT_SHA || "UNKNOWN", outgoing_spend_usd: "0", ...result}, result.ok ? 200 : 502);
    }
    return json({ok: false, error: "not_found"}, 404);
  },
};
