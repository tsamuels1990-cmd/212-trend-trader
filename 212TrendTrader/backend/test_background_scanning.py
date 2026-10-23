import asyncio
import copy
import time
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
import server

class BackgroundScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = server.default_paper_state()
        self.state['cash'] = 0.0
        self.state['position'] = {'symbol':'TEST','entry':100,'quantity':2,'target':104,'stop':98}
        self.state_patch = patch.object(server, 'PAPER_STATE', self.state)
        self.state_patch.start()
        self.save_patch = patch.object(server, 'save_paper_state')
        self.save_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.save_patch.stop)

    async def test_scan_runs_with_open_position_and_caps_quote_requests(self):
        entries=[{'symbol':f'S{i}','analysis':{'qualified':True,'score':99-i,'sma20':90}} for i in range(40)]
        scan=AsyncMock(return_value={'qualified':entries,'valid_count':6600,'universe_count':6900})
        quote=AsyncMock(return_value={'price':100,'source':'live','quote_timestamp':int(time.time()),'quote_age_seconds':1})
        original=copy.deepcopy(self.state['position'])
        with patch.object(server,'scanner_scan',scan), patch.object(server,'market_quote',quote), patch.object(server,'paper_buy',AsyncMock()) as buy:
            await server.refresh_market_shortlist()
            self.assertEqual(quote.await_count,20)
            self.assertEqual(len(self.state['automation']['shortlist']),20)
            self.assertEqual(self.state['position'],original)
            buy.assert_not_awaited()
            scan.assert_awaited_once_with(validate_live=False,**server.scan_settings())
            await server.refresh_market_shortlist()
            self.assertEqual(scan.await_count,1)
            self.assertEqual(quote.await_count,40)
            self.state['automation']['min_score']=80
            await server.refresh_market_shortlist()
            self.assertEqual(scan.await_count,2)

    async def test_delayed_or_failed_quotes_cannot_enter(self):
        scan=AsyncMock(return_value={'qualified':[{'symbol':'ELME','analysis':{'sma20':90}}]})
        with patch.object(server,'scanner_scan',scan), patch.object(server,'market_quote',AsyncMock(return_value={'price':100,'source':'delayed','quote_age_seconds':121})):
            await server.refresh_market_shortlist()
        self.assertFalse(self.state['automation']['shortlist'][0]['entry_eligible'])
        self.state['automation']['shortlist'][0]['entry_eligible']=True
        with patch.object(server,'market_quote',AsyncMock(side_effect=server.HTTPException(502,'unavailable'))):
            await server.refresh_market_shortlist()
        self.assertFalse(self.state['automation']['shortlist'][0]['entry_eligible'])

    async def test_stale_quote_does_not_exit_but_fresh_quote_does(self):
        self.state['position']['entry_signal_date']='2026-10-12'
        with patch.object(server,'market_quote',AsyncMock(return_value={'price':105,'source':'delayed','quote_age_seconds':121,'quote_timestamp':1})):
            await server.paper_check()
        self.assertIsNotNone(self.state['position'])
        self.assertEqual(self.state['position']['quote_age_seconds'],121)
        self.assertIsNotNone(self.state['automation']['last_position_check_at'])
        with patch.object(server,'market_quote',AsyncMock(return_value={'price':105,'source':'live','quote_age_seconds':1,'quote_timestamp':int(time.time())})):
            await server.paper_check()
        self.assertIsNone(self.state['position'])
        self.assertEqual(self.state['cash'],210)
        self.assertIn('TARGET HIT',self.state['events'][-1])

    async def test_quote_error_records_monitoring_failure(self):
        with patch.object(server,'market_quote',AsyncMock(side_effect=server.HTTPException(502,'unavailable'))):
            with self.assertRaises(server.HTTPException):
                await server.paper_check()
        self.assertIsNotNone(self.state['position'])
        self.assertEqual(self.state['automation']['last_position_check_error'],'HTTPException')
        self.assertIn('paused',self.state['position']['monitoring_status'])

    async def test_no_duplicate_buy_while_position_open(self):
        self.state['automation']['shortlist']=[{'symbol':'OTHER','entry_eligible':True,'refreshed_at':datetime.utcnow().isoformat(),'settings':server.scan_settings()}]
        sleeps=0
        async def stop_after_one_cycle(_):
            nonlocal sleeps
            sleeps+=1
            if sleeps>=2: raise asyncio.CancelledError()
        with patch.object(server.asyncio,'sleep',stop_after_one_cycle), patch.object(server,'paper_check',AsyncMock()), patch.object(server,'paper_buy',AsyncMock()) as buy:
            with self.assertRaises(asyncio.CancelledError): await server.paper_automation_loop()
        buy.assert_not_awaited()

    async def test_flat_account_uses_shortlist_and_revalidates_entry(self):
        self.state['position']=None
        self.state['automation']['shortlist']=[{'symbol':'OTHER','entry_eligible':True,'refreshed_at':datetime.utcnow().isoformat(),'settings':server.scan_settings()}]
        sleeps=0
        async def stop_after_one_cycle(_):
            nonlocal sleeps
            sleeps+=1
            if sleeps>=2: raise asyncio.CancelledError()
        with patch.object(server.asyncio,'sleep',stop_after_one_cycle), patch.object(server,'paper_buy',AsyncMock(side_effect=server.HTTPException(409,'stale quote'))) as buy:
            with self.assertRaises(asyncio.CancelledError): await server.paper_automation_loop()
        buy.assert_awaited_once()
        self.assertIsNone(self.state['position'])
        self.assertIn('stale quote',self.state['events'][-1])

class QuoteFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_minute_old_quote_is_delayed(self):
        response=Mock(status_code=200)
        response.json.return_value={'chart':{'result':[{'meta':{'regularMarketPrice':100,'regularMarketTime':int(time.time())-180}}]}}
        client=AsyncMock()
        client.get.return_value=response
        client.__aenter__.return_value=client
        with patch.object(server.httpx,'AsyncClient',return_value=client):
            result=await server.market_quote('ELME')
        self.assertEqual(result['source'],'delayed')
        self.assertGreaterEqual(result['quote_age_seconds'],180)

if __name__=='__main__': unittest.main()
