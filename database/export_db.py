"""
database/export_db.py
=====================
Write the university catalogue out of PostgreSQL into a single, readable
.sql file inside the project:  database/study_abroad_data.sql

Only the catalogue tables are exported (countries, universities, programs,
program_links). Admin accounts and stored API keys are deliberately left out,
so the file is safe to share or commit.

Run it:      python database/export_db.py
Restore it:  see the command printed at the end.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.config import settings  # noqa: E402

TABLES = ("countries", "universities", "programs", "program_links")
OUT = Path(__file__).resolve().parent / "study_abroad_data.sql"


def _find_pg_dump() -> str | None:
    """Locate pg_dump.exe (PATH first, then the usual Windows install folders)."""
    from shutil import which
    found = which("pg_dump")
    if found:
        return found
    base = Path(r"C:\Program Files\PostgreSQL")
    if base.exists():
        for version in sorted(base.iterdir(), reverse=True):
            candidate = version / "bin" / "pg_dump.exe"
            if candidate.exists():
                return str(candidate)
    return None


def main() -> int:
    pg_dump = _find_pg_dump()
    if not pg_dump:
        print("Could not find pg_dump. Install the PostgreSQL client tools, "
              "or add its bin folder to PATH.")
        return 1

    env = dict(os.environ)
    env["PGPASSWORD"] = settings.DB_PASSWORD
    cmd = [pg_dump, "-h", settings.DB_HOST, "-p", str(settings.DB_PORT),
           "-U", settings.DB_USER, "-d", settings.DB_NAME,
           "--no-owner", "--no-privileges", "--inserts"]
    for t in TABLES:
        cmd += ["-t", t]
    cmd += ["-f", str(OUT)]

    print(f"Exporting {settings.DB_NAME} -> {OUT.name} ...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print("Export failed:")
        print(result.stderr.strip()[:800])
        return result.returncode

    size_kb = OUT.stat().st_size / 1024
    print(f"Done: {OUT}  ({size_kb:,.0f} KB)")
    print("\nTo restore this file into a database, run:")
    print(f'  psql -h {settings.DB_HOST} -p {settings.DB_PORT} '
          f'-U {settings.DB_USER} -d {settings.DB_NAME} -f "{OUT}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
