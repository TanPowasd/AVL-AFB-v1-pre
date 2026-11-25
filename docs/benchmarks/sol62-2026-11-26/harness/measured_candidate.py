import json, resource, subprocess, sys, time, tempfile
resource.setrlimit(resource.RLIMIT_AS,(128*1024**2,128*1024**2))
resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024**2,2*1024**2))
t=time.perf_counter()
with tempfile.TemporaryFile() as out:
    r=subprocess.run(['./solution'],stdin=sys.stdin,stdout=out,stderr=subprocess.DEVNULL)
    out.seek(0)
    sys.stdout.buffer.write(out.read(1048577))
m=resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps(dict(solver_exit=r.returncode,solver_cpu_ms=round((m.ru_utime+m.ru_stime)*1000,3),solver_peak_rss_kib=m.ru_maxrss,solver_wall_ms=round((time.perf_counter()-t)*1000,3))),file=sys.stderr)
