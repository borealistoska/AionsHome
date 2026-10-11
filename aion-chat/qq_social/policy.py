"""Small per-bot/per-group budget, including direct replies and bot loops."""
from collections import defaultdict, deque


class ReplyPolicy:
    def __init__(self):
        self.history = defaultdict(deque)
        self.bot_turns = defaultdict(int)
        self.last_candidate = {}
        self.last_started = {}

    def human(self, key):
        self.bot_turns[key] = 0

    def allow(self, key, *, direct, is_bot, settings, now, include_candidate=True):
        history = self.history[key]
        while history and history[0][0] < now - 3600:
            history.popleft()
        if len(history) >= settings.get('total_hourly_limit', 60):
            return False
        if history and now - history[-1][0] < 3:
            return False
        if is_bot and self.bot_turns[key] >= 3:
            return False
        if direct:
            return True
        if settings.get('mode') != 'natural':
            return False
        spontaneous = [at for at, was_direct in history if not was_direct]
        if len(spontaneous) >= settings.get('hourly_limit', 6):
            return False
        latest = max(spontaneous[-1] if spontaneous else 0, self.last_started.get(key,0),
                     self.last_candidate.get(key, 0) if include_candidate else 0)
        return not latest or now - latest >= settings.get('interval_seconds', 120)

    def candidate(self, key, now):
        self.last_candidate[key] = now

    def started(self, key, *, direct, now):
        if not direct:
            self.last_started[key] = now

    def sent(self, key, *, direct, is_bot, now):
        self.history[key].append((now, direct))
        self.bot_turns[key] = self.bot_turns[key] + 1 if is_bot else 0
