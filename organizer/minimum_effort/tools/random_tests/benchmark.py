"""Check outputs and measure each solution process serially."""

import argparse
import csv
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import zipfile

if os.name == "nt":
    from ctypes import wintypes

    class ProcessMemory(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in ("peak", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
                         "pagefile", "peak_pagefile")
        ]


def run(executable, raw, cutoff):
    start = time.perf_counter()
    with subprocess.Popen(
        [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        stopped = False
        try:
            output, _ = process.communicate(raw, timeout=cutoff)
        except subprocess.TimeoutExpired:
            stopped = True
            process.kill()
            output, _ = process.communicate()
        wall = time.perf_counter() - start
        cpu = peak = None
        if os.name == "nt":
            handle = wintypes.HANDLE(int(process._handle))
            times = [wintypes.FILETIME() for _ in range(4)]
            if ctypes.windll.kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                cpu = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 1e7
            memory = ProcessMemory()
            memory.size = ctypes.sizeof(memory)
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.size):
                peak = memory.peak
        return output.strip().decode("ascii", errors="replace"), wall, cpu, peak, stopped, process.returncode


def results(args, cases):
    columns = [
        "batch", "id", "round", "expected", "actual", "limit_s", "wall_s", "cpu_s",
        "peak_bytes", "status", "points", "earned",
    ]
    with args.csv.open("w", encoding="utf-8", newline="") as file:
        log = csv.DictWriter(file, fieldnames=columns)
        log.writeheader()
        for batch, name, raw, expected, limit, points in cases:
            for trial in range(1, args.rounds + 1):
                actual, wall, cpu, peak, stopped, code = run(args.executable, raw, args.cutoff)
                if stopped:
                    status = "KILLED"
                elif code:
                    status = "RE"
                elif peak is not None and peak > 128 * 1024**2:
                    status = "MLE"
                elif (cpu if cpu is not None else wall) > limit:
                    status = "TLE"
                elif actual != expected:
                    status = "WA"
                else:
                    status = "AC"
                log.writerow(dict(
                    batch=batch, id=name, round=trial, expected=expected, actual=actual,
                    limit_s=limit, wall_s=f"{wall:.6f}", cpu_s=cpu, peak_bytes=peak,
                    status=status, points=points, earned=points if status == "AC" else 0,
                ))
                file.flush()
                if status != "AC":
                    print(batch, name, trial, status, actual, round(wall, 3), cpu, flush=True)


def official_cases(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Invalid ZIP CRC")
        for line in archive.read("SHA256SUMS").decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"Mismatched SHA-256: {name}")
        config = json.loads(archive.read("subtasks.json"))
        for group in config["subtasks"]:
            for case in group["cases"]:
                name = case["id"]
                yield (
                    str(group["id"]), name, archive.read(f"{name}.in"),
                    archive.read(f"{name}.out").decode("ascii").strip(),
                    case["time_limit_ms"] / 1000, case["score"],
                )


def random_cases(root, reference):
    with reference.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            path = root / row["batch"] / (row["id"] + ".in")
            raw = path.read_bytes()
            if "input_sha256" in row and row["input_sha256"]:
                normalized = raw.replace(b"\r\n", b"\n")
                if hashlib.sha256(normalized).hexdigest() != row["input_sha256"]:
                    raise ValueError(f"Mismatched generated input: {path}")
            yield row["batch"], row["id"], raw, row["expected"], float(row["limit_s"]), 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("data", type=Path, help="Official ZIP, or root of generated batch directories")
    parser.add_argument("--reference", type=Path, help="Expected answers and limits for generated data")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--cutoff", type=float, default=30)
    args = parser.parse_args()
    if args.rounds < 1 or args.cutoff <= 0:
        parser.error("--rounds and --cutoff must be positive")
    args.executable = args.executable.resolve()
    cases = random_cases(args.data, args.reference) if args.reference else official_cases(args.data)
    results(args, cases)


if __name__ == "__main__":
    main()
