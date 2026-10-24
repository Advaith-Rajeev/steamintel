"""Bootstrap a project-local Python environment, then initialize SteamIntel."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Importer options: --data-dir DIR, --from-database, --fx-rates FILE, "
        "--genre-aliases FILE, --refresh-metadata, --refresh-limit N, --dry-run. "
        "PostgreSQL must be running and the database in DATABASE_URL must exist."))
    parser.add_argument("--setup-only", action="store_true", help="Install dependencies and create .env without importing")
    parser.add_argument("--skip-install", action="store_true", help="Reuse dependencies already installed in .venv")
    args, importer_args = parser.parse_known_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    env = ROOT / ".venv"
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    if not args.skip_install:
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env. Set DATABASE_URL for PostgreSQL; GEMINI_API_KEY is only needed for the agent.", flush=True)
    if args.setup_only:
        print("Setup complete. Configure .env, create the PostgreSQL database, then run init.py again.")
        return 0
    return subprocess.run([str(python), str(ROOT / "data_import_script.py"), *importer_args]).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
