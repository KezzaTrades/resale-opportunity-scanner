"""Command-line scan, machine-readable report, and optional private Telegram alerts."""
import argparse
import base64
import csv
import io
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

from .analysis import Rules, scan
from .sources import read_csv, load_awin, load_ebay


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('yes', 'true', '1')


def _json_default(obj):
    return str(obj)


def _send_telegram(text):
    bot = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not bot or not chat:
        return False
    url = f'https://api.telegram.org/bot{bot}/sendMessage'
    data = urllib.parse.urlencode({'chat_id': chat, 'text': text,
                                   'disable_web_page_preview': 'true'}).encode('utf-8')
    # Do not echo URLs containing tokens or exception details into public CI logs.
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=12) as response:
            result = json.loads(response.read(10000))
        return result.get('ok') is True
    except Exception:
        return False


def notify(results, state_file):
    path = Path(state_file)
    try:
        sent = set(json.loads(path.read_text()).get('sent', [])) if path.exists() else set()
    except (ValueError, OSError):
        sent = set()
    notifications = 0
    for row in results:
        if row['verdict'] != 'BUY_REVIEW':
            continue
        key = f"{row['id']}|{row['acquisition_cost']}|{row['sale_estimate']}"
        if key in sent:
            continue
        note = ('RESELL DEAL — REVIEW BEFORE BUYING\n'
                + row['title'][:130] + '\n'
                + f"Buy incl shipping: £{row['acquisition_cost']:.2f}\n"
                + f"Conservative sold estimate: £{row['sale_estimate']:.2f}\n"
                + f"Projected net profit: £{row['net_profit']:.2f}\n"
                + f"ROI: {row['roi_percent']:.1f}% | completed sales: {row['sold_count']}\n"
                + 'Sales evidence is user-supplied. Verify fees, identity, stock and demand yourself.\n'
                + row['url'])
        if _send_telegram(note):
            sent.add(key)
            notifications += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'sent': sorted(sent)[-2000:]}, indent=2))
    return notifications


def main(argv=None):
    parser = argparse.ArgumentParser(description='UK resale scanner (analysis only; no checkout)')
    parser.add_argument('--offers', default='data/offers.csv', help='Local authorised offers CSV')
    parser.add_argument('--sales', default='data/confirmed_sales.csv', help='Documented completed sales CSV')
    parser.add_argument('--watchlist', default='data/watchlist.csv')
    parser.add_argument('--rules', default='config/rules.json')
    parser.add_argument('--out', default='.runtime/results.json')
    parser.add_argument('--state', default='.runtime/alerts-state.json')
    parser.add_argument('--telegram', action='store_true', help='Notify review-worthy results if secrets configured')
    parser.add_argument('--no-network', action='store_true', help='Run only local structured inputs')
    args = parser.parse_args(argv)

    rules = Rules.from_mapping(json.loads(Path(args.rules).read_text(encoding='utf-8')))
    offers = read_csv(args.offers)
    for row in offers:
        row['identity_verified'] = _bool(row.get('identity_verified'))
        row['available'] = _bool(row.get('available', 'true'))
    feeds = {'local': len(offers), 'awin': 0, 'ebay': 0}
    errors = []
    if not args.no_network:
        # A feed error must never silently become a zero-deal success.
        for label, fetcher in [('awin', load_awin), ('ebay', lambda: load_ebay(read_csv(args.watchlist)))]:
            try:
                incoming = fetcher()
                feeds[label] = len(incoming)
                offers.extend(incoming)
            except Exception as exc:
                errors.append(f'{label}: {type(exc).__name__}; check feed access/credentials')
    sales = read_csv(args.sales)
    # Optional private GitHub Actions secret. Never persist, print or upload this CSV.
    private_b64 = os.getenv('SALES_DATA_B64', '')
    if private_b64:
        try:
            raw = base64.b64decode(private_b64, validate=True)
            if len(raw) > 200_000:
                raise ValueError('Private sales data exceeds 200k bytes')
            sales.extend(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        except Exception:
            errors.append('private sales CSV: invalid encoded input')
    result = scan(offers, sales, rules)
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'feeds': feeds,
              'offers_evaluated': len(result), 'sales_rows_supplied': len(sales),
              'rules': {key: str(value) for key, value in vars(rules).items()},
              'source_errors': errors, 'opportunities': result}
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, default=_json_default), encoding='utf-8')
    count = sum(x['verdict'] == 'BUY_REVIEW' for x in result)
    sent = notify(result, args.state) if args.telegram and not errors else 0
    print(f"Scan: {len(result)} listings | {count} purchase-review candidates | {sent} Telegram alerts")
    print('Feeds:', feeds, '| Source issues:', len(errors), '| Report:', dest)
    if errors:
        print('Source issues:', '; '.join(errors))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
