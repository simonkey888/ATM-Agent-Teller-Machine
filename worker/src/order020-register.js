const HOST = "atm.simondalmasso44.workers.dev";
const INDEX_402_READ = `https://402index.io/api/v1/services?q=${encodeURIComponent(HOST)}&protocol=x402&limit=100`;
const INDEX_402_REGISTER = "https://402index.io/api/v1/register";
const AGENT402_READ = "https://agent402.tools/api/index";
const AGENT402_REGISTER = "https://agent402.tools/api/index/register";

async function readText(url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      accept: "application/json,text/plain,*/*",
      "user-agent": "ATM-ORDER020-Cloudflare-Register/1.0",
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  return { status: response.status, ok: response.ok, text: text.slice(0, 1_500_000) };
}

function containsResource(text, resource) {
  const lower = String(text || "").toLowerCase();
  return lower.includes(HOST) || lower.includes(String(resource || "").toLowerCase());
}

async function register402(resource) {
  const before = await readText(INDEX_402_READ);
  if (before.ok && containsResource(before.text, resource)) {
    return { surface: "402index", state: "RESOLVABLE", registered: false, readback: true };
  }
  const payload = {
    url: resource,
    name: "ATM Base USDC Falsifier",
    protocol: "x402",
    http_method: "POST",
    description: "Deterministic Base mainnet USDC funding, escrow, settlement and transfer claim falsifier.",
    price_usd: 0.01,
    payment_asset: "USDC",
    payment_network: "Base",
    category: "utility",
    provider: "ATM",
  };
  const posted = await readText(INDEX_402_REGISTER, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const after = await readText(INDEX_402_READ);
  return {
    surface: "402index",
    state: after.ok && containsResource(after.text, resource) ? "RESOLVABLE" : "PENDING",
    registered: [200, 201, 202, 409].includes(posted.status),
    register_status: posted.status,
    readback: after.ok && containsResource(after.text, resource),
  };
}

async function registerAgent402(origin, resource) {
  const before = await readText(AGENT402_READ);
  if (before.ok && containsResource(before.text, resource)) {
    return { surface: "agent402", state: "RESOLVABLE", registered: false, readback: true };
  }
  const posted = await readText(AGENT402_REGISTER, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ origin }),
  });
  const after = await readText(AGENT402_READ);
  return {
    surface: "agent402",
    state: after.ok && containsResource(after.text, resource) ? "RESOLVABLE" : "PENDING",
    registered: [200, 201, 202, 409].includes(posted.status),
    register_status: posted.status,
    readback: after.ok && containsResource(after.text, resource),
  };
}

export async function ensurePublicIndexes({ origin, resource }) {
  const result = {
    schema: "ATM_ORDER020_PUBLIC_INDEX_RECONCILE_V1",
    outgoing_spend_usd: "0",
    surfaces: {},
  };
  try {
    result.surfaces["402index"] = await register402(resource);
  } catch (error) {
    result.surfaces["402index"] = { surface: "402index", state: "RETRY", readback: false, error: String(error?.message || error).slice(0, 160) };
  }
  try {
    result.surfaces.agent402 = await registerAgent402(origin, resource);
  } catch (error) {
    result.surfaces.agent402 = { surface: "agent402", state: "RETRY", readback: false, error: String(error?.message || error).slice(0, 160) };
  }
  return result;
}
