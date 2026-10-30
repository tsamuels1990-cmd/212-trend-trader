import copy
import unittest
from momentum_paper import entry_signal, new_state, parse_chart, quote_reason, step

NOW = 1800000000


def snapshot(price=None):
    bars = []
    for i in range(25):
        close = 100 + i * .03
        bars.append({"time": NOW - (25-i)*300, "open": close-.01,
                     "high": close+.005, "low": close-.02, "close": close,
                     "volume": 40000 if i == 24 else 20000})
    return {"price": price or bars[-1]["close"], "bars": bars,
            "quote_time": NOW, "session_start": NOW-25000,
            "session_end": NOW+25000, "currency": "USD"}


def state():
    return new_state(1000., {"symbols": ["AAA"], "target_pct": 1., "stop_pct": .75,
                            "max_hold_minutes": 60., "cost_per_side_pct": .1,
                            "fx_per_side_pct": 0.})


class MomentumTests(unittest.TestCase):
    def test_qualifying_breakout_and_costed_entry(self):
        s, q = state(), snapshot()
        self.assertTrue(entry_signal(q, NOW)["qualified"])
        step(s, {"AAA": q}, NOW)
        self.assertEqual(s["cash"], 0)
        self.assertLess(s["equity"], 1000)
        self.assertGreater(s["position"]["entry_price"], q["price"])

    def test_stale_and_future_quote_never_enter(self):
        for stamp in (NOW-91, NOW+1):
            s, q = state(), snapshot()
            q["quote_time"] = stamp
            step(s, {"AAA": q}, NOW)
            self.assertIsNone(s["position"])

    def test_stale_quote_cannot_exit_even_at_target(self):
        s = state()
        step(s, {"AAA": snapshot()}, NOW)
        q = snapshot(200)
        q["quote_time"] = NOW-91
        step(s, {"AAA": q}, NOW)
        self.assertIsNotNone(s["position"])
        self.assertEqual(len(s["trades"]), 0)

    def test_target_compounds_actual_net_proceeds_and_no_same_poll_rebuy(self):
        s = state()
        step(s, {"AAA": snapshot()}, NOW)
        quantity = s["position"]["quantity"]
        q = snapshot(103)
        step(s, {"AAA": q}, NOW)
        self.assertIsNone(s["position"])
        self.assertEqual(s["trades"][-1]["reason"], "TARGET")
        self.assertAlmostEqual(s["cash"], quantity*103*.999)
        self.assertAlmostEqual(s["trades"][-1]["net_profit"], s["cash"]-1000)

    def test_stop_gap_uses_observed_price_not_ideal_stop(self):
        s = state()
        step(s, {"AAA": snapshot()}, NOW)
        step(s, {"AAA": snapshot(95)}, NOW)
        self.assertEqual(s["trades"][-1]["reason"], "STOP")
        self.assertLess(s["trades"][-1]["net_return_pct"], -5)
        step(s, {"AAA": snapshot()}, NOW)
        self.assertIsNone(s["position"])

    def test_timeout_requires_fresh_quote(self):
        s = state()
        step(s, {"AAA": snapshot()}, NOW)
        q = snapshot()
        q["quote_time"] = NOW+3601
        step(s, {"AAA": q}, NOW+3601)
        self.assertEqual(s["trades"][-1]["reason"], "TIMEOUT")

    def test_no_duplicate_buy_while_holding(self):
        s = state()
        step(s, {"AAA": snapshot()}, NOW)
        position = copy.deepcopy(s["position"])
        step(s, {"AAA": snapshot()}, NOW)
        self.assertEqual(s["position"], position)

    def test_bad_bars_and_chasing_rejected(self):
        q = snapshot()
        q["bars"].pop(-3)
        self.assertFalse(entry_signal(q, NOW)["qualified"])
        q = snapshot()
        q["bars"][-1]["time"] = NOW-1000
        self.assertFalse(entry_signal(q, NOW)["qualified"])
        self.assertFalse(entry_signal(snapshot(110), NOW)["qualified"])

    def test_session_and_currency_rejected(self):
        q = snapshot()
        q["currency"] = "GBP"
        self.assertIsNotNone(quote_reason(q, NOW))
        q = snapshot()
        self.assertIsNotNone(quote_reason(q, q["session_end"]))

    def test_closed_session_preserves_balance(self):
        s, q = state(), snapshot()
        step(s, {"AAA": q}, q["session_end"])
        self.assertEqual(s["status"], "Outside regular session; waiting")
        self.assertEqual(s["cash"], 1000)
        self.assertIsNone(s["position"])

    def test_reset_needs_new_valid_signal_and_cooldown(self):
        s = state()
        s["blocked"]["AAA"] = {"until": NOW+1800, "reset_after": NOW-600, "reset_seen": None}
        bad = snapshot()
        bad["bars"][-1]["volume"] = 20000
        step(s, {"AAA": bad}, NOW)
        self.assertEqual(s["blocked"]["AAA"]["reset_seen"], NOW-300)
        step(s, {"AAA": snapshot()}, NOW)
        self.assertIsNone(s["position"])
        q = snapshot()
        q["quote_time"] += 1801
        for bar in q["bars"]:
            bar["time"] += 1801
        step(s, {"AAA": q}, NOW+1801)
        self.assertIsNotNone(s["position"])

    def test_parser_ignores_partial_bars_and_nonfinite_prices(self):
        chart = {"meta": {"currentTradingPeriod": {"regular": {"start": NOW-1000, "end": NOW+1000}},
                          "regularMarketPrice": 100, "regularMarketTime": NOW, "currency": "USD"},
                 "timestamp": [NOW-900, NOW-600, NOW-100],
                 "indicators": {"quote": [{"open": [100]*3, "high": [101]*3,
                                           "low": [99]*3, "close": [100, float('nan'), 100],
                                           "volume": [20]*3}]}}
        parsed = parse_chart(chart, NOW)
        self.assertEqual(len(parsed["bars"]), 1)
        self.assertEqual(parsed["bars"][0]["time"], NOW-900)


class SessionSafetyTests(unittest.TestCase):
    def test_entry_cutoff_boundary_and_early_close(self):
        for remaining, expected in [(4501, True), (4500, False), (4499, False), (600, False)]:
            s, q = state(), snapshot()
            q["session_end"] = NOW + remaining
            step(s, {"AAA": q}, NOW)
            self.assertEqual(bool(s["position"]), expected)
        s, q = state(), snapshot()
        s["config"]["max_hold_minutes"] = 90
        q["session_end"] = NOW + 6000
        step(s, {"AAA": q}, NOW)
        self.assertIsNone(s["position"])

    def holding(self):
        s, q = state(), snapshot()
        step(s, {"AAA": q}, NOW)
        s["position"]["session_end"] = NOW + 600
        return s

    def test_session_close_uses_fresh_observed_price_and_no_rebuy(self):
        s, q = self.holding(), snapshot()
        q["session_end"] = NOW + 600
        q["quote_time"] = NOW + 300
        step(s, {"AAA": q}, NOW + 300)
        self.assertIsNone(s["position"])
        self.assertEqual(s["trades"][-1]["reason"], "SESSION_CLOSE")
        self.assertAlmostEqual(s["cash"], s["trades"][-1]["quantity"] * q["price"] * .999)
        self.assertIn("AAA", s["blocked"])

    def test_close_not_triggered_before_boundary(self):
        s, q = self.holding(), snapshot()
        q["session_end"] = NOW + 600
        q["quote_time"] = NOW + 299
        step(s, {"AAA": q}, NOW + 299)
        self.assertIsNotNone(s["position"])

    def test_missing_quote_overdue_survives_reload_and_retries(self):
        s = self.holding()
        step(s, {}, NOW + 301)
        self.assertIn("EXIT OVERDUE", s["status"])
        self.assertIsNotNone(s["position"])
        import json
        s = json.loads(json.dumps(s))
        q = snapshot()
        q["quote_time"] = NOW + 302
        step(s, {"AAA": q}, NOW + 302)
        self.assertEqual(s["trades"][-1]["reason"], "SESSION_CLOSE")
        self.assertNotIn("exit_pending", s)

    def test_closed_market_never_fabricates_fill_and_retries_next_session(self):
        s, q = self.holding(), snapshot()
        q["session_end"] = NOW + 600
        q["quote_time"] = NOW + 601
        step(s, {"AAA": q}, NOW + 601)
        self.assertIsNotNone(s["position"])
        self.assertIn("EXIT OVERDUE", s["status"])
        q["session_start"], q["session_end"] = NOW + 86400, NOW + 110000
        q["quote_time"] = NOW + 86401
        step(s, {"AAA": q}, NOW + 86401)
        self.assertIsNone(s["position"])
        self.assertEqual(s["trades"][-1]["reason"], "SESSION_CLOSE")

    def test_timeout_stale_quote_marks_overdue_without_balance_change(self):
        s, q = state(), snapshot()
        step(s, {"AAA": q}, NOW)
        old = copy.deepcopy(s["position"])
        step(s, {"AAA": q}, NOW + 3601)
        self.assertEqual(s["position"], old)
        self.assertEqual(s["cash"], 0)
        self.assertIn("EXIT OVERDUE (TIMEOUT)", s["status"])
        q["quote_time"] = NOW + 3602
        step(s, {"AAA": q}, NOW + 3602)
        self.assertEqual(s["trades"][-1]["reason"], "TIMEOUT")

if __name__ == "__main__":
    unittest.main()
