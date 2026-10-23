import asyncio
import copy
import json
import math
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import server
from entry_rules import record_exit
from paper_distributions import (VERIFIED_DISTRIBUTIONS, distribution_review_reason,
                                 reconcile_elme, settle_receivables, quote_actions)
from strategy import analyse


def held_position():
    return {"symbol": "ELME", "entry": 1.75, "quantity": 100,
            "target": 1.82, "stop": 1.715, "opened_at": "2026-10-06T16:05:00+00:00"}


def live_quote(price=0.03):
    stamp = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc).timestamp()
    return {"price": price, "source": "live", "provider": "yahoo_intraday",
            "quote_timestamp": stamp, "corporate_actions_checked": True,
            "corporate_actions": [{"kind": "dividends", "ex_date": "2026-10-23",
                                   "amount_per_share": 1.74}]}


class DistributionTests(unittest.TestCase):
    def test_credit_thresholds_and_restart_are_idempotent(self):
        state = {"cash": 0}; p = held_position(); q = live_quote()
        self.assertIsNone(distribution_review_reason(state, p, q))
        self.assertAlmostEqual(state["cash"], 174)
        self.assertAlmostEqual(p["target"], 0.08)
        self.assertAlmostEqual(p["stop"], -0.025)
        state, p = json.loads(json.dumps(state)), json.loads(json.dumps(p))
        self.assertIsNone(distribution_review_reason(state, p, q))
        self.assertAlmostEqual(state["cash"], 174)
        self.assertEqual(len(state["distribution_ledger"]), 1)
        self.assertAlmostEqual(p["target"], 0.08)

    def test_purchase_on_ex_date_is_not_entitled(self):
        p = held_position(); p["opened_at"] = "2026-10-23T13:35:00+00:00"
        state = {"cash": 0}
        self.assertIsNone(distribution_review_reason(state, p, live_quote()))
        self.assertEqual(state["cash"], 0)
        self.assertNotIn("distribution_per_share", p)

    def test_unknown_action_mismatched_amount_and_split_pause_without_credit(self):
        for action in [{"kind": "splits", "ex_date": "2026-10-23"},
                       {"kind": "dividends", "ex_date": "2026-10-23", "amount_per_share": 1.7}]:
            state={"cash":0};q=live_quote();q["corporate_actions"]=[action]
            self.assertIn("Unrecognised", distribution_review_reason(state, held_position(), q))
            self.assertEqual(state["cash"], 0)

    def test_missing_entry_date_never_invents_entitlement(self):
        p=held_position();p.pop("opened_at")
        state={"cash":0}
        self.assertIn("entry-date", distribution_review_reason(state, p, live_quote()))
        self.assertEqual(state["cash"], 0)

    def test_receivable_not_spendable_before_pay_date(self):
        action=dict(VERIFIED_DISTRIBUTIONS[0],pay_date="2026-10-26")
        state={"cash":0};p=held_position()
        with patch("paper_distributions.VERIFIED_DISTRIBUTIONS", (action,)):
            self.assertIsNone(distribution_review_reason(state,p,live_quote()))
        self.assertEqual(state["cash"],0)
        self.assertEqual(state["distribution_ledger"][0]["status"],"receivable")
        self.assertTrue(settle_receivables(state,"2026-10-26"))
        self.assertEqual(state["cash"],174)
        self.assertFalse(settle_receivables(state,"2026-10-26"))

    def test_closed_trade_reconciliation_preserves_execution_and_is_idempotent(self):
        qty=531.4399542057323
        trade={"symbol":"ELME","entry":1.75,"exit":0.03,"quantity":qty,
               "reason":"STOP HIT","return_pct":-98.2857142857,
               "exit_at":"2026-10-23T13:46:14.705382+00:00"}
        state={"cash":qty*0.03,"trades":[trade]}
        evidence={"quantity":qty,"held_on":"2026-10-13","backup":"verified state backup"}
        self.assertTrue(reconcile_elme(state,evidence))
        self.assertAlmostEqual(state["cash"],940.6487189441461)
        self.assertAlmostEqual(trade["return_pct"],1.142857142857)
        self.assertEqual(trade["reason"],"STOP HIT")
        self.assertEqual(trade["exit"],0.03)
        self.assertAlmostEqual(trade["profit_gross"],10.628799084114576)
        again=json.loads(json.dumps(state))
        self.assertFalse(reconcile_elme(again,evidence))
        self.assertEqual(again,state)

    def test_reconciliation_rejects_wrong_evidence(self):
        state={"cash":3,"trades":[{"symbol":"ELME","entry":1.75,"exit":0.03,
               "quantity":100,"return_pct":-98.28,"exit_at":"2026-10-23T14:00:00+00:00"}]}
        with self.assertRaises(ValueError):
            reconcile_elme(state,{"quantity":99,"held_on":"2026-10-13"})
        self.assertEqual(state["cash"],3)

    def test_trade_total_return_includes_distribution(self):
        p=held_position();p["distribution_per_share"]=1.74;state={}
        record_exit(state,p,0.03,"MANUAL SELL")
        self.assertAlmostEqual(state["trades"][0]["return_pct"],1.142857142857)
        self.assertAlmostEqual(state["trades"][0]["price_return_pct"],-98.2857142857)

    def test_event_parser_handles_verified_ex_date(self):
        stamp=live_quote()["quote_timestamp"]
        result=quote_actions({"meta":{"currency":"USD"},"events":{"dividends":{
            str(int(stamp)):{"date":stamp,"amount":1.74}}}})
        self.assertEqual(result["corporate_actions"][0]["ex_date"],"2026-10-23")
        self.assertTrue(result["corporate_actions_checked"])


class AccountingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state=server.default_paper_state();self.state["cash"]=0
        self.state["position"]=held_position()
        self.stack=[]
        for name,value in [("PAPER_STATE",self.state),("PAPER_MUTATION_LOCK",asyncio.Lock()),
                           ("save_paper_state",lambda:None),("market_quote",AsyncMock(return_value=live_quote()))]:
            p=patch.object(server,name,value);p.start();self.addCleanup(p.stop)

    async def test_distribution_prevents_false_stop_and_manual_sale_keeps_cash(self):
        await server.paper_check()
        self.assertIsNotNone(self.state["position"])
        self.assertEqual(self.state["trades"],[])
        self.assertAlmostEqual(self.state["cash"],174)
        await server.paper_sell()
        self.assertAlmostEqual(self.state["cash"],177)
        self.assertAlmostEqual(self.state["trades"][0]["return_pct"],1.142857142857)

    async def test_auto_target_sale_keeps_distribution_cash(self):
        server.market_quote.return_value=live_quote(0.09)
        await server.paper_check()
        self.assertIsNone(self.state["position"])
        self.assertAlmostEqual(self.state["cash"],183)
        self.assertEqual(self.state["trades"][0]["reason"],"TARGET HIT")

    async def test_unknown_action_pauses_automatic_sale(self):
        server.market_quote.return_value["corporate_actions"][0]["amount_per_share"]=1.7
        await server.paper_check()
        self.assertIsNotNone(self.state["position"])
        self.assertEqual(self.state["cash"],0)
        self.assertIn("paused",self.state["position"]["monitoring_status"])

    async def test_new_elme_entry_is_blocked(self):
        self.state["position"]=None
        with self.assertRaises(server.HTTPException) as exc:
            await server.paper_buy("ELME")
        self.assertIn("Liquidating",exc.exception.detail)

    async def test_bad_stock_does_not_abort_scanner(self):
        candles=[{"time":"2026-10-22","open":100,"high":102,"low":98,"close":100,"volume":1} for _ in range(60)]
        bad=copy.deepcopy(candles);bad[-21]["close"]=0
        with patch.object(server,"market_candles",AsyncMock(side_effect=[{"candles":bad},{"candles":candles}])):
            scan=await server.scanner_scan(symbols="BAD,GOOD",validate_live=False)
        self.assertEqual(scan["count"],2)
        self.assertEqual(scan["valid_count"],1)
        self.assertEqual(scan["unavailable"][0]["symbol"],"BAD")


class HistoricalValidationTests(unittest.TestCase):
    def test_zero_negative_nan_and_infinite_prices_never_qualify(self):
        for value in [0,-1,float("nan"),float("inf")]:
            candles=[{"open":10,"high":11,"low":9,"close":10,"volume":1} for _ in range(60)]
            candles[-21]["close"]=value
            result=analyse(candles)
            self.assertTrue(result["invalid_data"])
            self.assertFalse(result["qualified"])


if __name__=="__main__":
    unittest.main()
