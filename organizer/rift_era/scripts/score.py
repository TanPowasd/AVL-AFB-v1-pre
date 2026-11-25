"""Official rift_era scoring_v2 entrypoint; see organizer/strategy_scoring.py and pipeline/scoring.py.

    python3 organizer/rift_era/scripts/score.py --bots my_bot.zip --seed-count 2 --ticks 200   # 快速练习（非正式）
    python3 organizer/rift_era/scripts/score.py                                                 # 正式：64 seed × 28,800 tick
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategy_scoring import main

if __name__ == "__main__":
    main("rift_era")
