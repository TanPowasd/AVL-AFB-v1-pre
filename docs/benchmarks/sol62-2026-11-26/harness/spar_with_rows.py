"""Record per-game result objects from upstream public spar; does not change grading."""
import json
from pathlib import Path
import time
import spar
real=spar.arena.play_game
real_run=spar.run_one
output=Path('evidence_games.jsonl')
output.unlink(missing_ok=True)
def run_one(mine_path, opp_name, seeds, cfg_all):
    seat_count={}
    def trace(policies,seed,*a,**kw):
        seat=seat_count.get(seed,0)
        seat_count[seed]=seat+1
        t=time.monotonic()
        result=real(policies,seed,*a,**kw)
        rec={'opponent':opp_name,'entrant_seat':seat,'wall_s':time.monotonic()-t,'game':result}
        with output.open('a',encoding='utf8') as f: f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        return result
    spar.arena.play_game=trace
    try: return real_run(mine_path,opp_name,seeds,cfg_all)
    finally: spar.arena.play_game=real
spar.run_one=run_one
spar.main()
