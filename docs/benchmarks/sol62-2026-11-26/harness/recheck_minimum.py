#!/usr/bin/env python3
from pathlib import Path
import datetime, hashlib, json, subprocess, sys, time
import argparse
import tempfile
p=argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--data', type=Path, required=True, help='upstream official published case directory at pinned commit')
p.add_argument('--output', type=Path, required=True)
args=p.parse_args()
base=Path(__file__).resolve().parent
tmp=tempfile.TemporaryDirectory(prefix='afb-minimum-')
root=Path(tmp.name)
(root/'measured_candidate.py').write_bytes((base/'measured_candidate.py').read_bytes())
receipt=args.output
now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
safe=[sys.executable,str(base/'isolated.py'),'--root',str(root)]
start=now()
(root/'solution.cpp').write_bytes(args.source.read_bytes())
def sha(x): return hashlib.sha256(x).hexdigest()
preflight=subprocess.run(safe+['--time','45','--','g++','-std=gnu++20','-O2','-pipe','solution.cpp','-o','solution'],capture_output=True)
report={'started_utc':start,'source_sha256':sha((root/'solution.cpp').read_bytes()),'compile_returncode':preflight.returncode,'compiler_stdout':preflight.stdout.decode(errors='replace'),'compiler_stderr':preflight.stderr.decode(errors='replace'),'metric_note':'CPU and RSS of sandboxed candidate process from wait4 via Python RUSAGE_CHILDREN (fresh process per attempt); RSS is not enforced as 128 MiB resident, instead RLIMIT_AS=128 MiB and independently report RSS. Efficiency-weighted official score not calculated.','cases':[]}
for cfg in (json.loads((args.data/'manifest.json').read_text())['cases'] if preflight.returncode==0 else []):
    sg=args.data
    i=(sg/cfg['in']).read_bytes()
    expected=(sg/cfg['out']).read_bytes().split()
    row={k:v for k,v in cfg.items() if k not in ('in','out')}
    begin=time.perf_counter()
    run=subprocess.run(safe+['--time',str(cfg['time_limit_ms']/1000+0.75),'--','/usr/bin/python3','measured_candidate.py'],input=i,capture_output=True)
    row.update(wall_with_sandbox_ms=round((time.perf_counter()-begin)*1000,3),sandbox_exit=run.returncode,input_sha256=sha(i),actual_stdout_sha256=sha(run.stdout))
    if run.returncode: row.update(status='TLE' if run.returncode==124 else 'HARNESS_ERROR',diagnostic=run.stderr[:2048].decode(errors='replace'))
    else:
        row.update(json.loads(run.stderr.decode().strip().splitlines()[-1]))
        status=('OLE' if len(run.stdout)>1048576 else 'RE' if row['solver_exit'] else 'TLE' if row['solver_cpu_ms']>cfg['time_limit_ms'] else 'AC' if run.stdout.split()==expected else 'WA')
        row.update(status=status)
    report['cases'].append(row)
    receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
report['ended_utc']=now()
report['raw_points']=sum(c['points'] for c in report['cases'] if c['status']=='AC' and not c['is_sample'])
receipt.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('case summary',[(s,sum(c['status']==s for c in report['cases'])) for s in sorted({c['status'] for c in report['cases']})], 'raw points',report['raw_points'])
