import io, json, os, tempfile, unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import scanner
def row(**updates):
    r=dict(source="Licensed CSV",sku="MODEL-100",title="Example product",category="gaming",url="https://shop.example/p/100",purchase_price="35",expected_sale="75",fees="8",outbound_postage="4",packaging="1",return_reserve="2",recent_sales="8",comparable_count="6",condition_verified="true",valuation_source="https://evidence.example/sold/100",valuation_checked_at=datetime.now(timezone.utc).isoformat())
    r.update(updates);return r
class SafetyTests(unittest.TestCase):
    def test_profit(self):
        s=scanner.score(row())
        self.assertEqual(s["profit"],"25.00");self.assertEqual(s["status"],"QUALIFIED")
    def test_cap(self):self.assertEqual(scanner.score(row(purchase_price="51"))["status"],"REVIEW")
    def test_missing_evidence(self):
        with self.assertRaises(ValueError): scanner.score(row(valuation_source=""))
    def test_bad_url(self):
        with self.assertRaises(ValueError):scanner.score(row(url="http://unsafe.invalid"))
    def test_negative_fee(self):
        with self.assertRaises(ValueError):scanner.score(row(fees="-1"))
    def test_no_autopurchase(self):
        with self.assertRaises(PermissionError):scanner.auto_purchase()
    def test_no_unapproved_feed(self):
        with self.assertRaises(ValueError):scanner.fetch("/tmp/test.sqlite",url="https://wrong.example/feed",host="allowed.example")
    def test_reserve_duplicate_and_budget(self):
        with tempfile.TemporaryDirectory() as td:
            db=str(Path(td)/"test.sqlite")
            with scanner.connect(db) as c:c.execute("INSERT INTO deals(key,detail) VALUES (?,?)",("a",json.dumps(scanner.score(row()))))
            checkout=scanner.prepare_checkout(db,1)
            self.assertEqual(checkout["max_item_price"],"35.00")
            self.assertEqual(scanner.available(db),scanner.LIMITS["capital"]-scanner.LIMITS["reserve"]-scanner.gbp(35))
            with self.assertRaises(ValueError):scanner.prepare_checkout(db,1)
            scanner.settle(db,1,"CANCELLED")
            self.assertEqual(scanner.available(db),scanner.LIMITS["capital"]-scanner.LIMITS["reserve"])
    def test_telegram_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            db=str(Path(td)/"test.sqlite")
            with scanner.connect(db) as c:c.execute("INSERT INTO deals(key,detail) VALUES (?,?)",("a",json.dumps(scanner.score(row()))))
            self.assertEqual(scanner.telegram(db)[0]["status"],"DRY_RUN")
            self.assertEqual(scanner.telegram(db)[0]["status"],"DRY_RUN")
if __name__=="__main__":unittest.main()
