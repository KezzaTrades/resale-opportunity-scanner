import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from scanner.analysis import Rules, analyse, scan, money
from scanner.sources import awin_rows, ebay_offer_rows
from scanner.cli import notify

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def example():
    offer = dict(product_id='EAN-123', title='Test product, fictional', category='toy', variant='blue',
                 condition='new', retailer='Trusted store', source='partner', source_trust='authorised_feed',
                 identity_verified=True, url='https://shop.example/item', price='20', shipping_in='3',
                 currency='GBP', available=True)
    sales = [dict(product_id='EAN-123', variant='blue', condition='new', currency='GBP', status='sold',
                  evidence_type='confirmed_transaction', sold_price=v,
                  evidence_url=f'https://sales.example/{i}', sold_at=(NOW-timedelta(days=5+i)).isoformat())
             for i, v in enumerate(['50', '52', '54', '56'])]
    return offer, sales


class EngineTests(unittest.TestCase):
    def test_money_rejects_invalid(self):
        for value in ('nan', 'Infinity', '-3', 'x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_positive_opportunity_realistic_net_costs(self):
        offer, sales = example()
        r = analyse(offer, sales, Rules(), NOW)
        self.assertEqual(r['verdict'], 'BUY_REVIEW')
        self.assertEqual(r['sale_estimate'], 50.0)
        self.assertEqual(r['acquisition_cost'], 23.0)
        self.assertAlmostEqual(r['net_profit'], 13.96, places=2)

    def test_over_budget_rejected(self):
        o, s = example(); o['price'] = 51
        r = analyse(o, s, Rules(), NOW)
        self.assertEqual(r['verdict'], 'REJECT')
        self.assertIn('Exceeds £50', ' '.join(r['reasons']))

    def test_missing_inbound_shipping_blocks_profit(self):
        o, s = example(); o['shipping_in'] = None
        self.assertEqual(analyse(o, s, Rules(), NOW)['verdict'], 'REJECT')

    def test_no_sold_history_means_watch(self):
        o, _ = example()
        r = analyse(o, [], Rules(), NOW)
        self.assertEqual(r['verdict'], 'WATCH')
        self.assertIsNone(r['net_profit'])

    def test_asking_price_is_not_sold_comp(self):
        o, s = example(); s[0]['status'] = 'asking'; s[1]['evidence_type'] = 'listed'
        self.assertEqual(analyse(o, s, Rules(), NOW)['sold_count'], 2)

    def test_old_sales_ignored(self):
        o, s = example(); s[0]['sold_at'] = (NOW-timedelta(days=90)).isoformat()
        self.assertEqual(analyse(o, s, Rules(), NOW)['sold_count'], 3)

    def test_future_sales_ignored(self):
        o, s = example(); s[0]['sold_at'] = (NOW+timedelta(days=2)).isoformat()
        self.assertEqual(analyse(o, s, Rules(), NOW)['sold_count'], 3)

    def test_duplicate_sale_urls_excluded(self):
        o, s = example(); s[1]['evidence_url'] = s[0]['evidence_url']
        self.assertEqual(analyse(o, s, Rules(), NOW)['sold_count'], 3)

    def test_different_variant_not_mixed(self):
        o, s = example(); s[0]['variant'] = 'red'; s[1]['condition'] = 'used'
        self.assertEqual(analyse(o, s, Rules(), NOW)['verdict'], 'WATCH')

    def test_unverified_ebay_does_not_signal(self):
        o, s = example(); o['identity_verified'] = False; o['source_trust'] = 'marketplace_unverified'
        self.assertEqual(analyse(o, s, Rules(), NOW)['verdict'], 'WATCH')

    def test_costs_and_fee_thresholds(self):
        o, s = example(); o['price'] = 35
        self.assertEqual(analyse(o, s, Rules(), NOW)['verdict'], 'WATCH')

    def test_non_gbp_rejected(self):
        o, s = example(); o['currency'] = 'USD'
        self.assertEqual(analyse(o, s, Rules(), NOW)['verdict'], 'REJECT')

    def test_sort_priority(self):
        o, s = example(); p = dict(o); p['url'] = 'https://shop.example/other'; p['price'] = 48
        r = scan([p, o], s, Rules(), NOW)
        self.assertEqual(r[0]['verdict'], 'BUY_REVIEW')

    def test_feed_requires_barcode_and_explicit_postage(self):
        csv_data = ('product_name,search_price,ean,aw_deep_link,delivery_cost,merchant_name\n'
                    'Game controller,18,5050000011111,https://merchant.example/1,2.5,Store\n'
                    'Bad row,20,,https://merchant.example/2,1,Store\n')
        r = awin_rows(csv_data)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['shipping_in'], '2.5')

    def test_ebay_exact_token_and_no_autoverification(self):
        summary = {'itemSummaries': [
            {'title':'Genuine LEGO 75379 new', 'buyingOptions':['FIXED_PRICE'], 'condition':'New',
             'price':{'value':'19.99','currency':'GBP'}, 'itemWebUrl':'https://www.ebay.co.uk/itm/1',
             'shippingOptions':[{'shippingCost':{'currency':'GBP','value':'2.99'}}]},
            {'title':'LEGO 753790', 'buyingOptions':['FIXED_PRICE'], 'condition':'New',
             'price':{'value':'19.99','currency':'GBP'}}]}
        watch = {'product_id':'LEGO-75379','required_token':'75379','condition':'new'}
        rows = ebay_offer_rows(summary, watch)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['identity_verified'])

    def test_rules_reject_100_percent_costs(self):
        with self.assertRaises(ValueError):
            Rules.from_mapping({'seller_fee_percent': 98})

    def test_telegram_never_sends_watch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with patch('scanner.cli._send_telegram') as send:
                count = notify([{'verdict':'WATCH'}], directory+'/state.json')
                self.assertEqual(count, 0)
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
