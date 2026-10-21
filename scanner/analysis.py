"""Conservative resale financial analysis; standard library only.

A BUY_REVIEW is an invitation to independently verify the listing, not an order.
Asking prices and predicted MSRP are never used as evidence of realised sales.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import hashlib
import json
from statistics import median


D = Decimal


def money(value):
    try:
        result = D(str(value).replace('£', '').strip()).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, AttributeError):
        raise ValueError(f'Invalid money amount: {value!r}') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Money values must be finite and nonnegative')
    return result


def date_utc(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical(value):
    return ' '.join(str(value or '').casefold().split())


def percentile_nearest_rank(values, proportion):
    seq = sorted(values)
    if not seq:
        raise ValueError('No values')
    import math
    return seq[max(0, math.ceil(len(seq) * proportion) - 1)]


@dataclass(frozen=True)
class Rules:
    capital: Decimal = D('100')
    max_purchase: Decimal = D('50')
    min_profit: Decimal = D('8')
    min_roi_percent: Decimal = D('30')
    min_sold: int = 3
    max_sold_age_days: int = 60
    seller_fee_percent: Decimal = D('12.8')  # editable planning assumption, NOT an eBay quote
    seller_fee_fixed: Decimal = D('0.30')
    outbound_postage: Decimal = D('3.49')
    packaging: Decimal = D('0.35')
    return_reserve_percent: Decimal = D('5')
    additional_fixed_cost: Decimal = D('0')

    @classmethod
    def from_mapping(cls, obj):
        defaults = asdict(cls())
        if set(obj) - set(defaults):
            raise ValueError(f'Unknown rules: {sorted(set(obj)-set(defaults))}')
        values = dict(defaults)
        for k, v in obj.items():
            values[k] = int(v) if k in {'min_sold', 'max_sold_age_days'} else money(v)
        rules = cls(**values)
        if rules.max_purchase > rules.capital or rules.min_sold < 1 or rules.max_sold_age_days < 1:
            raise ValueError('Invalid budget or sold sample requirements')
        if rules.seller_fee_percent + rules.return_reserve_percent >= 100:
            raise ValueError('Fee and return reserve total cannot reach 100%')
        return rules


def _num(v):
    return float(v.quantize(D('0.01'), rounding=ROUND_HALF_UP))


def analyse(offer, sold_rows, rules: Rules, now=None):
    """Return report with explicit reasons and conservative realised-sales benchmark."""
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    reasons = []
    ident = canonical(offer.get('product_id'))
    variant = canonical(offer.get('variant', 'standard'))
    condition = canonical(offer.get('condition', 'new'))
    url = str(offer.get('url', '')).strip()
    seller = str(offer.get('retailer', '')).strip()
    title = str(offer.get('title', '')).strip()
    uid = hashlib.sha256(f'{offer.get("source")}|{url}|{ident}|{variant}|{condition}'.encode()).hexdigest()[:20]
    base = dict(id=uid, product_id=ident, title=title, category=offer.get('category', 'other'), variant=variant,
                condition=condition, retailer=seller, source=offer.get('source'), url=url, verdict='REJECT',
                reasons=reasons, sold_count=0, sale_estimate=None, acquisition_cost=None,
                net_profit=None, roi_percent=None, margin_percent=None, break_even_sale=None,
                fee_estimate=None, return_reserve=None, risk_score=None, evidence='none')
    if not ident or not title or not url.startswith(('https://', 'http://')) or not seller:
        reasons.append('Incomplete product identity, title, seller or URL')
        return base
    if offer.get('currency', 'GBP').upper() != 'GBP':
        reasons.append('Non-GBP buying price (FX not modelled)')
        return base
    try:
        price = money(offer.get('price'))
        shipping = money(offer.get('shipping_in'))
        other_buy_cost = money(offer.get('other_buy_cost', 0))
    except (TypeError, ValueError):
        reasons.append('Missing or invalid inbound delivery/purchase costs')
        return base
    acquisition = price + shipping + other_buy_cost
    base['acquisition_cost'] = _num(acquisition)
    if acquisition > rules.max_purchase:
        reasons.append('Exceeds £50 per-item cap including inbound shipping')
    if acquisition > rules.capital:
        reasons.append('Exceeds available starting capital')
    if not offer.get('available', True):
        reasons.append('Out of stock or unavailable')
    if offer.get('identity_verified') is not True:
        reasons.append('Product identity / variant needs manual verification')
    # Reject dubious or unpermitted sources before claiming a high-conviction opportunity.
    if offer.get('source_trust') not in ('authorised_feed', 'user_reviewed'):
        reasons.append('Source not approved as a trusted seller/feed')
    seen = set()
    samples = []
    for rec in sold_rows:
        if canonical(rec.get('product_id')) != ident or canonical(rec.get('variant', 'standard')) != variant:
            continue
        if canonical(rec.get('condition', 'new')) != condition or rec.get('currency', 'GBP').upper() != 'GBP':
            continue
        if canonical(rec.get('status')) != 'sold' or canonical(rec.get('evidence_type')) != 'confirmed_transaction':
            continue
        evidence_link = str(rec.get('evidence_url', '')).strip()
        if not evidence_link.startswith(('https://', 'http://')) or evidence_link in seen:
            continue
        try:
            dt = date_utc(rec['sold_at'])
            if dt > now + timedelta(minutes=5) or now - dt > timedelta(days=rules.max_sold_age_days):
                continue
            value = money(rec['sold_price'])
            if value == 0:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        seen.add(evidence_link)
        samples.append(value)
    base['sold_count'] = len(samples)
    if len(samples) < rules.min_sold:
        reasons.append(f'Insufficient recent documented completed sales ({len(samples)}/{rules.min_sold}); asking prices excluded')
        base['verdict'] = 'WATCH' if not any('Exceeds' in r or 'Non-GBP' in r for r in reasons) else 'REJECT'
        return base
    # Nearest-rank lower quartile conservatively avoids speculative high-priced outliers.
    expected = percentile_nearest_rank(samples, .25)
    variable_fees = expected * rules.seller_fee_percent / D(100)
    reserve = expected * rules.return_reserve_percent / D(100)
    fees = variable_fees + rules.seller_fee_fixed
    cost = (acquisition + fees + rules.outbound_postage + rules.packaging + reserve
            + rules.additional_fixed_cost)
    profit = expected - cost
    denominator = acquisition if acquisition > 0 else D('0.01')
    roi = D(100) * profit / denominator
    margin = D(100) * profit / expected
    variable_fraction = D(1) - (rules.seller_fee_percent + rules.return_reserve_percent) / D(100)
    breakeven = ((acquisition + rules.seller_fee_fixed + rules.outbound_postage + rules.packaging +
                  rules.additional_fixed_cost) / variable_fraction)
    deviation = (max(samples) - min(samples)) / D(median(samples)) if median(samples) else D(1)
    risk_score = min(100, round(float(deviation) * 40) + (15 if len(samples) < 5 else 0) +
                     (25 if offer.get('source_trust') != 'authorised_feed' else 0))
    if risk_score >= 60:
        reasons.append('High volatility in documented sale prices')
    base.update(sold_count=len(samples), sale_estimate=_num(expected), acquisition_cost=_num(acquisition),
                net_profit=_num(profit), roi_percent=_num(roi), margin_percent=_num(margin),
                break_even_sale=_num(breakeven), fee_estimate=_num(fees),
                return_reserve=_num(reserve), risk_score=risk_score,
                evidence='user-supplied documented transactions; independently verify')
    if profit < rules.min_profit:
        reasons.append('Below minimum forecast net profit')
    if roi < rules.min_roi_percent:
        reasons.append('Below minimum forecast return on buying cost')
    if not reasons:
        base['verdict'] = 'BUY_REVIEW'
    elif any('Exceeds' in r for r in reasons):
        base['verdict'] = 'REJECT'
    else:
        base['verdict'] = 'WATCH'
    return base


def scan(offers, comps, rules, now=None):
    results = [analyse(row, comps, rules, now) for row in offers]
    # Avoid duplicate listing items with same variant and condition, keeping lowest acquisition cost.
    unique = {}
    for row in results:
        old = unique.get(row['id'])
        if old is None or ((row['acquisition_cost'] or float('inf')) < (old['acquisition_cost'] or float('inf'))):
            unique[row['id']] = row
    priority = {'BUY_REVIEW': 0, 'WATCH': 1, 'REJECT': 2}
    return sorted(unique.values(), key=lambda x: (priority[x['verdict']],
                                                  -((x['net_profit'] or 0) - (x['risk_score'] or 0) * 0.1),
                                                  -(x['roi_percent'] or 0)))
