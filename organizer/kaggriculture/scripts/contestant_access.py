"""Build a Codex permission profile scoped to one contestant directory."""
import json
from pathlib import Path
import sys

PROFILE = "contestant"
WRITABLE_DIRS = (
    "submissions", "agents/submissions", "results", "replays", "logs", ".scratch",
)
DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "browser_use", "browser_use_external",
    "computer_use", "in_app_browser", "memories", "hooks", "multi_agent",
)


def toml_value(value):
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + "=" + toml_value(v)
                               for k, v in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False)


def permission_profile(workspace=None, protected_roots=(), platform=None):
    platform = sys.platform if platform is None else platform
    filesystem = {
        ":root": "read" if platform == "win32" else "deny", ":minimal": "read",
        ":tmpdir": "deny", ":slash_tmp": "deny",
    }
    filesystem.update({str(Path(p).resolve()): "deny" for p in protected_roots})
    if platform == "win32":
        filesystem[str(Path(sys.executable).resolve().parent)] = "read"
    if workspace is None:
        filesystem[":workspace_roots"] = {".": "read", **{p: "write" for p in WRITABLE_DIRS}}
    else:
        root = Path(workspace).resolve()
        filesystem[str(root)] = "read"
        filesystem.update({str(root / p): "write" for p in WRITABLE_DIRS})
    return {"filesystem": filesystem, "network": {"enabled": False}}


def config_overrides(workspace, protected_roots=()):
    values = {
        "default_permissions": PROFILE,
        "permissions." + PROFILE: permission_profile(workspace, protected_roots),
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


def project_config(workspace=None, protected_roots=()):
    values = {
        "default_permissions": PROFILE,
        "approval_policy": "never", "web_search": "disabled",
        "windows": {"sandbox": "elevated"},
        "features": {name: False for name in DISABLED_FEATURES},
        "permissions": {PROFILE: permission_profile(workspace, protected_roots)},
    }
    return "\n".join(key + "=" + toml_value(value) for key, value in values.items()) + "\n"
