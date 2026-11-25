"""Check all public examples transcribed mechanically from the supplied README."""
from pathlib import Path
import re
import subprocess
import sys

readme = Path('input/README.md').read_text(encoding='utf-8')
blocks = re.findall(r'```\s*\n([0-9][^`]+)```', readme)
assert len(blocks) == 6
for i in range(0, 6, 2):
    inp, expected = blocks[i : i + 2]
    run = subprocess.run([sys.argv[1]], input=inp, text=True, capture_output=True, check=True)
    actual = run.stdout.strip()
    assert actual == expected.strip(), (actual, expected)
    print(f'Public example {i//2+1}: actual={actual}, expected={expected.strip()}, PASS')
