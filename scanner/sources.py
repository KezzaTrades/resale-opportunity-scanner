"""Approved structured product feeds and optional official eBay Browse search.

No browser scraping, no hidden paid services, no automated purchasing.
"""
import base64
import csv
import gzip
import io
import json
import os
import pathlib
import re
import urllib.parse
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation


MAX_FEED_BYTES = 25_000_000


def read_csv(path):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    with p.open('r', encoding='utf-8-sig', newline='') as f:
        return [dict(item) for item in csv.DictReader(f)]


def _headers():
    return {'User-Agent': 'ResaleOpportunityScanner/1.0 (authorised product feed only)'}


def _fetch_data(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https':
        raise ValueError('Feed URL must use HTTPS')
    with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()), timeout=25) as response:
        data = response.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError('Feed too large (>25 MB); download and slice offline')
    if data[:2] == b'\x1f\x8b':
        data = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_FEED_BYTES + 1)
    elif zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith('.csv')]
            if len(members) != 1 or members[0].file_size > MAX_FEED_BYTES:
                raise ValueError('Expected exactly one bounded CSV in ZIP')
            data = archive.read(members[0])
    if len(data) > MAX_FEED_BYTES:
        raise ValueError('Uncompressed feed exceeded limit')
    return data.decode('utf-8-sig')


def _field(row, *keys):
    canon = {k.strip().lower(): v for k, v in row.items() if k}
    for k in keys:
        v = canon.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ''


def awin_rows(text, max_products=10000):
    """Normalise a configured, authorised AWIN publisher datafeed.

    Shipping must exist explicitly to qualify as a financially complete offer.
    EAN/GTIN is the verified identity; merchant SKU alone is not cross-retailer proof.
    """
    result = []
    for row in csv.DictReader(io.StringIO(text)):
        if len(result) >= max_products:
            break
        name = _field(row, 'product_name', 'name', 'title')
        price = _field(row, 'search_price', 'price', 'store_price')
        ean = _field(row, 'ean', 'gtin', 'barcode', 'upc')
        url = _field(row, 'aw_deep_link', 'deep_link', 'merchant_deep_link', 'product_url')
        shipping = _field(row, 'delivery_cost', 'delivery_price', 'shipping_cost')
        status = _field(row, 'in_stock', 'stock_status', 'availability').casefold()
        if not (name and price and ean and url):
            continue
        if not url.startswith(('http://', 'https://')):
            continue
        result.append(dict(product_id=ean, title=name, variant=_field(row, 'variant', 'size', 'colour') or 'standard',
                           condition='new', category=_field(row, 'category_name', 'category') or 'other',
                           retailer=_field(row, 'merchant_name', 'advertiser_name') or 'AWIN merchant',
                           source='awin_authorised_feed', source_trust='authorised_feed', identity_verified=True,
                           url=url, currency=_field(row, 'currency', 'currency_code') or 'GBP',
                           price=price, shipping_in=shipping or None,
                           available=status in ('in stock', 'in_stock', '1', 'true', 'available', 'yes')))
    return result


def load_awin():
    local = os.getenv('AWIN_FEED_FILE')
    url = os.getenv('AWIN_FEED_URL')
    if local:
        data = pathlib.Path(local).read_text(encoding='utf-8-sig')
    elif url:
        data = _fetch_data(url)
    else:
        return []
    return awin_rows(data)


def _request_json(url, token, marketplace='EBAY_GB'):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'X-EBAY-C-MARKETPLACE-ID': marketplace}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.loads(response.read(3_000_000))


def _ebay_token(client_id, secret):
    credential = base64.b64encode(f'{client_id}:{secret}'.encode()).decode()
    body = urllib.parse.urlencode({'grant_type': 'client_credentials',
                                   'scope': 'https://api.ebay.com/oauth/api_scope'}).encode()
    req = urllib.request.Request('https://api.ebay.com/identity/v1/oauth2/token',
                                 data=body, headers={'Authorization': 'Basic '+credential,
                                                     'Content-Type': 'application/x-www-form-urlencoded'},
                                 method='POST')
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)['access_token']


def ebay_offer_rows(summary, watch):
    token = str(watch.get('required_token', '')).strip()
    if len(token) < 4 or not watch.get('product_id'):
        return []
    result = []
    for item in summary.get('itemSummaries', []):
        title = str(item.get('title', ''))
        if not re.search(r'(?<![a-zA-Z0-9])'+re.escape(token)+r'(?![a-zA-Z0-9])', title, re.IGNORECASE):
            continue
        if 'FIXED_PRICE' not in item.get('buyingOptions', []):
            continue
        item_condition = str(item.get('condition', '')).lower()
        condition = 'new' if item_condition.startswith('new') else 'used'
        expected_condition = str(watch.get('condition', 'new')).lower()
        if condition != expected_condition:
            continue
        price = item.get('price') or {}
        if price.get('currency') != 'GBP' or item.get('itemLocation', {}).get('country') not in ('GB', None):
            continue
        shipping_options = item.get('shippingOptions') or []
        shipping_values = [o.get('shippingCost', {}).get('value') for o in shipping_options
                           if o.get('shippingCost', {}).get('currency') == 'GBP']
        shipping = min((Decimal(str(x)) for x in shipping_values if x is not None), default=None)
        result.append(dict(product_id=watch['product_id'], title=title,
                           variant=watch.get('variant') or 'standard', condition=condition,
                           category=watch.get('category', 'other'), retailer='eBay seller: '+str(item.get('seller', {}).get('username', 'unknown')),
                           source='ebay_browse', source_trust='marketplace_unverified', identity_verified=False,
                           url=item.get('itemWebUrl', ''), currency='GBP', price=price.get('value'),
                           shipping_in=str(shipping) if shipping is not None else None,
                           available=True))
    return result


def load_ebay(watches):
    client_id = os.getenv('EBAY_CLIENT_ID')
    secret = os.getenv('EBAY_CLIENT_SECRET')
    if not (client_id and secret):
        return []
    token = _ebay_token(client_id, secret)
    result = []
    for watch in watches[:20]:  # bounds daily API usage
        if not watch.get('query') or not watch.get('required_token'):
            continue
        params = urllib.parse.urlencode({'q': watch['query'], 'limit': '50',
                                          'filter': 'buyingOptions:{FIXED_PRICE},deliveryCountry:GB'})
        url = 'https://api.ebay.com/buy/browse/v1/item_summary/search?' + params
        result.extend(ebay_offer_rows(_request_json(url, token), watch))
    return result
