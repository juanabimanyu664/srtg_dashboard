/**
 * SRTG NAV Dashboard - Cloudflare Worker "write bridge"
 * -------------------------------------------------------
 * Holds the GitHub write credential as a Worker secret (never exposed
 * to the browser) and commits edits made from the dashboard's
 * Assumptions panel to assumptions.json in the repo via the GitHub
 * Contents API.
 *
 * Required Worker secrets/vars (set in Cloudflare dashboard -> Workers
 * & Pages -> your worker -> Settings -> Variables and Secrets):
 *   GITHUB_TOKEN     (secret)  fine-grained PAT, Contents: read/write, this repo only
 *   GITHUB_OWNER     (var)     e.g. "juanabimanyu664"
 *   GITHUB_REPO      (var)     e.g. "srtg_dashboard"
 *   GITHUB_BRANCH    (var)     e.g. "main"
 *   EDIT_PASSWORD    (secret)  the simple password checked against dashboard edits
 *   ALLOWED_ORIGIN   (var)     e.g. "https://juanabimanyu664.github.io"
 *
 * Endpoints (all POST, JSON body, CORS restricted to ALLOWED_ORIGIN):
 *   /save-assumption   { password, action, payload }
 *   /add-holding       { password, ticker, company, shares_outstanding_bn, stake_pct }
 *
 * action for /save-assumption is one of:
 *   "update_holding"        payload: { ticker, field, value }   field in stake_pct | shares_outstanding_bn | company
 *   "update_balance_sheet"  payload: { field, value }           field in debt_idr_bn | cash_idr_bn | non_listed_investment_idr_bn | srtg_shares_outstanding_bn
 */

const ASSUMPTIONS_PATH = "assumptions.json";

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

function jsonResponse(obj, status, env) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(env) },
  });
}

async function githubGetFile(env) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${ASSUMPTIONS_PATH}?ref=${env.GITHUB_BRANCH}`;
  const res = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "srtg-dashboard-worker",
      Accept: "application/vnd.github+json",
    },
  });
  if (!res.ok) throw new Error(`GitHub GET failed: ${res.status} ${await res.text()}`);
  const data = await res.json();
  const content = JSON.parse(atob(data.content));
  return { content, sha: data.sha };
}

async function githubPutFile(env, content, sha, message) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/${ASSUMPTIONS_PATH}`;
  const body = {
    message,
    content: btoa(unescape(encodeURIComponent(JSON.stringify(content, null, 2)))),
    sha,
    branch: env.GITHUB_BRANCH,
  };
  const res = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "srtg-dashboard-worker",
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`GitHub PUT failed: ${res.status} ${await res.text()}`);
  return res.json();
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

const HOLDING_FIELDS = new Set(["stake_pct", "shares_outstanding_bn", "company"]);
const BS_FIELDS = new Set([
  "debt_idr_bn",
  "cash_idr_bn",
  "non_listed_investment_idr_bn",
  "srtg_shares_outstanding_bn",
]);

function applyUpdateHolding(assumptions, payload) {
  const { ticker, field, value } = payload;
  if (!HOLDING_FIELDS.has(field)) throw new Error(`Invalid field: ${field}`);
  const h = assumptions.holdings.find((x) => x.ticker === ticker);
  if (!h) throw new Error(`Unknown ticker: ${ticker}`);
  h[field] = field === "company" ? String(value) : Number(value);
  h.as_of = today();
}

function applyUpdateBalanceSheet(assumptions, payload) {
  const { field, value } = payload;
  if (!BS_FIELDS.has(field)) throw new Error(`Invalid field: ${field}`);
  assumptions.balance_sheet[field] = Number(value);
  const asOfField = field.replace(/_idr_bn$|_bn$/, "_as_of");
  assumptions.balance_sheet[asOfField] = today();
}

function applyAddHolding(assumptions, payload) {
  const { ticker, company, shares_outstanding_bn, stake_pct } = payload;
  if (!ticker || !company) throw new Error("ticker and company are required");
  if (assumptions.holdings.some((h) => h.ticker === ticker.toUpperCase())) {
    throw new Error(`Ticker ${ticker} already exists`);
  }
  assumptions.holdings.push({
    ticker: String(ticker).toUpperCase(),
    company: String(company),
    shares_outstanding_bn: Number(shares_outstanding_bn),
    stake_pct: Number(stake_pct),
    as_of: today(),
  });
}

async function handleSaveAssumption(request, env) {
  const body = await request.json();
  if (body.password !== env.EDIT_PASSWORD) {
    return jsonResponse({ error: "Invalid password" }, 401, env);
  }
  const { content: assumptions, sha } = await githubGetFile(env);

  let message;
  if (body.action === "update_holding") {
    applyUpdateHolding(assumptions, body.payload);
    message = `Update ${body.payload.ticker} ${body.payload.field} via dashboard`;
  } else if (body.action === "update_balance_sheet") {
    applyUpdateBalanceSheet(assumptions, body.payload);
    message = `Update balance sheet ${body.payload.field} via dashboard`;
  } else {
    return jsonResponse({ error: `Unknown action: ${body.action}` }, 400, env);
  }

  await githubPutFile(env, assumptions, sha, message);
  return jsonResponse({ ok: true, assumptions }, 200, env);
}

async function handleAddHolding(request, env) {
  const body = await request.json();
  if (body.password !== env.EDIT_PASSWORD) {
    return jsonResponse({ error: "Invalid password" }, 401, env);
  }
  const { content: assumptions, sha } = await githubGetFile(env);
  applyAddHolding(assumptions, body);
  await githubPutFile(env, assumptions, sha, `Add holding ${body.ticker} via dashboard`);
  return jsonResponse({ ok: true, assumptions }, 200, env);
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(env) });
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405, env);
    }

    const url = new URL(request.url);
    try {
      if (url.pathname === "/save-assumption") {
        return await handleSaveAssumption(request, env);
      }
      if (url.pathname === "/add-holding") {
        return await handleAddHolding(request, env);
      }
      return jsonResponse({ error: "Not found" }, 404, env);
    } catch (err) {
      return jsonResponse({ error: String(err.message || err) }, 500, env);
    }
  },
};
