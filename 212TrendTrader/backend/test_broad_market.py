import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock
from broad_market import parse_history, warm_history_batch, HISTORY_DAILY_LIMIT

class HistoryTests(unittest.TestCase):
    def test_partial_daily_bar_excluded_and_nulls_skipped(self):
        stamps = [1700000000+i*86400 for i in range(60)]
        chart = {'meta': {'exchangeTimezoneName': 'America/New_York',
                    'currentTradingPeriod': {'regular': {'start': stamps[-1], 'end': stamps[-1]+23400}}},
                 'timestamp': stamps,
                 'indicators': {'quote': [{k: [100]*60 for k in ('open','high','low','close','volume')}]}}
        chart['indicators']['quote'][0]['close'][3] = None
        rows = parse_history(chart, now=stamps[-1]+60)
        self.assertEqual(len(rows), 58)
        self.assertLess(rows[-1]['time'], '2024-01-12')

class WarmTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_rotation_and_failed_symbol_backoff(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as folder:
            state = {'date':datetime.now(timezone.utc).date().isoformat(),
                     'requests_today':HISTORY_DAILY_LIMIT-1}
            save = lambda s: None
            with patch('broad_market.yahoo_history', AsyncMock(return_value={'candles': [], 'symbol':'AAPL'})) as fetch:
                await warm_history_batch(['AAPL','MSFT'],Path(folder),state,save)
                self.assertEqual(fetch.await_count, 1)
                self.assertEqual(state['requests_today'], HISTORY_DAILY_LIMIT)
                self.assertEqual(state['cursor'], 1)
                await warm_history_batch(['AAPL','MSFT'],Path(folder),state,save)
                self.assertEqual(fetch.await_count, 1)

    async def test_failed_fetch_does_not_overwrite_valid_cache(self):
        from datetime import datetime, timezone
        from fastapi import HTTPException
        import os, time
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'AAPL_compact.json'
            path.write_text('{"keep": true}')
            os.utime(path,(time.time()-90000,time.time()-90000))
            state={'date':datetime.now(timezone.utc).date().isoformat()}
            with patch('broad_market.yahoo_history', AsyncMock(side_effect=HTTPException(502,'No history'))):
                await warm_history_batch(['AAPL'],Path(folder),state,lambda s:None)
                await warm_history_batch(['AAPL'],Path(folder),state,lambda s:None)
            self.assertEqual(json.loads(path.read_text()), {'keep':True})
            self.assertEqual(state['requests_today'],1)

if __name__=='__main__':
    unittest.main()
