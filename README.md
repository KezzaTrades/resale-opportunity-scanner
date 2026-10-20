# UK multi-category resale opportunity engine

**Status (October 2026):** source code and tests implemented; live opportunity monitoring is **not enabled** until the owner supplies a legally authorised product-feed source, independently verifiable comparable sold-price data, and notification credentials. No retailer accounts or payment cards are connected. **No automatic real-money purchasing.**

## Budget and risk settings

| Setting | Value |
| --- | --- |
| Starting capital | £100 |
| Max individual item | £50 |
| Emergency/expenses reserve | £20 |
| Max amount reserved for stock | £80 |
| Min projected net profit | £15 |
| Min return on total costs | 30% |
| Minimum comparable listings | 5 |
| Minimum recent sales | 3 |

These are risk filters, **not** promises of profit. Returns, condition, selling fees, postage, refunds, tax and stock turnover must be evaluated before buying. Buy only after verifying exact item identity and sold-price evidence. The engine does not guarantee listings are good buys or that an item will sell.

## Architecture and stages

- **Stage 1: research and notifications.** Import an owner-supplied authorised CSV or fetch an opt-in HTTPS CSV from an explicitly approved domain. Reject stale valuation timestamps (older than seven days), missing evidence links, low profits, excessive prices, and unsupported categories. Qualified leads can be sent by Telegram using `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables. Tokens must live in encrypted GitHub secrets or local environment variables, **never in source control**. CSV inputs are untrusted; evidence links are **not independently audited** by this program.
- **Stage 2: assisted checkout.** Owner manually approves a qualified opportunity, reserves its budget allocation and gets a read-only product link and max purchase price. The user must buy on the official merchant website and manually record purchase/cancellation. No card or retailer login is stored.
- **Stage 3: sandbox guardrails.** Simulate policy decisions for authorised merchant, exact product, fresh inventory, max item spend, and available capital. `auto_purchase` always denies real orders. Turning on automatic real-money purchasing requires a *separate* mandate, approved retailer checkout API, secure payment infrastructure, reconciliation, regulatory review and integration tests.

## Local setup (Python >=3.10, no dependencies)

```sh
python -m unittest discover -s tests -v
python run.py budget
python run.py import /path/to/authorised-offers-with-evidence.csv
python run.py scan
python run.py alerts                   # safe dry-run
python run.py alerts --send-telegram   # requires bot credentials
python engine.py approve 1 owner      # manual approval record
python run.py reserve 1               # bookkeeping only
python engine.py checkout 1           # product URL and price only
python run.py settle 1 --state CANCELLED
python run.py sandbox-buy '{"price":25,"exact_sku_verified":true,"approved_merchant":true,"fresh_inventory":true}'
```

The database defaults to `resale.db` (ignored by Git). Set `RESALE_DB` to a persistent filesystem path in a secure runtime. `config.json` specifies user limits. **Do not commit your actual stock inventory, customer data, credentials or valuation-provider account information.**

## Authorised live feed

Provide an HTTPS CSV **you are licensed to access**, using a single approved host and feed URL as environment secrets or variables:

```
RESALE_APPROVED_FEED_URL=https://YOUR-AUTHORISED-HOST.example/feed.csv
RESALE_APPROVED_FEED_HOST=YOUR-AUTHORISED-HOST.example
```

Run `python run.py feed`. The fetcher enforces exact hostname, TLS, no redirects, 2 MB size, and CSV content type. It doesn't scrape websites, bypass robots/anti-bot controls, or discover live deals on its own. The feed must have CSV headers:

```
source,sku,title,category,url,purchase_price,expected_sale,fees,outbound_postage,packaging,return_reserve,recent_sales,comparable_count,condition_verified,valuation_source,valuation_checked_at
```

Money is GBP; `fees`, `outbound_postage`, `packaging` and `return_reserve` are **absolute GBP** values; timestamps are ISO-8601 timezone-aware and less than seven days old. `valuation_source` is a reference link supplied by the provider, not proof that this engine has verified the sales itself. Do not fabricate recent sales or valuations. Price/stock freshness on live merchant sites must be checked again before checkout.

## GitHub Actions

- `Resale engine safety tests`: runs unit tests on changes / PRs.
- `Resale scan (manual only)`: manual opt-in run via **Actions → Resale scan (manual only) → Run workflow**. It requires four GitHub Actions secrets: `RESALE_APPROVED_FEED_URL`, `RESALE_APPROVED_FEED_HOST`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. **Do not trigger until all four are correctly configured.** Workflow is intentionally **not scheduled**. No paid infrastructure is provisioned.
- Important: GitHub-hosted runners are ephemeral. Each manual scan starts a fresh SQLite database, so the same qualified deal can be re-alerted in a later run. For recurring production monitoring, deploy persistent encrypted storage and end-to-end deduplication first. This repo intentionally avoids claiming otherwise. GitHub Actions billing/quotas depend on your plan and settings; monitor usage and do not enable recurring jobs without checking costs.

## Next required external setup

1. Agree an actual legitimate retailer/product feed and *separate* legally accessible recent sold-price evidence source.
2. Create a Telegram bot with BotFather and get your private chat ID; store both as repo secrets. Don't paste tokens into chat.
3. Verify merchant URLs, authentic items, business seller requirements, UK consumer rights and tax responsibilities before trading.
4. Choose persistent hosting/data storage if always-on scans and reliable cross-run alert deduplication are needed. Paid deployment requires separate approval.
5. Keep live purchase transactions off until independently authorised and supported through merchant APIs.

## Files currently on this GitHub branch

- `scanner.py` is the deployed source-code implementation; `tests/test_scanner.py` contains repository CI tests.
- `.github/workflows/resale-tests.yml` runs safety checks. `.github/workflows/manual-feed-scan.yml` is manual **dry-run only** (does not send real Telegram messages).
- The extended local reference implementation is available as a separate downloadable ZIP in this conversation; GitHub branch and ZIP are not byte-identical.
