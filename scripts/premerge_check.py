"""Checks to run before a teammate's work is merged into main.

    uv run --no-project --python 3.12 scripts/premerge_check.py            on your PC
    uv run --no-project --python 3.12 scripts/premerge_check.py --tests    and run both test suites

The GitHub Action in .github/workflows/premerge.yml runs the same script on every push,
on a fresh database.

The rule it guards (Nischay, 21 Oct 2026): every migration is written by the migration
owner. Teammates push features to a project branch, the owner pulls them, writes the
migrations their models need, checks the result and merges it. So:

1. Migration owner: no commit after the base branch may add or change a file in
   backend/alembic/versions unless it was pushed by an owner. On your PC (no --actor)
   the files are only listed, since you are the owner there.
2. One head: every migration's parent exists, no revision id or file number is used
   twice, and exactly one migration has no child. Two heads make `alembic upgrade head`
   fail on every machine, tests included.
3. With --tests: the backend tests (tests/conftest.py rebuilds the forge_test database
   and runs every migration first) and the frontend tests.

It reads the migration files as text, so it needs nothing installed beyond git.
"""

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS = "backend/alembic/versions"

failures: list[str] = []


def report(status: str, check: str, detail: str = "") -> None:
    print(f"{status:<5} {check}" + (f"\n      {detail}" if detail else ""), flush=True)
    if status == "FAIL":
        failures.append(check)


def git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def resolve_commit(name: str) -> str:
    """The commit SHA a branch or commit name points at. The name goes to git on stdin,
    never as an argument, so a value such as "--output=x" cannot become a git option;
    only the SHA git prints back is used afterwards."""
    if "\n" in name:
        raise RuntimeError("a commit name cannot contain a line break")
    done = subprocess.run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                          cwd=ROOT, text=True, capture_output=True, input=f"{name}^{{commit}}\n")
    sha, _, kind = done.stdout.strip().partition(" ")
    if done.returncode != 0 or kind != "commit" or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise RuntimeError(f"no commit named {name!r}")
    return sha


def check_owner(base: str, actor: str | None, owners: list[str]) -> None:
    try:
        base_sha = resolve_commit(base)
        since = git("merge-base", base_sha, "HEAD")
    except RuntimeError as e:
        report("FAIL", "Migration owner", f"cannot compare with {base}: {e}")
        return
    changed = [f for f in git("diff", "--name-only", f"{since}..HEAD", "--", VERSIONS)
               .splitlines() if f.endswith(".py")]
    if not changed:
        report("PASS", "Migration owner", f"no migration files changed since {base}")
        return
    files = ", ".join(Path(f).name for f in changed)
    if actor is None:
        report("WARN", "Migration owner",
               f"migration files changed since {base}: {files}. Check you wrote them.")
    elif actor.lower() in {o.lower() for o in owners}:
        report("PASS", "Migration owner", f"{actor} may change migrations: {files}")
    else:
        report("FAIL", "Migration owner",
               f"{actor} changed {files}. Only {', '.join(owners)} writes "
               "migrations: remove these files and say which model changes need one.")


def _value(tree: ast.Module, name: str):
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            return ast.literal_eval(node.value)
    return None


def check_heads() -> None:
    revisions: dict[str, tuple[str, ...]] = {}
    numbers: dict[str, list[str]] = {}
    problems: list[str] = []
    for path in sorted((ROOT / VERSIONS).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rev, down = _value(tree, "revision"), _value(tree, "down_revision")
        if rev is None:
            problems.append(f"{path.name} has no revision id")
            continue
        if rev in revisions:
            problems.append(f"revision {rev} is used twice ({path.name})")
        revisions[rev] = (down,) if isinstance(down, str) else tuple(down or ())
        match = re.match(r"(\d+)_", path.name)
        if match:
            numbers.setdefault(match.group(1), []).append(path.name)
    for number, names in numbers.items():
        if len(names) > 1:
            problems.append(f"number {number} is used by {' and '.join(names)}")
    for rev, parents in revisions.items():
        for parent in parents:
            if parent not in revisions:
                problems.append(f"{rev} points at {parent}, which does not exist")
    children = {p for parents in revisions.values() for p in parents}
    heads = sorted(r for r in revisions if r not in children)
    if len(heads) != 1:
        problems.append(f"{len(heads)} heads: {', '.join(heads) or 'none'}. Re-point the "
                        "newer one's down_revision at the other and renumber it")
    if problems:
        report("FAIL", "One migration head", "; ".join(problems))
    else:
        report("PASS", "One migration head", f"head is {heads[0]}, {len(revisions)} migrations")


def run_tests() -> None:
    uv, npm = shutil.which("uv"), shutil.which("npm")
    for name, cmd, cwd in (
        ("Backend tests", [uv or "uv", "run", "pytest", "-q"], ROOT / "backend"),
        ("Frontend tests", [npm or "npm", "test"], ROOT / "frontend"),
    ):
        print(f"\n==> {name}: {' '.join(cmd[1:])}", flush=True)
        shell = os.name == "nt" and cmd[0].lower().endswith((".cmd", ".bat"))
        done = subprocess.run(subprocess.list2cmdline(cmd) if shell else cmd,
                              cwd=cwd, shell=shell)
        report("PASS" if done.returncode == 0 else "FAIL", name,
               "" if done.returncode == 0 else "see the test output above")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="origin/main",
                        help="branch the work will be merged into (default origin/main)")
    parser.add_argument("--actor", help="GitHub login that pushed the work (set by the Action)")
    parser.add_argument("--owners", default=os.environ.get("MIGRATION_OWNERS") or "Nischay8506",
                        help="comma-separated GitHub logins allowed to write migrations")
    parser.add_argument("--tests", action="store_true", help="also run both test suites")
    args = parser.parse_args()
    owners = [o.strip() for o in args.owners.split(",") if o.strip()]

    print(f"Pre-merge checks against {args.base}\n", flush=True)
    check_owner(args.base, args.actor, owners)
    check_heads()
    if args.tests:
        run_tests()
    print("\n" + (f"FAILED: {', '.join(failures)}" if failures else "All checks passed."))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
