"""Build an answer manifest from a FIXED_WITNESS executable and verify each optimal score vector."""

import argparse
import csv
import hashlib
from pathlib import Path
import subprocess

from fixed_certificate import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("batch", type=Path, help="Directory containing the generated .in files")
    parser.add_argument("reference", type=Path, help="Output CSV manifest")
    args = parser.parse_args()
    files = sorted(args.batch.glob("*.in"))
    if not files:
        parser.error("batch contains no .in files")
    with args.reference.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["batch", "id", "expected", "limit_s", "input_sha256"])
        for index, path in enumerate(files, 1):
            raw = path.read_bytes().replace(b"\r\n", b"\n")
            result = subprocess.run(
                [str(args.executable.resolve())], input=raw, capture_output=True, check=True, timeout=90
            )
            output = str(int(result.stdout))
            witness = path.with_suffix(".scores")
            path.with_suffix(".out").write_text(output + "\n", encoding="ascii", newline="\n")
            witness.write_bytes(result.stderr)
            length, linked_terms, _, _, exact = verify(path, witness)
            if exact != int(output):
                raise ValueError(f"Invalid score witness in {path}")
            writer.writerow([args.batch.name, path.stem, output, 1.5, hashlib.sha256(raw).hexdigest()])
            file.flush()
            if index % 10 == 0 or index == len(files):
                print(f"{index}/{len(files)} certificates checked ({length} moves, {linked_terms} linked terms)", flush=True)


if __name__ == "__main__":
    main()
