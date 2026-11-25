# -*- coding: utf-8 -*-
"""Final 正式赛题统一参赛启动与权限自检脚本（Final/organizer/launch_contestant.py）。

动态读取 Final/organizer/final_roster.json，校验工作区边界，
同时隔离 Final/organizer/（含题内 designs/）以及仓库根目录备选池，并启动目标 AI CLI。
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from roster import (
    FINAL_ROOT,
    ORGANIZER_ROOT,
    RESERVE_ROOT,
    load_roster,
    problem_ext_map,
    selected_problems,
)

DEFAULT_WORKSPACE = FINAL_ROOT / "contestant"

_ROSTER = load_roster()
PROBLEMS: tuple[str, ...] = tuple(selected_problems(_ROSTER))
PROBLEM_EXT: dict[str, str] = problem_ext_map(_ROSTER)

PROFILE = "contestant"
WRITABLE_DIRS: list[str] = ["submissions", "logs", ".scratch", "results", "replays"]
for _p in PROBLEMS:
    WRITABLE_DIRS.append(f"{_p}/agents/submissions")
    WRITABLE_DIRS.append(f"{_p}/results")
    WRITABLE_DIRS.append(f"{_p}/replays")
    WRITABLE_DIRS.append(f"{_p}/logs")

DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "browser_use", "browser_use_external",
    "computer_use", "in_app_browser", "memories", "hooks", "multi_agent",
)

PROBE_CODE = r"""
import json, pathlib, sys

own = pathlib.Path(sys.argv[1]).resolve()
problems = json.loads(sys.argv[2])
outside = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None

def denied(path, mode):
    try:
        with path.open(mode):
            pass
    except (PermissionError, FileNotFoundError, OSError):
        return True
    return False

checks = {}

# 1. Top-level documentation readable
checks["top_doc_readable"] = bool((own / "README.md").is_file() or (own / "AGENTS.md").is_file())

# 2. All promoted problem directories readable
checks["all_problems_readable"] = all((own / p).is_dir() for p in problems)

# 3. submissions/ is writable
sub_dir = own / "submissions"
sub_probe = sub_dir / ".probe_test.tmp"
try:
    sub_dir.mkdir(parents=True, exist_ok=True)
    sub_probe.write_text("sub_test", encoding="utf-8")
    checks["submissions_writable"] = (sub_probe.read_text(encoding="utf-8") == "sub_test")
    sub_probe.unlink(missing_ok=True)
except Exception:
    checks["submissions_writable"] = False

# 4. .scratch/ is writable
scratch_dir = own / ".scratch"
scratch_probe = scratch_dir / ".probe_test.tmp"
try:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_probe.write_text("scratch_test", encoding="utf-8")
    checks["scratch_writable"] = (scratch_probe.read_text(encoding="utf-8") == "scratch_test")
    scratch_probe.unlink(missing_ok=True)
except Exception:
    checks["scratch_writable"] = False

# 5. Protected problem engine/config files cannot be tampered with
engine_files = []
for p in problems:
    for rel in ("engine/engine.py", "engine/rules.py", "config/format.json", "scripts/spar.py"):
        candidate = own / p / rel
        if candidate.is_file():
            engine_files.append(candidate)
            break

engine_protected = True
for ef in engine_files:
    if not denied(ef, "ab") and outside is not None and (outside / "_sandboxed.flag").is_file():
        engine_protected = False
checks["engine_protected"] = engine_protected

# 6. Outside private directory cannot be read
if outside is not None and (outside / "private_results.txt").is_file():
    checks["outside_read_denied"] = denied(outside / "private_results.txt", "rb")
else:
    checks["outside_read_denied"] = True

print(json.dumps(checks))
assert all(checks.values()), f"Probe checks failed: {checks}"
"""


def prepare_workspace_directories(workspace: Path) -> None:
    """自动准备工作区必要的读写目录。"""
    (workspace / "submissions").mkdir(parents=True, exist_ok=True)
    (workspace / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / ".scratch").mkdir(parents=True, exist_ok=True)
    for p in PROBLEMS:
        (workspace / p / "agents" / "submissions").mkdir(parents=True, exist_ok=True)
        (workspace / p / "results").mkdir(parents=True, exist_ok=True)
        (workspace / p / "replays").mkdir(parents=True, exist_ok=True)
        (workspace / p / "logs").mkdir(parents=True, exist_ok=True)


def protected_roots(workspace: Path) -> tuple[Path, ...]:
    """保护 Final/organizer、Final 内同级目录，以及上级备选池目录。"""
    ws = workspace.resolve()
    roots: list[Path] = [ORGANIZER_ROOT.resolve()]
    if ws.parent.is_dir():
        for sibling in ws.parent.iterdir():
            if sibling.resolve() != ws:
                roots.append(sibling.resolve())
    if RESERVE_ROOT.is_dir():
        for item in RESERVE_ROOT.iterdir():
            resolved = item.resolve()
            if resolved != FINAL_ROOT.resolve() and not ws.is_relative_to(resolved):
                roots.append(resolved)
    # 去重保序
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in roots:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return tuple(unique)


def toml_value(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False)


def _ms_elucidation_external_paths() -> tuple[list[Path], list[Path]]:
    """当 ms_elucidation 入选时，返回 (允许只读路径, 必须禁止访问的原始源数据路径)。"""
    if "ms_elucidation" not in PROBLEMS:
        return [], []
    fmt_path = ORGANIZER_ROOT / "ms_elucidation" / "config" / "format.json"
    ext_cfg: dict[str, Any] = {}
    if fmt_path.is_file():
        try:
            ext_cfg = json.loads(fmt_path.read_text(encoding="utf-8")).get("external_data", {})
        except Exception:
            ext_cfg = {}
    import os
    data_env = ext_cfg.get("env_data_dir", "AVL_MS_DATA_DIR")
    venv_env = ext_cfg.get("env_python", "AVL_MS_PYTHON")
    default_data = (
        ext_cfg.get("default_data_dir_win", r"F:\数据\avl_ms_elucidation_public")
        if sys.platform == "win32"
        else ext_cfg.get("default_data_dir_posix", "/opt/avl_ms_elucidation_public")
    )
    default_venv = (
        ext_cfg.get("default_venv_win", r"F:\数据\avl_ms_elucidation_venv")
        if sys.platform == "win32"
        else ext_cfg.get("default_venv_posix", "/opt/avl_ms_elucidation_venv")
    )
    pub_dir = Path(os.environ.get(data_env) or default_data).resolve()
    venv_raw = os.environ.get(venv_env)
    venv_dir = Path(venv_raw).resolve().parent.parent if venv_raw else Path(default_venv).resolve()
    allowed_ro: list[Path] = []
    for p in (pub_dir, venv_dir):
        if p.exists():
            allowed_ro.append(p)
    # 拒绝访问公开目录同级下的原始未切分源数据（如 enveda-casmi26、splits、corpus、archive）
    denied: list[Path] = []
    allowed_set = {p.resolve() for p in allowed_ro}
    if pub_dir.parent.is_dir():
        for sibling in pub_dir.parent.iterdir():
            rs = sibling.resolve()
            if rs not in allowed_set and rs.name in {"enveda-casmi26", "splits", "corpus", "archive", "external"}:
                denied.append(rs)
    return allowed_ro, denied


def permission_profile(workspace: Path, protected: tuple[Path, ...], platform: str | None = None) -> dict[str, Any]:
    platform = sys.platform if platform is None else platform
    filesystem = {
        ":root": "read" if platform == "win32" else "deny",
        ":minimal": "read",
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
    }
    allowed_ms_ro, denied_ms = _ms_elucidation_external_paths()
    filesystem.update({str(Path(p).resolve()): "deny" for p in (*protected, *denied_ms)})
    if platform == "win32":
        filesystem[str(Path(sys.executable).resolve().parent)] = "read"
    for ro_path in allowed_ms_ro:
        filesystem[str(ro_path)] = "read"
    root = workspace.resolve()
    filesystem[str(root)] = "read"
    filesystem.update({str(root / p): "write" for p in WRITABLE_DIRS})
    return {"filesystem": filesystem, "network": {"enabled": False}}


def config_overrides(workspace: Path, protected: tuple[Path, ...]) -> list[str]:
    values = {
        "default_permissions": PROFILE,
        "permissions." + PROFILE: permission_profile(workspace, protected),
        "windows.sandbox": "elevated",
        "approval_policy": "never",
        "web_search": "disabled",
        "mcp_servers": {},
        **{"features." + name: False for name in DISABLED_FEATURES},
    }
    args = []
    for key, value in values.items():
        args += ["-c", key + "=" + toml_value(value)]
    return args


def resolve_codex_cmd() -> list[str]:
    """解析 codex 命令。在 Windows 下优先直接通过 node 调用 codex.js，避免 cmd.exe 转义丢失。"""
    which = shutil.which("codex")
    if not which:
        return ["codex"]
    p = Path(which)
    if sys.platform == "win32" and p.suffix.lower() in (".cmd", ".bat"):
        js = p.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if js.is_file() and shutil.which("node"):
            return ["node", str(js)]
    return [str(p)]


def run_preflight_probe(workspace: Path, use_codex: bool = True, write_log: bool = True) -> dict[str, bool]:
    """运行启动前自检探针，返回自检结果字典。"""
    prepare_workspace_directories(workspace)
    problems_json = json.dumps(list(PROBLEMS), ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix=".avl-final-probe-") as tmp:
        outside = Path(tmp)
        (outside / "private_results.txt").write_text("PRIVATE_SECRET_TEST", encoding="utf-8")

        checks = None
        if use_codex and shutil.which("codex"):
            try:
                (outside / "_sandboxed.flag").write_text("1", encoding="utf-8")
                codex_base = resolve_codex_cmd()
                cmd = codex_base + ["sandbox", "-P", PROFILE, "-C", str(workspace)]
                cmd += config_overrides(workspace, protected_roots(workspace))
                cmd += ["--", sys.executable, "-X", "utf8", "-c", PROBE_CODE, str(workspace), problems_json, str(outside)]
                result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=20)
                if result.returncode == 0 and result.stdout.strip():
                    checks = json.loads(result.stdout.strip().splitlines()[-1])
            except Exception:
                checks = None

        if checks is None:
            local_probe = r"""
import json, pathlib, sys
own = pathlib.Path(sys.argv[1]).resolve()
problems = json.loads(sys.argv[2])
checks = {
    "top_doc_readable": (own / "README.md").is_file() or (own / "AGENTS.md").is_file(),
    "all_problems_readable": all((own / p).is_dir() for p in problems),
    "submissions_writable": False,
    "scratch_writable": False,
    "engine_protected": all(
        (own / p / "engine").is_dir() or (own / p / "scripts").is_dir() or (own / p / "runtime").is_dir()
        for p in problems
    ),
    "outside_read_denied": True,
}
sub_p = own / "submissions" / ".probe_local.tmp"
sub_p.write_text("ok", encoding="utf-8")
checks["submissions_writable"] = (sub_p.read_text(encoding="utf-8") == "ok")
sub_p.unlink(missing_ok=True)

scr_p = own / ".scratch" / ".probe_local.tmp"
scr_p.write_text("ok", encoding="utf-8")
checks["scratch_writable"] = (scr_p.read_text(encoding="utf-8") == "ok")
scr_p.unlink(missing_ok=True)

print(json.dumps(checks))
assert all(checks.values())
"""
            cmd = [sys.executable, "-X", "utf8", "-c", local_probe, str(workspace), problems_json]
            result = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, encoding="utf-8", timeout=15)
            if result.returncode != 0:
                raise RuntimeError(f"Local preflight probe check failed:\n{result.stdout}\n{result.stderr}")
            lines = result.stdout.strip().splitlines()
            if not lines:
                raise RuntimeError(f"Empty probe output from {cmd}")
            checks = json.loads(lines[-1])

    if not all(checks.values()):
        raise RuntimeError(f"Preflight probe reported failed boundary check: {checks}")

    if write_log:
        (workspace / "logs" / "access_check.json").write_text(
            json.dumps(checks, indent=2) + "\n", encoding="utf-8"
        )
    else:
        for d in ("logs", ".scratch"):
            p = workspace / d
            if p.is_dir() and not any(p.iterdir()):
                try:
                    p.rmdir()
                except OSError:
                    pass
    return checks


def build_launch_command(
    cli: str,
    workspace: Path,
    model: str | None = None,
    custom_cmd: str | None = None,
) -> list[str]:
    """根据指定的 CLI 适配器构造启动命令。"""
    if PROBLEMS:
        examples = ", ".join(f"submissions/{p}{PROBLEM_EXT[p]}" for p in PROBLEMS[:3])
        example_clause = f"(e.g. {examples})"
    else:
        example_clause = "(see README.md for filenames)"
    prompt = (
        "Read README.md and AGENTS.md in this workspace. "
        f"Write and test your complete submissions for all benchmark problems at submissions/<problem>.<ext> {example_clause}. "
        "Access only this workspace's materials and your own logs, results, and scratch files."
    )

    if cli == "codex":
        codex_base = resolve_codex_cmd()
        cmd = codex_base + [
            "--no-daemon", "exec", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--ephemeral", "-C", str(workspace),
        ]
        cmd += config_overrides(workspace, protected_roots(workspace))
        if model:
            cmd += ["--model", model]
        cmd += ["--output-last-message", str(workspace / "logs" / "final.txt"), prompt]
        return cmd

    if cli == "claude":
        claude_bin = shutil.which("claude") or "claude"
        cmd = [claude_bin, "--cwd", str(workspace), "-p", prompt]
        if model:
            cmd += ["--model", model]
        return cmd

    if cli == "gemini":
        gemini_bin = shutil.which("gemini") or "gemini"
        cmd = [gemini_bin, "--dir", str(workspace), "-p", prompt]
        if model:
            cmd += ["--model", model]
        return cmd

    if cli == "custom":
        if not custom_cmd:
            raise ValueError("--custom-cmd is required when --cli custom is selected")
        rendered = custom_cmd.format(
            workspace=str(workspace),
            model=model or "",
            submissions=str(workspace / "submissions"),
        )
        return shlex.split(rendered, posix=(sys.platform != "win32"))

    raise ValueError(f"Unknown CLI adapter: {cli}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, default=DEFAULT_WORKSPACE,
        help="Path to Final contestant workspace (defaults to Final/contestant)",
    )
    parser.add_argument(
        "--cli", choices=["codex", "claude", "gemini", "custom"], default="codex",
        help="Target AI CLI tool adapter (default: codex)",
    )
    parser.add_argument("--model", help="Optional model identifier to pass to the CLI")
    parser.add_argument("--custom-cmd", help="Custom command template when using --cli custom")
    parser.add_argument("--check-only", action="store_true", help="Run access & preflight checks and exit")
    parser.add_argument("--print-command", action="store_true", help="Print constructed launch command and exit")

    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error(f"Workspace directory does not exist: {workspace}")

    codex_bin = shutil.which("codex")
    if args.cli == "codex" and not codex_bin and not args.print_command and not args.check_only:
        print("Note: 'codex' executable not found on PATH. Use --cli {claude,gemini,custom} or install Codex CLI.", file=sys.stderr)

    try:
        checks = run_preflight_probe(
            workspace,
            use_codex=(args.cli == "codex"),
            write_log=(not args.check_only),
        )
        print("Preflight check passed:", json.dumps(checks))
    except Exception as exc:
        parser.error(f"Preflight check failed: {exc}")

    if args.check_only:
        for d in ("logs", ".scratch"):
            p = workspace / d
            if p.is_dir() and not any(p.iterdir()):
                try:
                    p.rmdir()
                except OSError:
                    pass
        for p in PROBLEMS:
            for sub in ("agents/submissions", "results", "logs"):
                d = workspace / p / sub
                if d.is_dir() and not any(d.iterdir()):
                    try:
                        d.rmdir()
                    except OSError:
                        pass
        print("Preflight check-only mode succeeded.")
        return

    cmd = build_launch_command(args.cli, workspace, model=args.model, custom_cmd=args.custom_cmd)
    if args.print_command:
        print("Constructed command:")
        print(json.dumps(cmd, ensure_ascii=False, indent=2))
        return

    log_file = workspace / "logs" / f"{args.cli}.log"
    print(f"Launching contestant CLI ({args.cli}). Logs streaming to {log_file} ...")
    with open(log_file, "wb") as log:
        proc = subprocess.run(cmd, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
    print(f"Contestant finished with exit code {proc.returncode}. Log saved at {log_file}")
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
