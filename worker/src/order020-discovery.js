const REPO = "simonkey888/ATM-Agent-Teller-Machine";
const ORDER_ISSUE = 48;
const EVENT_MARKER = "ATM ORDER020 X402 PAYMENT EVENT";
const TRUSTED_AUTHORS = new Set(["simonkey888", "github-actions[bot]"]);

function base64Json(value) {
  return btoa(JSON.stringify(value));
}

export async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(String(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export function bazaarExtension({ resourceUrl, network, asset, payTo, amountAtomic }) {
  const body = {
    txHash: "0x" + "00".repeat(32),
    expectedRecipient: payTo,
    claimType: "transfer",
    minAmountAtomic: "1",
  };
  return {
    info: {
      input: {
        type: "http",
        method: "POST",
        bodyType: "json",
        body,
      },
      output: {
        type: "json",
        example: {
          ok: true,
          paid: true,
          payment: {
            network,
            asset,
            pay_to: payTo,
            amount_atomic: amountAtomic,
            settlement_tx: "0x" + "11".repeat(32),
            onchain_proven: true,
          },
          result: {
            schema: "ATM_X402_FALSIFIER_V1",
            capability: "DETERMINISTIC_BASE_USDC_ONCHAIN_FALSIFIER",
            verdict: "CONFIRM",
            authority: "BASE_MAINNET_RECEIPT",
          },
        },
      },
    },
    schema: {
      $schema: "https://json-schema.org/draft/2020-12/schema",
      type: "object",
      properties: {
        input: {
          type: "object",
          properties: {
            type: { type: "string", const: "http" },
            method: { type: "string", enum: ["POST", "PUT", "PATCH"] },
            bodyType: { type: "string", enum: ["json", "form-data", "text"] },
            body: { type: "object" },
            queryParams: { type: "object", additionalProperties: { type: "string" } },
            headers: { type: "object", additionalProperties: { type: "string" } },
          },
          required: ["type", "method", "bodyType", "body"],
          additionalProperties: false,
        },
        output: {
          type: "object",
          properties: {
            type: { type: "string" },
            example: {},
          },
          required: ["type"],
        },
      },
      required: ["input"],
    },
  };
}

export function openApiDocument({ origin, path, network, asset, payTo, amountAtomic }) {
  const resourceUrl = origin + path;
  const inputSchema = {
    type: "object",
    additionalProperties: false,
    required: ["txHash", "expectedRecipient"],
    properties: {
      txHash: { type: "string", pattern: "^0x[0-9a-fA-F]{64}$", description: "Base transaction hash to falsify." },
      expectedRecipient: { type: "string", pattern: "^0x[0-9a-fA-F]{40}$", description: "Expected recipient in the target transfer claim." },
      claimType: { type: "string", enum: ["funding", "escrow", "settlement", "transfer"], default: "funding" },
      minAmountAtomic: { type: "string", pattern: "^[0-9]{1,24}$", default: "1", description: "Minimum USDC atomic units claimed by the target statement." },
    },
  };
  const errorSchema = {
    type: "object",
    properties: { ok: { type: "boolean" }, error: { type: "string" } },
  };
  return {
    openapi: "3.1.0",
    info: {
      title: "ATM Deterministic Base USDC Falsifier",
      version: "1.0.0",
      description: "Pay-per-call deterministic verifier/falsifier for Base mainnet USDC funding, escrow, settlement, and transfer claims. No LLM and no caller-supplied RPC or URL.",
    },
    servers: [{ url: origin }],
    paths: {
      [path]: {
        post: {
          operationId: "falsifyBaseUsdcClaim",
          summary: "Falsify a Base USDC on-chain claim",
          description: "After x402 settlement is independently proven on Base, checks the supplied transaction receipt for a matching USDC transfer and returns CONFIRM or FALSIFY.",
          security: [{ x402Payment: [] }],
          "x-payment-info": {
            price: { mode: "fixed", currency: "USD", amount: "0.010000" },
            protocols: [{ x402: {} }],
          },
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: inputSchema,
                example: {
                  txHash: "0x" + "00".repeat(32),
                  expectedRecipient: payTo,
                  claimType: "transfer",
                  minAmountAtomic: "1",
                },
              },
            },
          },
          responses: {
            "200": {
              description: "Paid deterministic falsifier result.",
              headers: { "PAYMENT-RESPONSE": { schema: { type: "string" } } },
              content: { "application/json": { schema: { type: "object" } } },
            },
            "400": { description: "Invalid bounded falsifier input.", content: { "application/json": { schema: errorSchema } } },
            "402": {
              description: "x402 v2 payment required before input validation or execution.",
              headers: { "PAYMENT-REQUIRED": { schema: { type: "string" } } },
              content: { "application/json": { schema: { type: "object" } } },
            },
            "403": { description: "Self-payment is not accepted as seller revenue.", content: { "application/json": { schema: errorSchema } } },
            "500": { description: "Unexpected fail-closed server error.", content: { "application/json": { schema: errorSchema } } },
            "502": { description: "Settlement or Base evidence was not final/consistent.", content: { "application/json": { schema: errorSchema } } },
            "503": { description: "Required facilitator, Base RPC, or accounting transport unavailable.", content: { "application/json": { schema: errorSchema } } },
          },
        },
      },
    },
    components: {
      securitySchemes: {
        x402Payment: {
          type: "apiKey",
          in: "header",
          name: "PAYMENT-SIGNATURE",
          description: `x402 v2 exact payment: ${amountAtomic} atomic Base USDC on ${network} to ${payTo}.`,
        },
      },
    },
  };
}

export function wellKnownDocument(resourceUrl) {
  return { version: 1, resources: [resourceUrl] };
}

function trustedComment(row) {
  if (!row || typeof row !== "object") return false;
  const login = String(row.user?.login || "");
  const association = String(row.author_association || "");
  return login === "github-actions[bot]" ||
    (login === "simonkey888" && ["OWNER", "MEMBER", "COLLABORATOR"].includes(association));
}

function parseEvent(row) {
  if (!trustedComment(row)) return null;
  const text = String(row.body || "");
  if (!text.startsWith(EVENT_MARKER)) return null;
  const match = text.match(/```json\s*([\s\S]*?)\s*```/);
  if (!match) return null;
  try {
    const value = JSON.parse(match[1]);
    if (value?.schema !== "ATM_ORDER020_X402_PAYMENT_EVENT_V1") return null;
    return { ...value, comment_id: Number(row.id || 0), comment_created_at: row.created_at || null };
  } catch {
    return null;
  }
}

async function latestIssueComments(fetchImpl = fetch) {
  const issueResponse = await fetchImpl(`https://api.github.com/repos/${REPO}/issues/${ORDER_ISSUE}`, {
    headers: { Accept: "application/vnd.github+json", "User-Agent": "ATM-X402-Seller-Funnel/1.0" },
  });
  if (!issueResponse.ok) throw new Error(`github_issue_${issueResponse.status}`);
  const issue = await issueResponse.json();
  const page = Math.max(1, Math.ceil(Number(issue.comments || 0) / 100));
  const commentsResponse = await fetchImpl(
    `https://api.github.com/repos/${REPO}/issues/${ORDER_ISSUE}/comments?per_page=100&page=${page}`,
    { headers: { Accept: "application/vnd.github+json", "User-Agent": "ATM-X402-Seller-Funnel/1.0" } },
  );
  if (!commentsResponse.ok) throw new Error(`github_comments_${commentsResponse.status}`);
  return commentsResponse.json();
}

export async function publishSellerPaymentEvent(env, event, fetchImpl = fetch) {
  const token = String(env?.ATM_GITHUB_DISPATCH_TOKEN || "");
  if (!token) return { published: false, reason: "ACCOUNTING_TRANSPORT_CREDENTIAL_MISSING" };
  const body = EVENT_MARKER + "\n```json\n" + JSON.stringify(event, null, 2) + "\n```";
  const response = await fetchImpl(`https://api.github.com/repos/${REPO}/issues/${ORDER_ISSUE}/comments`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "ATM-ORDER020-X402-Event/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ body }),
  });
  let payload = {};
  try { payload = await response.json(); } catch {}
  if (response.status !== 201) {
    return { published: false, reason: `ACCOUNTING_TRANSPORT_${response.status}` };
  }
  return { published: true, comment_id: Number(payload.id || 0), url: payload.html_url || null };
}

export async function sellerFunnel({ legacy, request, env, ctx, canonicalPayTo, fetchImpl = fetch }) {
  let events = [];
  try {
    const comments = await latestIssueComments(fetchImpl);
    events = (Array.isArray(comments) ? comments : []).map(parseEvent).filter(Boolean);
  } catch {}
  const external = events.filter((row) =>
    String(row.recipient || "").toLowerCase() === canonicalPayTo.toLowerCase() &&
    String(row.payer || "").toLowerCase() !== canonicalPayTo.toLowerCase()
  );
  const distinctBuyers = new Set(external.map((row) => String(row.payer || "").toLowerCase()).filter(Boolean));
  let withdrawable = "UNKNOWN";
  try {
    const url = new URL(request.url);
    url.pathname = "/api/status";
    url.search = "";
    const response = await legacy.fetch(new Request(url.toString(), { headers: { Accept: "application/json" } }), env, ctx);
    const status = await response.json();
    withdrawable = String(status.realized_withdrawable_usd ?? "UNKNOWN");
  } catch {}
  const last = external.length ? external[external.length - 1] : null;
  const positive = Number(withdrawable) > 0;
  return {
    schema: "ATM_ORDER020_SELLER_FUNNEL_V1",
    route: "/x402/falsify",
    route_live: true,
    discovery: { openapi: "/openapi.json", well_known: "/.well-known/x402", bazaar_extension: true },
    payment_challenges_observed_min: external.length,
    verify_success_min: external.length,
    settle_success_min: external.length,
    onchain_proven_min: external.length,
    external_buyer_events: external.length,
    distinct_external_buyers: distinctBuyers.size,
    withdrawable_usdc: withdrawable,
    last_external_payment: last ? {
      settlement_tx: last.settlement_tx,
      payer: last.payer,
      amount_atomic: last.amount_atomic,
      observed_at: last.observed_at || last.comment_created_at,
      payment_event_id: last.payment_event_id,
    } : null,
    seller_loop_state: positive ? "POST_PAYMENT_SELLER_LOOP" : "WAITING_GENUINE_EXTERNAL_BUYER",
    outgoing_spend_usd: "0",
  };
}

export { EVENT_MARKER, base64Json };
