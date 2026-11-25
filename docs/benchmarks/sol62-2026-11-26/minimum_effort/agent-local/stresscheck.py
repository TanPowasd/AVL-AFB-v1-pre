"""Reproduce the 10,000-action fixed-shape stress test."""
import subprocess
import sys

L = 10000
data = [
    '2 3 1',
    '2 1',
    '2 999999998 999990000',
    '2 999999997 999989999',
    '1 1 999989998',
    str(L),
    *[f'1 {L+2-j}' for j in range(L-1)],
    '1 2',
]
mod = 10**9+7
# Highest permissible staging scores minimize every affected cost independently.
# The initial charge has exponent 999999997; all later charges have exponent 10000.
expected = (pow(2, 999999997, mod) + (L-1)*pow(2, 10000, mod)) % mod
run = subprocess.run([sys.argv[1]], input='\n'.join(data)+'\n', text=True,
                     capture_output=True, check=True)
assert int(run.stdout) == expected
print(f'Stress case: actual={run.stdout.strip()}, expected={expected}, PASS')
