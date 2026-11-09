import copy
import math
import unittest
from datetime import datetime,timezone

from ai_product import Policy, features, exit_trade, replay
from ai_forward import initial_state, step, paper_blockers, fresh

START=int(datetime(2026,11,10,14,30,tzinfo=timezone.utc).timestamp())


def bars(n=78):
    return [(START+i*300,100.,100.1,99.9,100.,1000.) for i in range(n)]


class ReplayTests(unittest.TestCase):
    def test_costs_and_timeout(self):
        result=exit_trade(bars(12),Policy())
        self.assertEqual(result['reason'],'TIMEOUT')
        self.assertAlmostEqual(result['net_return'],.999/1.001-1)
        self.assertEqual(result['exit_time'],START+3600)

    def test_stop_first_when_both_barriers_hit(self):
        b=bars(12)
        b[1]=(b[1][0],100,110,90,100,1000)
        r=exit_trade(b,Policy())
        self.assertEqual(r['reason'],'STOP_AMBIGUOUS')
        self.assertAlmostEqual(r['net_return'],-.0075)

    def test_gap_stop_exits_at_actual_open(self):
        b=bars(12)
        b[1]=(b[1][0],90,91,89,90,1000)
        r=exit_trade(b,Policy())
        self.assertEqual(r['reason'],'STOP_GAP')
        self.assertLess(r['net_return'],-.09)

    def test_missing_data_is_conservative(self):
        b=bars(12)
        del b[1]
        r=exit_trade(b,Policy())
        self.assertEqual(r['reason'],'DATA_GAP_CONSERVATIVE')
        self.assertLessEqual(r['net_return'],-.0075+1e-10)

    def test_features_do_not_use_future(self):
        b=bars()
        x=features(b[:21],START)
        b[21]=(b[21][0],500,500,500,500,1000)
        self.assertEqual(x,features(b[:21],START))
        self.assertIsNone(features(b[:20],START))
        bad=b[:21];del bad[2]
        self.assertIsNone(features(bad,START))

    def test_one_position_and_daily_trade_limit(self):
        b=[(t,o,h,l,100+i*.01,v) for i,(t,o,h,l,c,v) in enumerate(bars())]
        # Consistent OHLC bars for a mild rising signal.
        b=[(t,c,c+.1,c-.1,c,v) for t,o,h,l,c,v in b]
        report=replay({('2026-11-10','AAPL'):b,('2026-11-10','MSFT'):b},
                      {'2026-11-10'},None,Policy(max_trades=2),baseline=True)
        self.assertEqual(report['trades'],2)
        t=report['trade_log']
        self.assertGreater(t[1]['entry_time'],t[0]['exit_time'])
        for trade in t:
            self.assertLess(abs(trade['profit']),10)

    def test_daily_loss_lock_blocks_later_signals(self):
        b=bars()
        # Past return positive but the next bar repeatedly crosses the stop.
        b=[(t,100+i*.02,101+i*.02,98,100+i*.02,v) for i,(t,o,h,l,c,v) in enumerate(b)]
        r=replay({('2026-11-10','AAPL'):b},{'2026-11-10'},None,
                 Policy(allocation=1,daily_loss=.005,max_trades=20,cooldown=0),True)
        self.assertEqual(r['trades'],1)

    def test_invalid_policy_fails(self):
        for kwargs in [{'allocation':2},{'cost':float('nan')},{'max_hold':5},{'seed':0}]:
            with self.assertRaises(ValueError): Policy(**kwargs)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.policy=Policy()
        self.now=START+7200
        self.manifest={'model_sha256':'test','mode':'research','usable_sessions':60,
                       'latest_evaluation_day':'2026-11-09','blockers':[
                           'Forward shadow observation and execution review still required']}
        self.state=initial_state('test',self.policy)
        self.state['forward_sessions']=[str(i) for i in range(10)]
        self.state['prediction_count']=100
        self.control={'entries_enabled':True,'approved_model_sha256':'test','halt':False}
        self.quote={'currency':'USD','price':100.,'quote_time':self.now,'session_start':START,
                    'session_end':START+23400}
        self.pred=[{'symbol':'AAPL','signal_time':self.now-300,'target_probability':.9,'expected_return':.003}]

    def advance(self,mode='paper',pred=None):
        step(self.state,{'AAPL':self.quote},self.pred if pred is None else pred,
             self.now,self.policy,mode,self.control,self.manifest)

    def test_shadow_never_opens(self):
        self.advance('shadow')
        self.assertIsNone(self.state['position'])
        self.assertEqual(self.state['cash'],1000)

    def test_paper_entry_allocation_and_stop(self):
        self.advance()
        self.assertAlmostEqual(self.state['position']['invested'],250)
        self.assertEqual(self.state['cash'],750)
        self.quote['price']=98
        self.advance(pred=[])
        self.assertIsNone(self.state['position'])
        self.assertEqual(self.state['trades'][0]['reason'],'STOP')
        self.assertLess(self.state['equity'],1000)
        self.assertIn('AAPL',self.state['blocked'])

    def test_stale_quotes_block_entry_and_exit(self):
        self.quote['quote_time']=self.now-91
        self.advance()
        self.assertIsNone(self.state['position'])
        self.quote['quote_time']=self.now
        self.advance()
        self.quote['quote_time']=self.now-91
        self.quote['price']=90
        self.advance(pred=[])
        self.assertIsNotNone(self.state['position'])
        self.assertIn('paused',self.state['status'])

    def test_kill_switch_exits_then_prevents_entry(self):
        self.advance()
        self.control['halt']=True
        self.advance()
        self.assertEqual(self.state['trades'][0]['reason'],'KILL_SWITCH')
        self.advance()
        self.assertIsNone(self.state['position'])

    def test_development_and_wrong_hash_block_paper(self):
        self.manifest['mode']='development'
        self.advance()
        self.assertIsNone(self.state['position'])
        self.manifest['mode']='research'
        self.control['approved_model_sha256']='wrong'
        self.advance()
        self.assertIsNone(self.state['position'])

    def test_past_signal_and_history_overlap_cannot_fill(self):
        self.pred[0]['signal_time']=self.now-1000
        self.advance()
        self.assertIsNone(self.state['position'])
        self.pred[0]['signal_time']=self.now-300
        self.manifest['latest_evaluation_day']='2026-11-10'
        self.advance()
        self.assertIsNone(self.state['position'])

    def test_daily_loss_lock_is_sticky(self):
        self.state['daily']['2026-11-10']={'start':1000,'trades':1,'locked':False}
        self.state['cash']=980
        self.advance()
        self.assertIsNone(self.state['position'])
        self.assertTrue(self.state['daily']['2026-11-10']['locked'])
        self.state['cash']=1000
        self.advance()
        self.assertIsNone(self.state['position'])

    def test_cooldown_and_trade_limit(self):
        self.state['blocked']['AAPL']=self.now+1
        self.advance()
        self.assertIsNone(self.state['position'])
        self.state['blocked']={}
        self.state['daily']['2026-11-10']={'start':1000,'trades':5,'locked':False}
        self.advance()
        self.assertIsNone(self.state['position'])

    def test_outside_session_and_future_quote(self):
        self.assertFalse(fresh(self.quote,START-1))
        self.quote['quote_time']=self.now+1
        self.assertFalse(fresh(self.quote,self.now))


if __name__=='__main__': unittest.main()
