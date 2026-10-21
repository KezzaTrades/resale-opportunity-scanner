# Resale Opportunity Scanner — UK multi-category engine

A working **manual-purchase-only** research system for the [Resale Opportunity Scanner](https://github.com/KezzaTrades/resale-opportunity-scanner). Python 3.12 standard library; no paid data providers, no orders, no API keys in source.

## What's implemented

- Scan real seller offers from a local CSV or an **authorised AWIN CSV/CSV.gz/ZIP product feed**. Optional official eBay Browse API search across a bounded watchlist (20 queries x 50 results).
- Exact product ID, condition and variant segregation; keyword-only eBay title matches are flagged **unverified** and are **never** automatic high-confidence buy candidates.
- Calculate all-in acquisition cost, estimated platform seller fees, selling postage, packaging, additional expenses and reserve for returns. Compute lower-quartile completed-sale benchmark, breakeven, estimated net profit, ROI, margin and a price-dispersion risk indicator.
- Match only **recent, documented, completed sales** (the user must supply evidence, or an independently authorised sold-sales feed must be added). Asking prices, MSRP and eBay active listings are NEVER used as verified sales.
- `BUY_REVIEW`, `WATCH`, `REJECT` verdicts, per-item reasons, ranked JSON report, private Telegram alerts and de-duplication. Shortlists at most £100 of combined acquisition costs. Never executes a purchase.
- Scheduled GitHub Actions scanner, twice daily 08:17 and 18:17 UTC (scheduled workflows run only on the GitHub default branch). Runs don't automatically commit deals to a public repository.

## Start locally

```bash
python -m unittest discover -s tests -v
python -m scanner.cli --no-network
```

`--no-network` imports `data/offers.csv` and `data/confirmed_sales.csv`, then generates `.runtime/results.json`. Both CSVs contain **headers only** at first: this is deliberate. A result of zero offers is truthful, not a failed purchase scan. To use the live integrations, configure their credentials and run:

```bash
python -m scanner.cli
```

### Private credentials (GitHub → Settings → Secrets and variables → Actions)

- `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` — optional official eBay Production Browse API; [get application keys](https://developer.ebay.com/my/keys). Browse API searches active listings only, not historical sales; **it will not validate authenticity**. Some Buy APIs require additional approval.
- `AWIN_FEED_URL` — optional HTTPS URL for a datafeed the user is permitted to access as an AWIN partner. Limit is 25 MB compressed and uncompressed. Your feed URL may include private keys, so store as a GitHub Action secret, never in tracked files.
- `SALES_DATA_B64` — optional base64-encoded UTF-8 CSV of genuine documented completed sales using the `data/confirmed_sales.csv` header. Store as a secret, since sales sourcing is commercially sensitive. Never store private sale data in this **public** repository. No sold-data integration has been automatically approved.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — optional private Telegram alert delivery. Create a bot with BotFather and request its chat ID, then add both as secrets. Failed deliveries don't get recorded as sent.

**Safety:** A GitHub secret is never displayed in the output. Don't put sales data or tokens in the public repository. Never add untrusted sellers as `authorised_feed` without checking them. Configure the correct shipping and seller fee rules in `config/rules.json` for YOUR selling account. Default 12.8% plus £0.30, and 5% return reserve, are *editable planning assumptions*, not confirmed 2026 eBay fees. Fees, buyer returns, tax and shipping change by platform and seller type; profits are estimates, not guaranteed.

## Import templates

`data/offers.csv`:

`product_id,title,variant,condition,category,retailer,source,source_trust,identity_verified,url,currency,price,shipping_in,other_buy_cost,available`

For an offer manually validated by you: `source_trust=user_reviewed`, `identity_verified=true`; cost fields in GBP. Product ID should be a genuine exact GTIN or manufacturer code matched to sales.

`data/confirmed_sales.csv`:

`product_id,variant,condition,currency,status,evidence_type,sold_at,sold_price,evidence_url`

Completed sale rows must have `status=sold`, `evidence_type=confirmed_transaction`, a recent ISO date (`YYYY-MM-DD`), a unique source URL and a GBP realised price. **A user-provided URL is only user-attested evidence, not independently authenticated by this software.** Only enter legally available transaction data, and avoid exposing personal details.

`data/watchlist.csv` controls the eBay search terms and strict token check. These are SEARCHES, not product authentication. Extend across LEGO, electronics, gaming, collectibles etc. Some eBay results have unknown delivery cost and will be ineligible for a profit forecast. In the future authorised automated sold-data sources can be attached in place of manual transaction import.

## Financial formulas

- Acquisition = purchase price + inbound shipping + other buying costs.
- Conservative sale benchmark = nearest-rank 25th percentile of documented, recent, same-product, same-variant, same-condition sales.
- Estimated net profit = benchmark − acquisition − platform % and fixed fees − outbound shipping − packaging − return reserve − other fixed costs.
- ROI % = net profit / acquisition × 100. Margin % = net profit / resale value × 100.
- Breakeven price solves for a sale price that covers both percentage-based costs and fixed costs.
- Initial minimums: **£100 combined shortlist capital; £50 maximum acquisition including shipping; £8 minimum estimated net profit; 30% minimum ROI; at least three recent documented completed sales**.

The engine **never buys automatically**. The `BUY_REVIEW` classification is not an instruction to buy; check authenticity, stock, condition, title variations, fee tiers, delivery, payment restrictions, demand and returns first.

## Test and deployment

`python -m unittest discover -s tests -v` covers budget, postage, fee calculation, stale/incorrect/duplicated comps, currency, identity, variant, source trust, no-history, duplicate opportunities, eBay matching, portfolio allocation and alert safety.

The GitHub Actions workflow includes a scheduled scan and manual dispatch. It tests before scanning, and caches hashed alert identifiers between runs. It does not upload private deal lists as public artifacts. The schedule on a feature branch does not run until the workflow is merged to `main`. GitHub Actions usage may be billable depending on account plan and usage; monitor limits and disable the schedule if necessary.

**Important capability limit:** Without verified sold-history data, the scanner can find active deals but cannot responsibly confirm resale profit or alert a recommended purchase. eBay's Marketplace Insights sold-history API is restricted to selected developers; this program does NOT claim access to it.
