# Contestant Workflow (`ai_training`)

When asked to participate in **Frontier Lab (`ai_training`)**, build, package, test, and deliver a complete Python strategy autonomously.
Start with `AI_README.md`, then read `handbook/player-guide.md`, `handbook/game-facts.md`, `agents/template/`, `agents/starter.py`, and `engine/engine.py` / `engine/world.py`.
Do not stop at a plan, a strategy outline, or the empty starter template.

- **Submission format & paths (`.zip` multi-file package)**:
  - Develop in a directory under `agents/submissions/<name>/` with root `main.py` (optionally split into `.py` submodules and read-only data files loaded via `Path(__file__).resolve().parent`).
  - Package and smoke-test using `python scripts/make_submission.py <dir_or_py> --out ../submissions/ai_training.zip`.
  - Unified suite submission (required for formal multi-problem grading): `../submissions/ai_training.zip`.
  - Standalone development / local self-test path (optional): `agents/submissions/my_agent.zip` (or directory `agents/submissions/my_agent/`).
  - Archive limits: compressed `<= 64 MiB`, extracted `<= 256 MiB`, entries `<= 2048`, compression ratio `<= 100x`, root `main.py` required; no encryption, symlinks, path traversal, or Windows reserved names/illegal chars.
- **Runtime constraints**:
  - UTF-8 Python 3.10+ using Python standard library only. No network access, no I/O outside your extracted package directory, no reading opponent source code.
  - Subprocess line-delimited JSON protocol over `stdin` / `stdout` (`init` -> `{}` -> per-tick `obs` -> action JSON -> `end` -> `{}`), launched via `engine/pkg_entry.py <pkg_dir>`.
  - Timing & IPC limits: `10.0s` init handshake, `2.0s` per tick + `120s` shared episode time pool (`obs["time_pool"]`), `2400s` total episode thinking budget, `1.0s` end-message wait, max `50` outstanding unanswered messages, max `1 MiB` per stdout line, max `64` queued stdout lines.
- **Testing & replay inspection**:
  - Run `python scripts/spar.py --list` to check public baselines (`blank`, `scaling`, `merchant`) and `--solo` mode.
  - Test both adversarial and solo modes via `scripts/spar.py` and `scripts/run_match.py` (accepting `.zip`, directory with `main.py`, or `.py`).
  - Record local replays with `python scripts/record_replay.py --a ../submissions/ai_training.zip --b merchant --seed 1 --ticks 4380` and inspect trajectories/events offline in `viz/player.html`.
  - Check `faults` (`timeouts`, `bad_json`, `dead`, `illegal`) and improve your strategy within the available time; report actual measured results honestly.
- **Workspace boundaries**:
  - Read only public contest materials and your own work. Do not inspect other submissions, sibling directories, or private evaluation files.
  - Keep all outputs inside this workspace's `results/`, `replays/`, and `logs/` directories; package outputs only into `agents/submissions/` or `../submissions/ai_training.zip`.
  - Use only the supplied public baselines (`blank`, `scaling`, `merchant`), `--solo`, or your own mirror match as opponents. Do not pass `--unrestricted` or launch a sharing server (`--serve`).
  - Keep the engine, baselines, `config/format.json`, and `config/workspace.json` unchanged.
- **Completion**:
  - Finish with the exact submission `.zip` path and a concise summary of observed self-test scores (`solo` and adversarial vs baselines) and zero-fault verification.
  - If file writes are unavailable (chat-only mode), return the complete code in one Python code block.
