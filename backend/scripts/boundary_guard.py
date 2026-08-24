#!/usr/bin/env python3
"""
Boundary guard for SupoClip V2 import matrix.

Implements the legality matrix from docs/architecture/TARGET_ARCHITECTURE.md §2.
Checks that each layer only imports allowed layers.

Usage:
    python backend/scripts/boundary_guard.py
    python backend/scripts/boundary_guard.py --strict  # exit 1 on violations

Layers (importer -> allowed imported):
- shared/config: OK shared/errors, shared/observability ; lazy infra/db, infra/cache ; NG repositories, domain, api, workers
- infra/db: OK shared ; NG infra/cache, repositories, domain, api, workers
- infra/cache: lazy shared/config, OK shared/errors ; NG infra/db, repositories, domain, api
- repositories (infra/db/repositories): OK shared, infra/db ; NG infra/cache, domain, api, workers
- domain/* : OK shared, infra, repositories ; NG api, workers
- api/routes: OK shared, infra/db (DI), infra/cache (DI), domain ; NG repositories, workers, api
- workers: OK shared, infra, domain ; NG repositories via direct, api
- migrations: OK infra/db, models ; NG others

This guard is intentionally simple (regex-based) and runs without import-linter.
For strict enforcement, add `import-linter` config (see .importlinter).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # backend
SRC = ROOT / "src"

# classify a file path to a layer
def classify(path: Path) -> str:
    rel = path.relative_to(SRC).as_posix()
    if rel.startswith("shared/config"):
        return "shared/config"
    if rel.startswith("shared/errors"):
        return "shared/errors"
    if rel.startswith("shared/observability"):
        return "shared/observability"
    if rel.startswith("shared/"):
        return "shared"
    if rel.startswith("infra/db/repositories"):
        return "repositories"
    if rel.startswith("infra/db"):
        return "infra/db"
    if rel.startswith("infra/cache"):
        return "infra/cache"
    if rel.startswith("infra/"):
        return "infra"
    if rel.startswith("domain/"):
        return "domain"
    if rel.startswith("api/routes"):
        return "api"
    if rel.startswith("api/"):
        return "api"
    if rel.startswith("workers"):
        return "workers"
    if rel.startswith("migrations/"):
        return "migrations"
    if rel.startswith("repositories"):
        # old shim location, treat as repositories
        return "repositories"
    # god modules at root
    if rel in ("models.py", "ai.py", "video_utils.py", "clip_editor.py", "youtube_utils.py"):
        return "god"
    # shim files at root that are leaf -> treat as shared/infra/domain per new location, skip
    return "other"

# define illegal targets per importer
# map: importer layer -> set of illegal imported layer patterns
ILLEGAL = {
    "shared/config": {"repositories", "domain", "api", "workers", "infra/db", "infra/cache", "infra"},
    # ^ infra/db and infra/cache only allowed via lazy (inside function)
    "shared/errors": {"infra", "infra/db", "infra/cache", "repositories", "domain", "api", "workers"},
    "shared/observability": {"infra", "infra/db", "infra/cache", "repositories", "domain", "api", "workers"},
    "infra/db": {"infra/cache", "repositories", "domain", "api", "workers"},
    "infra/cache": {"infra/db", "repositories", "domain", "api", "workers"},
    "infra": {"repositories", "domain", "api", "workers"},
    "repositories": {"infra/cache", "domain", "api", "workers"},
    "domain": {"api", "workers"},
    "api": {"repositories", "workers", "api"},
    "workers": {"api", "repositories"},
    "migrations": {"shared", "shared/config", "shared/errors", "domain", "api", "workers", "infra/cache"},
}

# patterns to detect imports
IMPORT_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import|^\s*import\s+([.\w]+)")
# map import string to layer
def import_to_layer(imp: str, current_file: Path) -> str:
    # resolve relative imports
    if imp.startswith("."):
        # count dots
        dots = len(imp) - len(imp.lstrip("."))
        rest = imp.lstrip(".")
        # navigate up
        cur_pkg = current_file.parent.relative_to(SRC).as_posix()  # e.g., domain/task
        # for file at src/domain/task/validation.py, parent is domain/task
        # dots=1 => same package, dots=2 => parent, etc.
        # we simulate by walking
        pkg_parts = cur_pkg.split("/") if cur_pkg != "." else []
        # if current is __init__.py, pkg is its directory, else parent
        if current_file.name != "__init__.py":
            # already parent
            pass
        else:
            # file is __init__.py, pkg is already its directory
            pass
        # for dots=1, stay at pkg; dots=2 go up one, etc.
        up = dots - 1
        if up > len(pkg_parts):
            base = []
        else:
            base = pkg_parts[: len(pkg_parts) - up] if up else pkg_parts
        if rest:
            target = "/".join(base + rest.split("."))
        else:
            target = "/".join(base)
        # target like shared/config or infra/cache/redis_client
        # classify by prefix
    else:
        # absolute: src.shared.config or src.infra.db etc or src.XXX
        if imp.startswith("src."):
            target = imp[4:].replace(".", "/")
        else:
            return "external"
    # classify target
    if target.startswith("shared/config"):
        return "shared/config"
    if target.startswith("shared/errors"):
        return "shared/errors"
    if target.startswith("shared/observability"):
        return "shared/observability"
    if target.startswith("shared"):
        return "shared"
    if target.startswith("infra/db/repositories"):
        return "repositories"
    if target.startswith("infra/db"):
        return "infra/db"
    if target.startswith("infra/cache"):
        return "infra/cache"
    if target.startswith("infra"):
        return "infra"
    if target.startswith("domain"):
        return "domain"
    if target.startswith("api"):
        return "api"
    if target.startswith("workers"):
        return "workers"
    if target.startswith("migrations"):
        return "migrations"
    if target.startswith("repositories"):
        return "repositories"
    if target in ("config", "database", "errors", "observability", "media_tools", "model_assets", "task_validation", "clip_metadata", "clip_source_map", "clip_cleanup", "caption_templates", "emoji_captions", "font_registry", "face_tracking", "visual_signals", "transition_spec", "transition_engine", "apify_youtube_downloader", "video_cache", "broll", "freesound", "sound_effect_cache", "auth_headers", "admin_auth", "models", "ai", "video_utils", "clip_editor", "youtube_utils", "main_refactored", "app", "worker_main", "worker"):
        # old root shims -> map to new layers roughly
        leaf_shared = {"config": "shared/config", "errors": "shared/errors", "observability": "shared/observability"}
        leaf_infra = {"database": "infra/db", "media_tools": "infra", "model_assets": "infra"}
        if target in leaf_shared:
            return leaf_shared[target]
        if target in leaf_infra:
            return leaf_infra[target]
        # domain/media etc
        return "other"
    return "external"

def is_lazy_import(line: str, file_content: str, lineno: int) -> bool:
    # check if import is inside function (indented and after def)
    # simple heuristic: if line is indented >=4 and previous def within 10 lines
    lines = file_content.splitlines()
    # indented imports inside function are lazy
    if line.startswith(" ") or line.startswith("\t"):
        # inside function/class
        return True
    return False

def scan():
    violations = []
    for py in SRC.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        # skip shims at old locations? they are shims that import canonical, we skip them
        # detect shim by marker
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "Shim re-export" in text and "canonical" in text:
            # shim is allowed to import its canonical, skip
            continue
        layer = classify(py)
        if layer in ("other", "god", "external"):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = re.match(r"^\s*from\s+([.\w]+)\s+import", line)
            if not m:
                m2 = re.match(r"^\s*import\s+([.\w]+)", line)
                if not m2:
                    continue
                imp = m2.group(1).split(",")[0].strip()
            else:
                imp = m.group(1)
            target_layer = import_to_layer(imp, py)
            if target_layer == "external" or target_layer == "other":
                continue
            illegal = ILLEGAL.get(layer, set())
            if target_layer in illegal:
                # allow lazy infra imports for shared/config
                if layer == "shared/config" and target_layer in ("infra/db", "infra/cache", "infra"):
                    if is_lazy_import(line, text, i):
                        continue
                if layer == "infra/cache" and target_layer == "shared/config":
                    if is_lazy_import(line, text, i):
                        continue
                # known exceptions: api -> workers for job_queue/progress (queue infra lives under workers)
                if layer == "api" and target_layer == "workers" and ("job_queue" in line or "progress" in line):
                    continue
                # workers -> repositories for tasks.py is legacy direct access; prefer service but allow for now
                if layer == "workers" and target_layer == "repositories":
                    continue
                # repositories -> domain for task validation is legacy pure helper, allowed
                if layer == "repositories" and target_layer == "domain" and "task.validation" in line:
                    continue
                violations.append((py.relative_to(ROOT).as_posix(), i, layer, target_layer, line.strip()))
    return violations

if __name__ == "__main__":
    violations = scan()
    if not violations:
        print("OK Boundary guard: no violations")
        sys.exit(0)
    print(f"FAIL Boundary guard: {len(violations)} violation(s)")
    for path, lineno, importer, imported, line in violations:
        print(f"  {path}:{lineno}  [{importer} -> {imported}]  {line}")
    if "--strict" in sys.argv:
        sys.exit(1)
    else:
        # warn but don't fail in non-strict mode
        sys.exit(0)
