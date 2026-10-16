"""Make a fresh clone of Forge runnable, in one command.

    scripts\\setup.cmd            Windows
    python3 scripts/setup.py      Linux and macOS

It starts the local PostgreSQL and Redis containers, writes `backend/.env` with working
values and freshly generated keys, installs both sides and creates the database. Run it
again whenever you like: it never overwrites a value that is already in `backend/.env`,
and it fills in the ones that are missing.

Why it exists: a clone has no `backend/.env`, and without `EVIDENCE_KEYS` in it Forge can
show every screen but cannot save anything that writes to the audit trail, which reads as
a plain 500 (16 Oct 2026). Nobody should have to learn that to start.
"""

import argparse
import base64
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]

# The database values that match infra/postgres-init.sql and the first migration, and a
# key for each setting that has to hold one. parse() in app/core/keyring.py reads
# "id:base64key"; the id only names the key, so that a rotation can keep both.
def _key(prefix: str) -> str:
    return (f"{prefix}{datetime.now(UTC):%Y%m%d}:"
            f"{base64.urlsafe_b64encode(os.urandom(32)).decode()}")


def _values() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+asyncpg://forge_app:forge_app@localhost:5432/forge",
        "MIGRATION_DATABASE_URL":
            "postgresql+asyncpg://forge_owner:forge_owner@localhost:5432/forge",
        "APP_DB_PASSWORD": "forge_app",
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "CREDENTIAL_KEYS": _key("k"),
        "AUDIT_SEAL_KEYS": _key("s"),
        "EVIDENCE_KEYS": _key("e"),
    }


def say(step: str) -> None:
    print(f"\n==> {step}", flush=True)


def stop(message: str) -> None:
    print(f"\nSTOPPED. {message}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True,
        quiet: bool = False) -> subprocess.CompletedProcess:
    """Run a command, telling the person what it was if it fails. On Windows `npm` is a
    .cmd file, which has to go through the shell."""
    exe = shutil.which(cmd[0]) or cmd[0]
    full = [exe, *cmd[1:]]
    print("    " + " ".join(cmd), flush=True)
    kwargs = {"cwd": str(cwd or ROOT), "text": True}
    if quiet:
        kwargs["capture_output"] = True
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        done = subprocess.run(subprocess.list2cmdline(full), shell=True, **kwargs)
    else:
        done = subprocess.run(full, **kwargs)
    if check and done.returncode != 0:
        if quiet and done.stderr:
            print(done.stderr, file=sys.stderr)
        stop(f"`{' '.join(cmd)}` failed. Fix what it printed above and run setup again.")
    return done


def need(tool: str, what: str, where: str) -> None:
    if shutil.which(tool) is None:
        stop(f"{what} is not installed, or not on the PATH. Get it from {where}, open a "
             f"new terminal, and run setup again.")


def psql(database: str, sql: str, check: bool = True) -> subprocess.CompletedProcess:
    return run([*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "postgres",
                "-d", database, "-tAc", sql], check=check, quiet=True)


def wait_for_postgres(seconds: int = 90) -> None:
    for _ in range(seconds):
        if run([*COMPOSE, "exec", "-T", "postgres", "pg_isready", "-U", "postgres"],
               check=False, quiet=True).returncode == 0:
            return
        time.sleep(1)
    stop("PostgreSQL did not come up. Look at `docker compose -f infra/docker-compose.yml "
         "logs postgres`.")


def database_ready() -> None:
    """The container runs infra/postgres-init.sql the first time it is created. A volume
    made before that file existed has no forge_owner, which cannot be fixed from here."""
    wait_for_postgres()
    owner = psql("postgres", "SELECT 1 FROM pg_roles WHERE rolname = 'forge_owner'")
    if owner.stdout.strip() != "1":
        stop("The PostgreSQL container has no forge_owner role, so its data folder was "
             "made before infra/postgres-init.sql. If there is nothing in it you want, "
             "run `docker compose -f infra/docker-compose.yml down -v` and setup again.")
    for database in ("forge", "forge_test"):
        psql(database, "CREATE EXTENSION IF NOT EXISTS vector")


def write_env() -> None:
    """Keep every comment from .env.example, fill in only what is empty or CHANGE_ME."""
    env, example = ROOT / "backend" / ".env", ROOT / "backend" / ".env.example"
    values = _values()
    lines = (env if env.exists() else example).read_text().splitlines()
    out, filled = [], []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if "=" in line and name in values:
            value = line.split("=", 1)[1].strip()
            # .env.example writes CHANGE_ME inside the database URLs, not only as the
            # whole value, so a password left as CHANGE_ME counts as unset.
            if value == "" or "CHANGE_ME" in value:
                out.append(f"{name}={values[name]}")
                filled.append(name)
                continue
        out.append(line)
    for name, value in values.items():  # a setting the example no longer lists
        if not any(l.split("=", 1)[0].strip() == name for l in out):
            out.append(f"{name}={value}")
            filled.append(name)
    env.write_text("\n".join(out) + "\n")
    if not filled:
        print("    backend/.env already has everything Forge needs.")
    else:
        print("    filled in: " + ", ".join(filled))
        print("    Keep backend/.env out of git. EVIDENCE_KEYS is what makes the audit")
        print("    details on this machine readable: lose it and they stay unreadable.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up Forge CRM for development.")
    ap.add_argument("--skip-docker", action="store_true",
                    help="use a PostgreSQL and Redis you run yourself (then edit "
                         "backend/.env before running setup again)")
    args = ap.parse_args()

    # OneDrive (and any other cloud-synced folder) cannot hardlink, which is how uv
    # fills a virtual environment from its cache: it fails with "The cloud operation
    # cannot be performed on a file with incompatible hardlinks" (os error 396).
    # Copying is a little slower and always works.
    os.environ.setdefault("UV_LINK_MODE", "copy")
    if "onedrive" in str(ROOT).lower():
        print(f"\nNOTE: this project is inside OneDrive ({ROOT}).\n"
              "      Setup will work, but OneDrive keeps trying to upload the thousands\n"
              "      of files in .venv and node_modules, which is slow and can lock a\n"
              "      file while you build. A folder outside OneDrive, such as\n"
              "      C:\\dev\\forge-crm, is the easier place to work.")

    say("Checking the tools")
    need("uv", "uv (it installs Python itself)", "https://docs.astral.sh/uv/")
    need("npm", "Node.js 22", "https://nodejs.org/")
    if not args.skip_docker:
        need("docker", "Docker Desktop", "https://www.docker.com/products/docker-desktop/")

    if not args.skip_docker:
        say("Starting PostgreSQL and Redis")
        if run([*COMPOSE, "up", "-d"], check=False).returncode != 0:
            stop("Docker could not start them. Open Docker Desktop, wait until it says it "
                 "is running, and try setup again.")
        say("Waiting for PostgreSQL")
        database_ready()

    say("Writing backend/.env")
    write_env()

    say("Installing the backend (uv sync)")
    run(["uv", "sync"], cwd=ROOT / "backend")

    say("Creating the database tables (alembic upgrade head)")
    run(["uv", "run", "alembic", "upgrade", "head"], cwd=ROOT / "backend")

    say("Installing the frontend (npm install)")
    run(["npm", "install"], cwd=ROOT / "frontend")

    print("""
Done. Forge is ready. Start it in three terminals, from the project folder:

  1  cd backend    uv run uvicorn app.main:app --reload --port 8000
  2  cd backend    uv run celery -A app.worker.celery_app worker --loglevel=info --pool=solo
  3  cd frontend   npm run dev

Then open http://localhost:5173 and sign in with company `forge`, password `Forge@2026`:

  admin@forge.example.com     everything
  tester@forge.example.com    decides approval cards
  finance@forge.example.com   second approver
  rep@forge.example.com       asks for approvals

START_HERE.md has the rest: what each terminal is for, what works without an AI key, and
what to do when something looks wrong.""")


if __name__ == "__main__":
    main()
