import legacy from "./index.js";
import {
  bazaarExtension,
  openApiDocument,
  publishSellerPaymentEvent,
  sellerFunnel,
  sha256Hex,
  wellKnownDocument,
} from "./order020-discovery.js";

export const X402_PATH = "/x402/falsify";
export const X402_VERSION = 2;
export const X402_NETWORK = "eip155:8453";
export const X402_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
export const X402_PAY_TO = "0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4";
export const X402_AMOUNT_ATOMIC = "10000";
export const X402_FACILITATOR = "https://facilitator.xpay.sh";
export const BASE_RPC = "https://mainnet.base.org";
export const X402_SELLER_FUNNEL_PATH = "/api/seller-funnel";
const TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const RESOURCE_ORIGIN = "https://atm.simondalmasso44.workers.dev";
const RESOURCE_URL = RESOURCE_ORIGIN + X402_PATH;
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const TX_RE = /^0x[0-9a-fA-F]{64}$/;
const SIG_RE = /^0x[0-9a-fA-F]{130}$/;
const NONCE_RE = /^0x[0-9a-fA-F]{64}$/;

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data, null, 2) + "\n", {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
      ...extraHeaders,
    },
  });
}
function b64Json(value) {
  return btoa(JSON.stringify(value));
}
function decodeB64Json(raw) {
  const text = String(raw || "").trim().replace(/-/g, "+").replace(/_/g, "/");
  if (!text || text.length > 16384) throw new Error("payment_header_invalid");
  const padded = text + "=".repeat((4 - (text.length % 4)) % 4);
  const parsed = JSON.parse(atob(padded));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("payment_payload_invalid");
  return parsed;
}
function sameAddress(a, b) {
  return String(a || "").toLowerCase() === String(b || "").toLowerCase();
}
function requirements() {
  return {
    scheme: "exact",
    network: X402_NETWORK,
    amount: X402_AMOUNT_ATOMIC,
    asset: X402_USDC,
    payTo: X402_PAY_TO,
    maxTimeoutSeconds: 60,
    extra: { name: "USDC", version: "2" },
  };
}
function bazaar() {
  return bazaarExtension({
    resourceUrl: RESOURCE_URL,
    network: X402_NETWORK,
    asset: X402_USDC,
    payTo: X402_PAY_TO,
    amountAtomic: X402_AMOUNT_ATOMIC,
  });
}
function paymentRequired(error = "PAYMENT-SIGNATURE header is required") {
  return {
    x402Version: X402_VERSION,
    error,
    resource: {
      url: RESOURCE_URL,
      description: "Deterministic Base USDC funding, escrow, and on-chain transfer falsifier",
      mimeType: "application/json",
      serviceName: "ATM Falsifier",
      tags: ["falsifier", "base", "usdc"],
    },
    accepts: [requirements()],
    extensions: { bazaar: bazaar() },
  };
}
function payment402(error) {
  const challenge = paymentRequired(error);
  return json(challenge, 402, { "PAYMENT-REQUIRED": b64Json(challenge) });
}
async function fetchJson(url, init = {}) {
  const response = await fetch(url, {
    ...init,
    headers: {
      "accept": "application/json",
      "user-agent": "ATM-X402-Seller/2.0",
      ...(init.headers || {}),
    },
  });
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) throw new Error(`upstream_http_${response.status}`);
  return body;
}
function supportedByFacilitator(body) {
  if (Array.isArray(body?.kinds)) {
    return body.kinds.some(
      (row) => Number(row?.x402Version) === 2 &&
        row?.scheme === "exact" &&
        row?.network === X402_NETWORK
    );
  }
  const rows = Array.isArray(body?.supportedNetworks) ? body.supportedNetworks : [];
  return rows.some((row) => {
    if (typeof row === "string") return row === X402_NETWORK;
    const network = row?.networkId || row?.network || "";
    const version = String(row?.version ?? row?.x402Version ?? "").toLowerCase();
    return network === X402_NETWORK && (version === "v2" || version === "2");
  });
}
async function requireFacilitator() {
  const body = await fetchJson(`${X402_FACILITATOR}/supported`);
  if (!supportedByFacilitator(body)) throw new Error("facilitator_base_v2_unavailable");
}
function validatePaymentPayload(p) {
  if (Number(p?.x402Version) !== 2) throw new Error("x402_version_mismatch");
  const accepted = p?.accepted;
  const req = requirements();
  if (!accepted || typeof accepted !== "object") throw new Error("accepted_missing");
  for (const key of ["scheme", "network", "amount"]) {
    if (String(accepted[key]) !== String(req[key])) throw new Error(`accepted_${key}_mismatch`);
  }
  if (!sameAddress(accepted.asset, req.asset)) throw new Error("accepted_asset_mismatch");
  if (!sameAddress(accepted.payTo, req.payTo)) throw new Error("accepted_payto_mismatch");
  if (Number(accepted.maxTimeoutSeconds) !== req.maxTimeoutSeconds) throw new Error("accepted_timeout_mismatch");
  if (p?.resource?.url && p.resource.url !== RESOURCE_URL) throw new Error("resource_url_mismatch");
  const payload = p?.payload;
  const auth = payload?.authorization;
  if (!SIG_RE.test(String(payload?.signature || ""))) throw new Error("signature_invalid");
  if (!auth || typeof auth !== "object") throw new Error("authorization_missing");
  if (!ADDRESS_RE.test(String(auth.from || ""))) throw new Error("payer_invalid");
  if (!sameAddress(auth.to, X402_PAY_TO)) throw new Error("authorization_recipient_mismatch");
  if (String(auth.value) !== X402_AMOUNT_ATOMIC) throw new Error("authorization_amount_mismatch");
  if (!/^\d+$/.test(String(auth.validAfter || "")) || !/^\d+$/.test(String(auth.validBefore || ""))) {
    throw new Error("authorization_time_invalid");
  }
  if (!NONCE_RE.test(String(auth.nonce || ""))) throw new Error("authorization_nonce_invalid");
  return auth.from;
}
async function facilitatorCall(path, paymentPayload) {
  const body = {
    x402Version: X402_VERSION,
    paymentPayload,
    paymentRequirements: requirements(),
  };
  return fetchJson(`${X402_FACILITATOR}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function rpc(method, params) {
  const body = await fetchJson(BASE_RPC, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  if (body.error) throw new Error(`rpc_${body.error.code || "error"}`);
  return body.result;
}
function transferTo(log, recipient, minAmount) {
  if (!log || sameAddress(log.address, X402_USDC) === false) return null;
  if (!Array.isArray(log.topics) || String(log.topics[0] || "").toLowerCase() !== TRANSFER_TOPIC) return null;
  const toTopic = String(log.topics[2] || "").toLowerCase();
  if (!toTopic.endsWith(String(recipient).slice(2).toLowerCase())) return null;
  let amount;
  try { amount = BigInt(String(log.data || "0x0")); } catch { return null; }
  return amount >= minAmount ? amount : null;
}
async function receipt(txHash) {
  if (!TX_RE.test(String(txHash || ""))) throw new Error("tx_hash_invalid");
  return rpc("eth_getTransactionReceipt", [txHash]);
}
async function proveUsdcTransfer(txHash, recipient, minAmountAtomic) {
  const r = await receipt(txHash);
  if (!r) return { proven: false, reason: "TX_NOT_FOUND", tx_hash: txHash };
  if (String(r.status || "").toLowerCase() !== "0x1") {
    return { proven: false, reason: "TX_REVERTED", tx_hash: txHash, block_number: r.blockNumber || null };
  }
  const minimum = BigInt(String(minAmountAtomic));
  for (const log of Array.isArray(r.logs) ? r.logs : []) {
    const amount = transferTo(log, recipient, minimum);
    if (amount !== null) {
      return {
        proven: true,
        reason: "BASE_USDC_TRANSFER_CONFIRMED",
        tx_hash: txHash,
        block_number: r.blockNumber || null,
        recipient,
        amount_atomic: amount.toString(),
      };
    }
  }
  return {
    proven: false,
    reason: "EXPECTED_USDC_TRANSFER_ABSENT",
    tx_hash: txHash,
    block_number: r.blockNumber || null,
    recipient,
  };
}
async function parseCapabilityInput(request) {
  const length = Number(request.headers.get("content-length") || "0");
  if (Number.isFinite(length) && length > 4096) throw new Error("body_too_large");
  const text = await request.text();
  if (text.length > 4096) throw new Error("body_too_large");
  let body;
  try { body = JSON.parse(text || "{}"); } catch { throw new Error("invalid_json"); }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("invalid_body");
  const txHash = String(body.txHash || body.tx_hash || "");
  const expectedRecipient = String(body.expectedRecipient || body.expected_recipient || "");
  const claimType = String(body.claimType || body.claim_type || "funding").toLowerCase();
  const minAmountAtomic = String(body.minAmountAtomic || body.min_amount_atomic || "1");
  if (!TX_RE.test(txHash)) throw new Error("target_tx_hash_invalid");
  if (!ADDRESS_RE.test(expectedRecipient)) throw new Error("target_recipient_invalid");
  if (!["funding", "escrow", "settlement", "transfer"].includes(claimType)) throw new Error("claim_type_invalid");
  if (!/^\d{1,24}$/.test(minAmountAtomic) || BigInt(minAmountAtomic) < 1n) throw new Error("min_amount_invalid");
  return { txHash, expectedRecipient, claimType, minAmountAtomic };
}
async function runFalsifier(input) {
  const evidence = await proveUsdcTransfer(input.txHash, input.expectedRecipient, input.minAmountAtomic);
  return {
    schema: "ATM_X402_FALSIFIER_V1",
    capability: "DETERMINISTIC_BASE_USDC_ONCHAIN_FALSIFIER",
    claim_type: input.claimType,
    verdict: evidence.proven ? "CONFIRM" : "FALSIFY",
    evidence,
    authority: "BASE_MAINNET_RECEIPT",
    outgoing_spend_usd: "0",
  };
}
async function publishPaymentEventAfterResponse(env, ctx, event) {
  const publish = async () => {
    let result = await publishSellerPaymentEvent(env, event);
    if (!result.published) result = await publishSellerPaymentEvent(env, event);
    return result;
  };
  if (ctx && typeof ctx.waitUntil === "function") {
    ctx.waitUntil(publish());
    return { scheduled: true };
  }
  const result = await publish();
  return { scheduled: false, ...result };
}
async function x402Falsify(request, env, ctx) {
  if (request.method !== "POST") return json({ ok: false, error: "method_not_allowed" }, 405, { allow: "POST" });

  // Discovery and unpaid probes must always reach a parseable 402 before body
  // validation or any external facilitator dependency.
  const paymentHeader = request.headers.get("PAYMENT-SIGNATURE");
  if (!paymentHeader) return payment402();

  try {
    await requireFacilitator();
  } catch (error) {
    return json({ ok: false, error: "SELLER_LANE_KILLED_FACILITATOR_UNAVAILABLE", detail: String(error?.message || error) }, 503);
  }

  let paymentPayload;
  let payer;
  try {
    paymentPayload = decodeB64Json(paymentHeader);
    payer = validatePaymentPayload(paymentPayload);
  } catch (error) {
    return payment402(String(error?.message || "payment_payload_invalid"));
  }
  if (sameAddress(payer, X402_PAY_TO)) {
    return json({ ok: false, error: "owner_self_payment_rejected", outgoing_spend_usd: "0" }, 403);
  }

  let input;
  try {
    input = await parseCapabilityInput(request);
  } catch (error) {
    return json({ ok: false, error: String(error?.message || "invalid_input") }, 400);
  }

  let verification;
  try {
    verification = await facilitatorCall("/verify", paymentPayload);
  } catch (error) {
    return payment402(`verify_unavailable:${String(error?.message || error)}`);
  }
  if (verification?.isValid !== true && verification?.valid !== true) {
    return payment402(String(verification?.invalidReason || verification?.reason || "payment_invalid"));
  }

  // Finality first. No capability work occurs until the facilitator settles and
  // Base independently proves the USDC transfer to the canonical wallet.
  let settlement;
  try {
    settlement = await facilitatorCall("/settle", paymentPayload);
  } catch (error) {
    return json({ ok: false, error: "settlement_unavailable", detail: String(error?.message || error) }, 503);
  }
  const settled = settlement?.success === true || settlement?.settled === true;
  const settlementTx = String(settlement?.transaction || settlement?.txHash || "");
  if (!settled || !TX_RE.test(settlementTx)) {
    return json({ ok: false, error: "settlement_not_final", reason: settlement?.errorReason || settlement?.reason || "UNKNOWN" }, 502);
  }
  if (settlement?.network && settlement.network !== X402_NETWORK && settlement.network !== "base") {
    return json({ ok: false, error: "settlement_network_mismatch" }, 502);
  }

  let paymentEvidence;
  try {
    paymentEvidence = await proveUsdcTransfer(settlementTx, X402_PAY_TO, X402_AMOUNT_ATOMIC);
  } catch (error) {
    return json({ ok: false, error: "payment_onchain_proof_unavailable", detail: String(error?.message || error) }, 503);
  }
  if (!paymentEvidence.proven) {
    return json({ ok: false, error: "payment_not_proven_to_canonical_wallet", evidence: paymentEvidence }, 502);
  }

  const result = await runFalsifier(input);
  const inputSha256 = await sha256Hex(JSON.stringify({
    txHash: input.txHash,
    expectedRecipient: input.expectedRecipient,
    claimType: input.claimType,
    minAmountAtomic: input.minAmountAtomic,
  }));
  const observedAt = new Date().toISOString();
  const paymentEventId = await sha256Hex(`${settlementTx.toLowerCase()}:${String(payer).toLowerCase()}:${paymentEvidence.amount_atomic}`);
  const event = {
    schema: "ATM_ORDER020_X402_PAYMENT_EVENT_V1",
    payment_event_id: paymentEventId,
    resource: RESOURCE_URL,
    settlement_tx: settlementTx,
    payer: String(payer).toLowerCase(),
    recipient: X402_PAY_TO.toLowerCase(),
    network: X402_NETWORK,
    chain_id: 8453,
    token: X402_USDC.toLowerCase(),
    amount_atomic: X402_AMOUNT_ATOMIC,
    input_sha256: inputSha256,
    source_sha: String(env?.ATM_GIT_SHA || "").toLowerCase(),
    observed_at: observedAt,
    onchain_proven: true,
    external_buyer: true,
    outgoing_spend_usd: "0",
  };
  const accountingEvent = await publishPaymentEventAfterResponse(env, ctx, event);
  const normalizedSettlement = {
    success: true,
    payer: String(settlement?.payer || verification?.payer || payer),
    transaction: settlementTx,
    network: X402_NETWORK,
    amount: X402_AMOUNT_ATOMIC,
  };
  return json(
    {
      ok: true,
      paid: true,
      payment: {
        network: X402_NETWORK,
        asset: X402_USDC,
        pay_to: X402_PAY_TO,
        amount_atomic: X402_AMOUNT_ATOMIC,
        settlement_tx: settlementTx,
        onchain_proven: true,
        external_buyer: true,
      },
      accounting_event: accountingEvent,
      result,
    },
    200,
    { "PAYMENT-RESPONSE": b64Json(normalizedSettlement) },
  );
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/openapi.json" && request.method === "GET") {
      return json(openApiDocument({
        origin: RESOURCE_ORIGIN,
        path: X402_PATH,
        network: X402_NETWORK,
        asset: X402_USDC,
        payTo: X402_PAY_TO,
        amountAtomic: X402_AMOUNT_ATOMIC,
      }));
    }
    if (url.pathname === "/.well-known/x402" && request.method === "GET") {
      return json(wellKnownDocument(RESOURCE_URL));
    }
    if (url.pathname === X402_SELLER_FUNNEL_PATH && request.method === "GET") {
      return json(await sellerFunnel({ legacy, request, env, ctx, canonicalPayTo: X402_PAY_TO }));
    }
    if (url.pathname === X402_PATH) return x402Falsify(request, env, ctx);
    return legacy.fetch(request, env, ctx);
  },
  async scheduled(controller, env, ctx) {
    if (typeof legacy.scheduled === "function") return legacy.scheduled(controller, env, ctx);
  },
};
