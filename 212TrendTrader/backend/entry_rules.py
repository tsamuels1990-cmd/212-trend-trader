"""Persistent paper re-entry rules, independent of quote/network providers."""
from datetime import date, datetime, timezone


def signal_date(candles):
    dates = []
    for candle in candles:
        try:
            dates.append(date.fromisoformat(str(candle['time'])[:10]))
        except (KeyError, TypeError, ValueError):
            pass
    return max(dates).isoformat() if dates else ''


def freshness_reason(candles, today=None, max_age_days=5):
    latest = signal_date(candles)
    if not latest:
        return 'Missing daily signal date'
    age = ((today or datetime.now(timezone.utc).date()) - date.fromisoformat(latest)).days
    if age < 0:
        return 'Daily signal date is in the future'
    if age > max_age_days:
        return f'Daily signal is {age} calendar days old; refresh required'
    return None


def record_exit(state, position, price, reason, now=None):
    now = now or datetime.now(timezone.utc)
    symbol = position['symbol'].upper()
    trade = {
        'symbol': symbol, 'entry': position['entry'], 'exit': price,
        'quantity': position['quantity'], 'reason': reason,
        'exit_at': now.isoformat(),
        'return_pct': (price / position['entry'] - 1) * 100,
        'profit_target_pct': position.get('profit_target_pct'),
        'stop_loss_pct': position.get('stop_loss_pct'),
        'entry_signal_date': position.get('entry_signal_date'),
        'entry_score': position.get('entry_score'),
    }
    state.setdefault('trades', []).append(trade)
    state['trades'] = state['trades'][-1000:]
    if reason == 'STOP HIT':
        state.setdefault('reentry_blocks', {})[symbol] = {
            'exit_at': now.isoformat(), 'exit_date': now.date().isoformat(),
            'reset_signal_date': None,
        }


def reentry_reason(state, symbol, candles, analysis, cooldown_sessions=2):
    block = state.setdefault('reentry_blocks', {}).get(symbol.upper())
    if not block:
        return None
    latest = signal_date(candles)
    exit_date = block.get('exit_date', '9999-12-31')
    # Count distinct completed provider bars after the exit day; no guessed
    # weekend/holiday calendar. Exit-day data cannot reset the signal.
    sessions = set()
    for candle in candles:
        try:
            day = date.fromisoformat(str(candle['time'])[:10]).isoformat()
            if exit_date < day <= latest:
                sessions.add(day)
        except (KeyError, TypeError, ValueError):
            pass
    if latest > exit_date and analysis.get('qualified') is False:
        block['reset_signal_date'] = latest
    if len(sessions) < cooldown_sessions:
        return f'Stop-loss cooldown: {len(sessions)}/{cooldown_sessions} new daily sessions'
    reset = block.get('reset_signal_date')
    if not reset:
        return 'Waiting for the old qualifying signal to reset after the stop-loss'
    if latest <= reset or not analysis.get('qualified'):
        return 'Waiting for a newly qualified daily signal after the reset'
    return None
