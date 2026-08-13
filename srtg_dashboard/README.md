# SRTG NAV Dashboard

Tracks PT Saratoga Investama Sedaya Tbk (IDX: SRTG) share price against the
NAV/share of its portfolio (TBIG, MDKA, ADRO, AADI, MPMX, AGII, NRCA + a
non-listed investment estimate), fully standalone once deployed: a daily
GitHub Actions job pulls prices with `yfinance`, recomputes NAV, and rebuilds
`index.html`, which GitHub Pages serves. No AI assistant is involved in the
routine refresh — this repo is the whole system.

## Repo layout

```
assumptions.json        Source of truth: holdings (ticker, shares outstanding,
                         stake %) + SRTG balance sheet (debt, cash, non-listed
                         investment, shares outstanding). Editable from the
                         dashboard (via the Cloudflare Worker) or by hand.
history.csv              Daily price history per ticker + NAV/discount history.
latest.json               Most recent computed snapshot.
index.html                 Built dashboard, served by GitHub Pages. Regenerated
                         on every Actions run — don't hand-edit it.
scripts/fetch_and_calculate.py  Fetches prices (yfinance), computes NAV, appends
                         history.csv, writes latest.json, builds index.html.
dashboard/template.html   Dashboard HTML/CSS/JS template with placeholders
                         (__LATEST_JSON__, __HISTORY_JSON__, __ASSUMPTIONS_JSON__)
                         that fetch_and_calculate.py fills in.
.github/workflows/update.yml  Daily cron (+ manual "Run workflow" button) that
                         runs the script above and commits/pushes the result.
worker/worker.js           Cloudflare Worker: the only thing holding the GitHub
                         write token. Receives dashboard edits + a password,
                         commits them to assumptions.json via the GitHub API.
worker/wrangler.toml       Worker config (non-secret vars only).
```

## One-time setup (~20-30 minutes)

### 1. Push this repo to GitHub

You already created `juanabimanyu664/srtg_dashboard` (public). From this
folder:

```bash
git init
git add .
git commit -m "Initial commit: SRTG NAV dashboard"
git branch -M main
git remote add origin https://github.com/juanabimanyu664/srtg_dashboard.git
git push -u origin main
```

### 2. Enable GitHub Pages

Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch:
`main`, folder `/ (root)` → Save. Your dashboard will be live at:

```
https://juanabimanyu664.github.io/srtg_dashboard/
```

(takes a minute or two after the first push)

### 3. Create a GitHub fine-grained Personal Access Token (for the Worker)

This lets the Cloudflare Worker commit to `assumptions.json` on your behalf.

1. GitHub → your avatar → **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**.
2. **Repository access**: Only select repositories → `srtg_dashboard`.
3. **Permissions**: Repository permissions → **Contents: Read and write**.
   Leave everything else as "No access."
4. Set an expiration (e.g. 1 year — you'll need to rotate it before then).
5. Generate, and copy the token (`github_pat_...`) somewhere safe — you
   won't be able to see it again.

### 4. Deploy the Cloudflare Worker

1. Sign up / log in at https://dash.cloudflare.com (free tier is enough).
2. Install Wrangler locally (needs Node.js): `npm install -g wrangler`
3. From the `worker/` folder: `wrangler login` (opens a browser to
   authorize), then `wrangler deploy`.
4. This creates a Worker at a URL like
   `https://srtg-dashboard-worker.<your-subdomain>.workers.dev`. Copy it.
5. Set the two secrets (you'll be prompted to paste each value):
   ```bash
   wrangler secret put GITHUB_TOKEN
   wrangler secret put EDIT_PASSWORD
   ```
   `GITHUB_TOKEN` is the fine-grained PAT from step 3. `EDIT_PASSWORD` is
   whatever simple password you want to require for saving edits from the
   dashboard.
6. Double check `worker/wrangler.toml` has the right `GITHUB_OWNER`,
   `GITHUB_REPO`, `GITHUB_BRANCH`, and `ALLOWED_ORIGIN` (should already be
   correct for this repo) — redeploy with `wrangler deploy` if you change
   anything.

Alternative to the CLI: you can do all of this from the Cloudflare dashboard
UI (Workers & Pages → Create → paste in `worker.js` → Settings → Variables
and Secrets) if you'd rather not install Wrangler.

### 5. Point the dashboard at your Worker

Open `dashboard/template.html`, find this line near the bottom of the
`<script>` block:

```js
const WORKER_URL = "REPLACE_WITH_YOUR_WORKER_URL";
```

Replace it with your actual Worker URL from step 4, e.g.:

```js
const WORKER_URL = "https://srtg-dashboard-worker.yoursubdomain.workers.dev";
```

Commit and push this change. (The next Actions run will regenerate
`index.html` from this template, so the live dashboard picks it up
automatically — or trigger the workflow manually to update it right away.)

### 6. Fill in the fine-grained financial data (already done for you)

`assumptions.json` in this repo is already seeded with the figures you
confirmed: shares outstanding for every holding, SRTG's own shares
outstanding (13.55bn), the non-listed investment estimate (IDR
15,469.238bn), debt (IDR 795.96bn), and cash (IDR 870.895bn), all as of
2026-08-13. Revisit these whenever SRTG publishes a new annual
report/financial statement — either by editing `assumptions.json` directly
and pushing, or from the dashboard's Assumptions panel once the Worker is
live.

### 7. First run

Repo → **Actions** tab → **Update SRTG NAV Dashboard** → **Run workflow**
(the manual trigger). This fetches live prices, recomputes NAV, and pushes
the updated `index.html`, `latest.json`, and `history.csv`. After that it
runs automatically every weekday at 17:30 WIB (10:30 UTC), after the IDX
closes — see the cron schedule in `.github/workflows/update.yml`.

## Day-to-day use

- **Dashboard stays fresh on its own** — nothing to do.
- **Manual refresh**: Actions tab → Run workflow.
- **Edit stake %, debt, cash**: use the Assumptions panel on the dashboard
  itself (needs the edit password from step 4). Changes commit straight to
  `assumptions.json` and take effect for everyone on the next refresh.
- **Add a new holding**: "+ Add new holding" form on the dashboard. Its
  price history starts from whenever it's added — it isn't retroactively
  backfilled.
- **Audit trail**: every change (automated price update or manual
  assumption edit) is a normal git commit — `git log assumptions.json` shows
  who/when/what changed, no separate logging system needed.

## Known simplifications

- Historical NAV/discount rows before this dashboard existed (seeded from
  `srtg_historical_prices.csv`, Dec 2024 onward) hold ownership %, shares
  outstanding, and balance-sheet figures constant at today's values —only
  the underlying prices are real historical data. Treat the shape/trend as
  indicative for that period; only data appended after this system went
  live reflects the fundamentals as they stood on that date.
- JCI (^JKSE) index history only starts accumulating from the first Actions
  run — older `history.csv` rows have a blank `jci_idx`.
- AGII's stake % (10.00%) still traces to a 2025 broker estimate, not a
  company-verified figure — worth confirming against SRTG's next annual
  report.
