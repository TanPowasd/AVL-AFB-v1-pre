"""Official cache_duel scoring_v2 entrypoint; see organizer/strategy_scoring.py."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategy_scoring import main

if __name__ == "__main__":
    main("cache_duel")
