"""Table escrow and saved state share one SQLite transaction. Money is integer cents."""
import json
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import tabletop

TYPES = {'aion': ('wallet_user', 'wallet_ai'),
         'connor': ('connor_wallet_user', 'connor_wallet_ai')}


@contextmanager
def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS tabletop_tables (kind TEXT PRIMARY KEY, state TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS tabletop_reports (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        db.execute('''CREATE TABLE IF NOT EXISTS bookkeeping (id TEXT PRIMARY KEY,
                   record_type TEXT NOT NULL, amount REAL DEFAULT 0, description TEXT DEFAULT '', created_at REAL NOT NULL)''')
        with db:
            yield db
    finally:
        db.close()


def _balances(db):
    return {p: max(0, int((Decimal(str(db.execute(
        'SELECT COALESCE(SUM(amount),0) FROM bookkeeping WHERE record_type IN (?,?)', kinds
    ).fetchone()[0])) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))) for p, kinds in TYPES.items()}


def balances(path):
    with connect(path) as db:
        return _balances(db)


def load(path, kind):
    with connect(path) as db:
        row = db.execute('SELECT state FROM tabletop_tables WHERE kind=?', (kind,)).fetchone()
        return json.loads(row[0]) if row else None


def _write(db, state):
    db.execute('INSERT OR REPLACE INTO tabletop_tables VALUES (?,?)',
               (state['kind'], json.dumps(state, ensure_ascii=False)))


def _entry(db, state, actor, amount, event):
    if actor == 'user':
        return  # The user's bankroll is a table limit, not a new persistent wallet.
    db.execute('INSERT OR IGNORE INTO bookkeeping (id,record_type,amount,description,created_at) VALUES (?,?,?,?,?)',
               (f"table_{state['id']}_{actor}_{event}", TYPES[actor][1], amount / 100,
                f"{tabletop.KINDS[state['kind']]} · {event} · 桌号 {state['id'][:8]}", time.time()))


def open_table(path, kind, players, wild, amount):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT state FROM tabletop_tables WHERE kind=?', (kind,)).fetchone()
        if old and (json.loads(old[0]).get('wallet') or {}).get('status') == 'open':
            raise ValueError('这桌金额仍在桌上，请先结束本局或收桌退回')
        available = _balances(db)
        user_limit = max(available.values())
        if amount and kind == 'texas-holdem' and amount > user_limit:
            raise ValueError(f'你的开桌额度不能超过两位伴侣中较高的钱包余额：¥{user_limit / 100:.2f}')
        initial = {p: (amount * 3 if kind == 'liars-dice' else amount if p == 'user' else min(amount, available[p]))
                   for p in players}
        if amount:
            if kind == 'liars-dice' and any(available[p] < amount * 3 for p in players if p != 'user'):
                raise ValueError('开局需暂存 3 轮约定金额，有伴侣余额不足，请调低每轮金额')
            if kind == 'texas-holdem' and any(n < 1000 for n in initial.values()):
                raise ValueError('每位玩家至少需要 ¥10.00 才能入座，请调低金额或补充钱包')
        state = tabletop.new_game(kind, players, wild, stacks=initial if amount and kind == 'texas-holdem' else None,
                                  blind=1000 if amount else 10, dice_stake=amount or 1)
        if amount:
            state['wallet'] = dict(status='open', stake=amount, initial=initial, user_limit=user_limit,
                                   net={p: 0 for p in players}, returned={})
            for p, n in initial.items():
                _entry(db, state, p, -n, '带入')
        _write(db, state)
        return state


def _settle(db, state, cancel=False):
    wallet = state.get('wallet')
    if not wallet or wallet['status'] != 'open':
        return
    if state['kind'] == 'texas-holdem':
        returned = dict(state['stacks'])
    elif state.get('dice_mode') == 'three_rounds':
        returned = {p: wallet['initial'][p] + state['dice_net'][p] for p in state['players']}
    elif cancel:
        returned = dict(wallet['initial'])
    else:
        returned = {p: sum(wallet['initial'].values()) if p == state['winner'] else 0 for p in state['players']}
    for p, n in returned.items():
        _entry(db, state, p, n, '退回' if cancel else '结算退回')
    wallet.update(status='refunded' if cancel else 'settled', returned=returned,
                  net={p: returned[p] - wallet['initial'][p] for p in state['players']})


def save(path, state, report=None):
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        if state.get('wallet') and state['kind'] == 'texas-holdem':
            state['wallet']['net'] = {p: state['stacks'][p] + (state['contributed'].get(p, 0) if state['phase'] == 'playing' else 0)
                                     - n for p, n in state['wallet']['initial'].items()}
        elif state.get('wallet') and state.get('dice_mode') == 'three_rounds':
            state['wallet']['net'] = state['dice_net'].copy()
        if state['phase'] == 'game_over':
            _settle(db, state)
        _write(db, state)
        if report:
            # Store the post-settlement snapshot, even if delivery to chat is temporarily unavailable.
            report['state'] = state
            db.execute('INSERT OR IGNORE INTO tabletop_reports VALUES (?,?)',
                       (report['id'], json.dumps(report, ensure_ascii=False)))


def cashout(path, state):
    if state.get('closed') or state['phase'] == 'game_over':
        return
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        # Void only the unfinished hand; previously completed rounds keep their net.
        if state['kind'] == 'texas-holdem' and state['phase'] == 'playing':
            for player, amount in state['contributed'].items():
                state['stacks'][player] += amount
            state['contributed'] = {p: 0 for p in state['players']}
            state['bets'] = {p: 0 for p in state['players']}
            state['current_bet'] = 0
        _settle(db, state, cancel=state['kind'] == 'liars-dice' and state.get('dice_mode') != 'three_rounds')
        state.update(closed=True, phase='cancelled', turn=None, pending=[],
                     end_reason='提前结束 · 未完成的这一轮作废', updated_at=time.time())
        state['revision'] += 1
        _write(db, state)


def pending_reports(path):
    with connect(path) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM tabletop_reports ORDER BY rowid')]


def update_report(path, report, delivered=False):
    with connect(path) as db:
        if delivered:
            db.execute('DELETE FROM tabletop_reports WHERE id=?', (report['id'],))
        else:
            db.execute('UPDATE tabletop_reports SET payload=? WHERE id=?',
                       (json.dumps(report, ensure_ascii=False), report['id']))
