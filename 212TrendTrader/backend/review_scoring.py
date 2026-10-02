"""Offline chronological comparison. Never connects to providers or changes paper state.
Known limits: current cached universe (selection/survivorship bias), short history,
daily OHLC execution assumptions, fixed round-trip friction, no dividends/FX model.
This is an exploratory comparison, not proof of a profitable strategy.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from backtest_strategy import load_market, stats
from entry_rules import record_exit, reentry_reason
from strategy import analyse


def qualifies(candles, variant, target, stop):
    r = analyse(candles, min_score=85 if variant == 'score85' else 75,
                profit_target_pct=target, stop_loss_pct=stop)
    if variant == 'stop1.5ATR' and r.get('stop_noise_ratio', 0) < 1.5:
        r['qualified'] = False
    if variant == 'rsi55to65' and not 55 <= r.get('rsi14',0) <= 65:
        r['qualified'] = False
    return r


def simulate(market, variant, friction=.1, target=4, stop=2):
    days=defaultdict(dict);histories={s:[] for s in market}
    for symbol, candles in market.items():
        for c in candles:days[c['time']][symbol]=c
    state={'reentry_blocks':{},'trades':[]};position=None;trades=[]
    for day in sorted(days):
        today=days[day];was_open=position is not None
        analyses={s:qualifies(histories[s],variant,target,stop) for s in today if len(histories[s])>=51}
        reasons={s:reentry_reason(state,s,histories[s],r) for s,r in analyses.items()}
        if position is None and not was_open:
            candidates=[(r['score'],s) for s,r in analyses.items() if r['qualified']
                        and (variant=='baseline' or not reasons[s])
                        and (date.fromisoformat(day)-date.fromisoformat(histories[s][-1]['time'])).days<=5]
            if candidates:
                score,s=max(candidates);entry=float(today[s]['open'])
                # Reconfirm a bullish trend at the executable price.
                if variant=='baseline' or entry>analyses[s]['sma20']:
                    position={'symbol':s,'entry':entry,'quantity':1,'entry_date':day,
                              'entry_score':score,'target':entry*(1+target/100),
                              'stop':entry*(1-stop/100),'profit_target_pct':target,'stop_loss_pct':stop}
        if position and position['symbol'] in today:
            c=today[position['symbol']];op,hi,lo=map(float,(c['open'],c['high'],c['low']))
            reason=price=None
            # Adverse opening gaps fill at open. If both levels touched,
            # conservatively assume the stop was reached first.
            if op<=position['stop']:reason,price='STOP HIT',op
            elif op>=position['target']:reason,price='TARGET HIT',op
            elif lo<=position['stop']:reason,price='STOP HIT',position['stop']
            elif hi>=position['target']:reason,price='TARGET HIT',position['target']
            if reason:
                trades.append({**position,'exit_date':day,'outcome':reason,
                               'return_pct':(price/position['entry']-1)*100-friction})
                record_exit(state,position,price,reason,datetime.fromisoformat(day).replace(tzinfo=timezone.utc))
                position=None
        for s,c in today.items():histories[s].append(c)
    return trades,position


def summary(trades):
    n,w,lo,hi,av,comp,pf,dd=stats(trades)
    return dict(trades=n,win_pct=round(w,2),win_95pct_interval=[round(lo,2),round(hi,2)],
                average_trade_pct=round(av,3),closed_trade_compound_pct=round(comp,2),
                profit_factor=round(pf,2) if pf!=float('inf') else None,
                closed_trade_max_drawdown_pct=round(dd,2))


def run():
    market=load_market('market_cache');dates=sorted({c['time'] for cs in market.values() for c in cs})
    split=dates[int(len(dates)*.7)]
    report={'symbols':len(market),'start':dates[0],'end':dates[-1],'validation_start':split,
            'limitations':__doc__,'results':[]}
    for friction in (.1,.3):
        for variant in ('baseline','guarded','stop1.5ATR','rsi55to65','score85'):
            trades,position=simulate(market,variant,friction)
            # Exclude trades crossing the split from calibration to avoid leakage.
            calibration=[t for t in trades if t['exit_date']<split]
            validation=[t for t in trades if t['entry_date']>=split]
            row={'variant':variant,'round_trip_friction_pct':friction,
                 'calibration':summary(calibration),'validation':summary(validation),
                 'open_position_excluded':position,'completed_trades':trades}
            report['results'].append(row)
            print(variant,'friction',friction,'calibration',row['calibration'],'validation',row['validation'],
                  'open',position['symbol'] if position else None)
    Path('scoring_review_20261002.json').write_text(json.dumps(report,indent=2))
    print('Data:',len(market),'symbols',dates[0],dates[-1],'validation from',split)

if __name__=='__main__':run()
