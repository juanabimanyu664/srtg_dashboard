"""
SRTG NAV Dashboard - Fetch, Calculate, Build
----------------------------------------------
Standalone script run by GitHub Actions (no Claude/AI assistant involved).

1. Reads assumptions.json (holdings, stake %, debt, cash - always the
   latest version, including anything just edited from the dashboard
   via the Cloudflare Worker).
2. Fetches current prices via yfinance for every ticker present in
   assumptions.json, plus SRTG itself and the JCI index (^JKSE). The
   ticker list is read dynamically from the file - a newly added
   holding is automatically picked up on the next run.
3. Computes NAV, NAV/share, discount to NAV, and each holding's %
   contribution to NAV.
4. Appends a row to history.csv and writes a fresh latest.json snapshot.
5. Builds index.html from dashboard/template.html.

The GitHub Actions workflow (.github/workflows/update.yml) is
responsible for committing and pushing the resulting changes - this
script only reads/writes local files.
"""

import csv
import json
import os
from datetime import datetime, timedelta, timezone

import yfinance as yf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSUMPTIONS_FILE = os.path.join(BASE_DIR, "assumptions.json")
HISTORY_CSV = os.path.join(BASE_DIR, "history.csv")
LATEST_JSON = os.path.join(BASE_DIR, "latest.json")
TEMPLATE_FILE = os.path.join(BASE_DIR, "dashboard", "template.html")
OUTPUT_HTML = os.path.join(BASE_DIR, "index.html")

# Jakarta (WIB) is a fixed UTC+7 offset year-round - no DST to worry about.
WIB_OFFSET = timedelta(hours=7)
# IDX's regular session ends ~15:49-16:00 WIB. Use 16:00 as a safe cutoff:
# before this, treat today's Yahoo Finance bar (if present) as a live/partial
# price, not a finished close, and fall back to the prior confirmed close
# instead. This protects against manual "Run workflow" triggers during
# market hours recording an unfinished day's price as if it were final.
MARKET_CLOSE_HOUR_WIB = 16


def now_wib() -> datetime:
    return datetime.now(timezone.utc) + WIB_OFFSET


def load_assumptions() -> dict:
    with open(ASSUMPTIONS_FILE, "r") as f:
        return json.load(f)


def fetch_last_close(ticker: str):
    """Fetch the most recent CONFIRMED daily close for a ticker.

    If today's IDX session hasn't closed yet (before 16:00 WIB) and Yahoo's
    latest bar is dated today, that bar is a live/partial price, not a
    finished close - fall back to the prior trading day's close instead so
    history.csv only ever records completed sessions. Returns
    (price, date_str) or (None, None).
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d", auto_adjust=False)
        if hist.empty:
            return None, None

        today_wib = now_wib().strftime("%Y-%m-%d")
        if hist.index[-1].strftime("%Y-%m-%d") == today_wib and now_wib().hour < MARKET_CLOSE_HOUR_WIB:
            print(f"  Market not yet closed ({now_wib().strftime('%H:%M')} WIB) - using prior close instead of today's partial price")
            hist = hist.iloc[:-1]
            if hist.empty:
                return None, None

        last = hist.iloc[-1]
        price = float(last["Close"])
        date_str = hist.index[-1].strftime("%Y-%m-%d")
        return price, date_str
    except Exception as e:
        print(f"  FAIL fetching {ticker}: {e}")
        return None, None


def calculate(assumptions: dict, prices: dict, srtg_price, srtg_date, jci_price) -> dict:
    holdings = assumptions["holdings"]
    bs = assumptions["balance_sheet"]

    rows = []
    total_stake_value = 0.0
    for h in holdings:
        price = prices.get(h["ticker"])
        shares_out = h.get("shares_outstanding_bn")
        stake_pct = h.get("stake_pct")

        market_cap = None
        stake_value = None
        if price is not None and shares_out is not None:
            market_cap = price * shares_out
            if stake_pct is not None:
                stake_value = market_cap * (stake_pct / 100.0)
                total_stake_value += stake_value

        rows.append({
            "ticker": h["ticker"],
            "company": h["company"],
            "price_idr": price,
            "shares_outstanding_bn": shares_out,
            "stake_pct": stake_pct,
            "market_cap_idr_bn": round(market_cap, 2) if market_cap is not None else None,
            "stake_value_idr_bn": round(stake_value, 2) if stake_value is not None else None,
            "as_of": h.get("as_of"),
        })

    non_listed = bs.get("non_listed_investment_idr_bn") or 0.0
    debt = bs.get("debt_idr_bn") or 0.0
    cash = bs.get("cash_idr_bn") or 0.0
    shares_out_srtg = bs.get("srtg_shares_outstanding_bn")

    nav = total_stake_value + non_listed + cash - debt
    nav_per_share = nav / shares_out_srtg if shares_out_srtg else None
    srtg_market_cap = srtg_price * shares_out_srtg if (srtg_price and shares_out_srtg) else None
    discount_to_nav = None
    if nav_per_share and srtg_price:
        discount_to_nav = (1 - (srtg_price / nav_per_share)) * 100

    for r in rows:
        r["pct_of_nav"] = round((r["stake_value_idr_bn"] / nav) * 100, 2) if (r["stake_value_idr_bn"] is not None and nav) else None

    result = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of_date": srtg_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "holdings": rows,
        "total_stake_value_idr_bn": round(total_stake_value, 2),
        "non_listed_investment_idr_bn": non_listed,
        "non_listed_investment_note": bs.get("non_listed_investment_note"),
        "debt_idr_bn": debt,
        "debt_as_of": bs.get("debt_as_of"),
        "cash_idr_bn": cash,
        "cash_as_of": bs.get("cash_as_of"),
        "nav_idr_bn": round(nav, 2),
        "srtg_shares_outstanding_bn": shares_out_srtg,
        "nav_per_share_idr": round(nav_per_share, 2) if nav_per_share is not None else None,
        "srtg_price_idr": srtg_price,
        "srtg_market_cap_idr_bn": round(srtg_market_cap, 2) if srtg_market_cap is not None else None,
        "discount_to_nav_pct": round(discount_to_nav, 2) if discount_to_nav is not None else None,
        "jci_idx": jci_price,
    }
    return result


def append_history(result: dict, holdings_order):
    row = {
        "date": result["as_of_date"],
        "srtg_price_idr": result["srtg_price_idr"],
    }
    for h in result["holdings"]:
        row[h["ticker"]] = h["price_idr"]
    row["jci_idx"] = result["jci_idx"]
    row["nav_idr_bn"] = result["nav_idr_bn"]
    row["nav_per_share_idr"] = result["nav_per_share_idr"]
    row["discount_to_nav_pct"] = result["discount_to_nav_pct"]

    fieldnames = ["date", "srtg_price_idr"] + holdings_order + ["jci_idx", "nav_idr_bn", "nav_per_share_idr", "discount_to_nav_pct"]

    file_exists = os.path.isfile(HISTORY_CSV)
    existing_rows = []
    if file_exists:
        with open(HISTORY_CSV, newline="") as f:
            reader = csv.DictReader(f)
            existing_fieldnames = reader.fieldnames
            existing_rows = list(reader)
            # If a new ticker was added, existing_fieldnames won't have it -
            # rewrite the whole file with the new schema (old rows get blanks).
            if existing_fieldnames != fieldnames:
                file_exists = False  # force full rewrite below

    if not file_exists:
        with open(HISTORY_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in existing_rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
            # Skip appending duplicate for today if already the last row
            if not existing_rows or existing_rows[-1].get("date") != row["date"]:
                w.writerow(row)
        return

    # Same schema - just append (avoid duplicate rows for the same date on reruns)
    with open(HISTORY_CSV, newline="") as f:
        last_date = None
        for r in csv.DictReader(f):
            last_date = r["date"]
    if last_date == row["date"]:
        print(f"  history.csv already has a row for {row['date']}, skipping append")
        return

    with open(HISTORY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writerow(row)


def build_dashboard(result: dict):
    with open(HISTORY_CSV, newline="") as f:
        history_rows = list(csv.DictReader(f))

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__LATEST_JSON__", json.dumps(result))
    html = html.replace("__HISTORY_JSON__", json.dumps(history_rows))
    html = html.replace("__ASSUMPTIONS_JSON__", json.dumps(load_assumptions()))

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Built {OUTPUT_HTML}")


def main():
    assumptions = load_assumptions()
    holdings = assumptions["holdings"]

    print("Fetching prices via yfinance...")
    prices = {}
    for h in holdings:
        yf_ticker = h["ticker"] + ".JK"
        price, date_str = fetch_last_close(yf_ticker)
        prices[h["ticker"]] = price
        print(f"  {h['ticker']:6s} ({yf_ticker}): {price}")

    srtg_price, srtg_date = fetch_last_close(assumptions.get("srtg_ticker", "SRTG.JK"))
    print(f"  SRTG   ({assumptions.get('srtg_ticker', 'SRTG.JK')}): {srtg_price} as of {srtg_date}")

    jci_price, _ = fetch_last_close(assumptions.get("jci_ticker", "^JKSE"))
    print(f"  JCI    ({assumptions.get('jci_ticker', '^JKSE')}): {jci_price}")

    result = calculate(assumptions, prices, srtg_price, srtg_date, jci_price)

    with open(LATEST_JSON, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote {LATEST_JSON}")

    holdings_order = [h["ticker"] for h in holdings]
    append_history(result, holdings_order)
    print(f"Updated {HISTORY_CSV}")

    build_dashboard(result)

    print("\nSRTG NAV Snapshot")
    print(f"  As of:              {result['as_of_date']}")
    print(f"  Total NAV:          IDR {result['nav_idr_bn']:,.2f} bn")
    print(f"  NAV per share:      IDR {result['nav_per_share_idr']:,.2f}" if result['nav_per_share_idr'] else "  NAV per share:      N/A")
    print(f"  SRTG price:         IDR {result['srtg_price_idr']:,.2f}" if result['srtg_price_idr'] else "  SRTG price:         N/A")
    print(f"  Discount to NAV:    {result['discount_to_nav_pct']:.2f}%" if result['discount_to_nav_pct'] is not None else "  Discount to NAV:    N/A")


if __name__ == "__main__":
    main()
