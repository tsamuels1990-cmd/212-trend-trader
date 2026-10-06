import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from entry_rules import freshness_reason, reentry_reason, record_exit


def bars(*days):
    return [{'time': d} for d in days]


class EntryRulesTest(unittest.TestCase):
    def setUp(self):
        self.state = {}
        record_exit(self.state, {'symbol': 'AAPL', 'entry': 100, 'quantity': 10},
                    98, 'STOP HIT', datetime(2026, 10, 2, tzinfo=timezone.utc))

    def test_cooldown_and_signal_reset(self):
        self.assertIsNotNone(reentry_reason(self.state, 'AAPL', bars('2026-10-02'), {'qualified': True}))
        self.assertIsNotNone(reentry_reason(self.state, 'AAPL', bars('2026-10-05','2026-10-06'), {'qualified': True}))
        self.assertIsNotNone(reentry_reason(self.state, 'AAPL', bars('2026-10-05','2026-10-06'), {'qualified': False}))
        self.assertIsNotNone(reentry_reason(self.state, 'AAPL', bars('2026-10-05','2026-10-06'), {'qualified': True}))
        self.assertIsNone(reentry_reason(self.state, 'AAPL', bars('2026-10-05','2026-10-06','2026-10-07'), {'qualified': True}))

    def test_restart_and_other_symbols(self):
        state = json.loads(json.dumps(self.state))
        self.assertIsNotNone(reentry_reason(state, 'aapl', bars('2026-10-02'), {'qualified': True}))
        self.assertIsNone(reentry_reason(state, 'MSFT', bars('2026-10-02'), {'qualified': True}))

    def test_target_exit_not_blocked(self):
        state = {}
        record_exit(state, {'symbol': 'MSFT', 'entry': 100, 'quantity': 1}, 104, 'TARGET HIT')
        self.assertFalse(state.get('reentry_blocks'))
        self.assertAlmostEqual(state['trades'][0]['return_pct'], 4)

    def test_stale_data(self):
        self.assertIsNotNone(freshness_reason(bars('2026-09-17'), date(2026,10,2)))
        self.assertIsNotNone(freshness_reason(bars('2026-10-03'), date(2026,10,2)))
        self.assertIsNone(freshness_reason(bars('2026-09-30'), date(2026,10,2)))


class PaperIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import server
        self.s = server
        self.old = (server.PAPER_STATE, server.PAPER_STATE_FILE, server.market_quote, server.market_candles, server.PAPER_MUTATION_LOCK)
        self.tmp = tempfile.TemporaryDirectory()
        server.PAPER_STATE_FILE = Path(self.tmp.name)/'state.json'
        server.PAPER_STATE = server.default_paper_state()
        server.PAPER_MUTATION_LOCK = asyncio.Lock()
        server.market_quote = AsyncMock(return_value={'price':98,'source':'live'})
        today = datetime.now(timezone.utc).date()
        candles = [{'time': (today-timedelta(days=60-i)).isoformat(),
                    'open':100,'high':102,'low':98,'close':100,'volume':100}
                   for i in range(60)]
        server.market_candles = AsyncMock(return_value={'candles':candles})
        server.PAPER_STATE['position'] = {'symbol':'AAPL','entry':100,'quantity':10,'stop':98,'target':104,'profit_target_pct':4,'stop_loss_pct':2}

    async def asyncTearDown(self):
        s = self.s
        s.PAPER_STATE,s.PAPER_STATE_FILE,s.market_quote,s.market_candles,s.PAPER_MUTATION_LOCK=self.old
        self.tmp.cleanup()

    async def test_concurrent_stop_checks_and_rebuy(self):
        await asyncio.gather(self.s.paper_check(), self.s.paper_check())
        self.assertEqual(len(self.s.PAPER_STATE['trades']), 1)
        self.assertEqual(self.s.PAPER_STATE['cash'], 980)
        with self.assertRaises(self.s.HTTPException) as error:
            await self.s.paper_buy('AAPL')
        self.assertEqual(error.exception.status_code,409)
        self.assertIsNone(self.s.PAPER_STATE['position'])
        self.assertIn('AAPL',self.s.load_paper_state()['reentry_blocks'])

    async def test_scanner_removes_blocked_candidate(self):
        from unittest.mock import patch
        record_exit(self.s.PAPER_STATE, self.s.PAPER_STATE['position'], 98, 'STOP HIT')
        analysis = {'qualified': True, 'score': 90, 'sma20': 97, 'blockers': []}
        with patch.object(self.s, 'analyse', side_effect=lambda *a, **k: dict(analysis, blockers=[])):
            result = await self.s.scanner_scan(symbols='AAPL,MSFT')
        self.assertEqual([r['symbol'] for r in result['qualified']], ['MSFT'])
        self.assertIn('cooldown', result['results'][0]['analysis']['reason'])

    async def test_scanner_skips_stale_top_and_keeps_fresh_candidate(self):
        from unittest.mock import patch
        self.s.market_quote.side_effect = [
            {'price': 102, 'source': 'delayed', 'quote_age_seconds': 86400},
            {'price': 102, 'source': 'live'}]
        analysis = {'qualified': True, 'score': 90, 'sma20': 100}
        with patch.object(self.s, 'analyse', side_effect=lambda *a, **k: dict(analysis)):
            result = await self.s.scanner_scan(symbols='ALBY,MSFT')
        self.assertEqual([r['symbol'] for r in result['qualified']], ['MSFT'])
        self.assertIn('quote age', result['results'][0]['analysis']['reason'])
        self.assertEqual(result['qualified'][0]['analysis']['price'], 102)
        self.assertEqual(self.s.PAPER_STATE['trades'], [])

    async def test_scanner_rejects_live_price_below_sma(self):
        from unittest.mock import patch
        self.s.market_quote.return_value = {'price': 98, 'source': 'live'}
        with patch.object(self.s, 'analyse', return_value={'qualified': True, 'score': 90, 'sma20': 100}):
            result = await self.s.scanner_scan(symbols='MSFT')
        self.assertEqual(result['qualified_count'], 0)
        self.assertIn('bullish', result['results'][0]['analysis']['reason'])

    async def test_delayed_quote_cannot_exit(self):
        self.s.market_quote.return_value = {'price': 90, 'source': 'delayed'}
        await self.s.paper_check()
        self.assertIsNotNone(self.s.PAPER_STATE['position'])
        self.assertEqual(self.s.PAPER_STATE['trades'], [])
        self.assertEqual(self.s.PAPER_STATE['position']['current_price'], 90)

    async def test_timestamped_close_retained_for_display(self):
        from unittest.mock import patch, MagicMock
        from datetime import datetime, timezone
        original_quote = self.old[2]
        now = datetime.now(timezone.utc).timestamp()
        for age, expected in [(60, 'live'), (3600, 'delayed')]:
            response = MagicMock(status_code=200)
            response.json.return_value = {'chart': {'result': [{'meta': {
                'regularMarketPrice': 333.69,
                'regularMarketTime': int(now - age),
            }}]}}
            client = AsyncMock()
            client.get.return_value = response
            with patch.object(self.s.httpx, 'AsyncClient') as factory:
                factory.return_value.__aenter__.return_value = client
                quote = await original_quote('AAPL')
            self.assertEqual(quote['source'], expected)
            self.assertEqual(quote['price'], 333.69)
            self.assertEqual(quote['provider'], 'yahoo_intraday')

    async def test_delayed_quote_cannot_enter(self):
        self.s.PAPER_STATE['position']=None
        self.s.market_quote.return_value={'price':102,'source':'cached'}
        with self.assertRaises(self.s.HTTPException) as error:
            await self.s.paper_buy('MSFT')
        self.assertIn('live quote',error.exception.detail)


if __name__ == '__main__':
    unittest.main()
