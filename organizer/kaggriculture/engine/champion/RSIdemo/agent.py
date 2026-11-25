"""RSIdemo entry: isolated package with legacy and native-accelerated elite backends."""
import json
from pathlib import Path
import sys

if globals().get('__package__'):
    from .strategy import Strategy
else:
    # Supports Kaggle source loaders without __file__.
    def _location(): return _location.__code__.co_filename
    folder = Path(globals().get('__file__', _location())).resolve().parent
    if str(folder.parent) not in sys.path: sys.path.insert(0, str(folder.parent))
    from RSIdemo.strategy import Strategy


class RSIPolicy:
    def __init__(self, params=None):
        if params is None:
            params_path=Path(RSIPolicy.__init__.__code__.co_filename).resolve().with_name('params.json')
            params=json.loads(params_path.read_text(encoding='utf-8')) if params_path.exists() else {}
        self.params = dict(params or {})
        self.controller = self.make_controller()
        self.last_step = -1
        self.last_player = None

    @property
    def metrics(self):
        return self.controller.metrics

    @property
    def day_stats(self):
        return self.controller.day_stats

    def make_controller(self):
        if self.params.get('backend')=='elite':
            if globals().get('__package__'):
                from .elite_agent import ElitePolicy
            else:
                from RSIdemo.elite_agent import ElitePolicy
            return ElitePolicy(self.params)
        return Strategy(self.params)

    def act(self, observation, configuration=None):
        step, player = int(observation['step']), int(observation['player'])
        if step <= self.last_step or player != self.last_player:
            self.controller = self.make_controller()
        self.last_step, self.last_player = step, player
        return self.controller.act(observation, configuration)


_players = {}


def agent(observation, configuration=None):
    player = int(observation['player'])
    if player not in _players: _players[player] = RSIPolicy()
    return _players[player].act(observation, configuration)


def main():
    for line in sys.stdin:
        if line.strip():
            packet = json.loads(line)
            print(json.dumps(agent(packet.get('observation',packet),packet.get('configuration'))),flush=True)


if __name__ == '__main__':
    main()
