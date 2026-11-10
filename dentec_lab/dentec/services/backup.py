"""Local backup, restore and database export.

A backup is a single .zip containing a consistent snapshot of the SQLite
database (made with SQLite's online backup API) plus the lab slip document
store.  Nothing is ever sent off the computer.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .. import __version__
from ..config import get_paths

MANIFEST = "dentec_backup.json"


def backup_dir(settings: dict | None = None) -> Path:
    custom = (settings or {}).get("backup_dir", "")
    p = Path(custom) if custom else get_paths().backups
    p.mkdir(parents=True, exist_ok=True)
    return p


def _snapshot_db(db_file: Path, dest: Path) -> None:
    src = sqlite3.connect(str(db_file))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def create_backup(dest_dir: Path | None = None, label: str = "manual", include_documents: bool = True) -> Path:
    paths = get_paths()
    dest_dir = Path(dest_dir) if dest_dir else backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = dest_dir / f"DentecBackup_{stamp}_{label}.zip"
    tmp_db = paths.data / f".backup_{stamp}.db"
    _snapshot_db(paths.db_file, tmp_db)
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, "dentec.db")
            n_docs = 0
            if include_documents and paths.documents.exists():
                for f in paths.documents.rglob("*"):
                    if f.is_file():
                        z.write(f, "documents/" + f.relative_to(paths.documents).as_posix())
                        n_docs += 1
            z.writestr(MANIFEST, json.dumps({"app_version": __version__, "created": stamp, "label": label,
                                             "documents": n_docs}, indent=2))
    finally:
        tmp_db.unlink(missing_ok=True)
    return out


def verify_backup(path: Path) -> dict:
    """Check a backup file and return its manifest; raises ValueError if invalid."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("Not a valid backup file (.zip expected).")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "dentec.db" not in names:
            raise ValueError("Backup does not contain a Dentec database.")
        bad = z.testzip()
        if bad:
            raise ValueError(f"Backup file is damaged ({bad}).")
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError("Backup contains unsafe file paths.")
        manifest = json.loads(z.read(MANIFEST)) if MANIFEST in names else {}
        data = z.read("dentec.db")
    tmp = get_paths().data / ".verify.db"
    tmp.write_bytes(data)
    try:
        con = sqlite3.connect(str(tmp))
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    finally:
        tmp.unlink(missing_ok=True)
    if ok != "ok":
        raise ValueError(f"Database in backup failed integrity check: {ok}")
    if not {"offices", "cases", "invoices"} <= tables:
        raise ValueError("Backup database is not a Dentec Lab database.")
    return manifest


def restore_backup(path: Path) -> Path:
    """Replace the live database and documents with a backup.

    The caller must close the database first (``database.close_db()``) and
    re-open it afterwards.  A safety backup of the current data is made first
    and its path returned.
    """
    verify_backup(path)
    paths = get_paths()
    safety = create_backup(label="before_restore")
    with zipfile.ZipFile(path) as z:
        # database
        for suffix in ("", "-wal", "-shm"):
            Path(str(paths.db_file) + suffix).unlink(missing_ok=True)
        with z.open("dentec.db") as src, open(paths.db_file, "wb") as dst:
            shutil.copyfileobj(src, dst)
        # documents
        doc_names = [n for n in z.namelist() if n.startswith("documents/") and not n.endswith("/")]
        if doc_names:
            old = paths.documents.with_name("documents_replaced")
            if old.exists():
                shutil.rmtree(old)
            if paths.documents.exists():
                paths.documents.rename(old)
            paths.documents.mkdir(parents=True, exist_ok=True)
            for n in doc_names:
                target = paths.documents / n[len("documents/"):]
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            shutil.rmtree(old, ignore_errors=True)
    return safety


def prune_backups(dest_dir: Path | None = None, keep: int = 30) -> int:
    dest_dir = Path(dest_dir) if dest_dir else backup_dir()
    auto = sorted(dest_dir.glob("DentecBackup_*_auto.zip"))
    removed = 0
    for f in auto[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)
        removed += 1
    return removed


def auto_backup_due(last_backup: str, hours: int = 24) -> bool:
    if not last_backup:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(last_backup) >= timedelta(hours=hours)
    except ValueError:
        return True


def list_backups(dest_dir: Path | None = None) -> list[Path]:
    dest_dir = Path(dest_dir) if dest_dir else backup_dir()
    return sorted(dest_dir.glob("DentecBackup_*.zip"), reverse=True)


def export_database(dest: Path) -> Path:
    """Export every table to CSV, plus a copy of the SQLite file, in one zip."""
    paths = get_paths()
    dest = Path(dest)
    tmp_db = paths.data / ".export.db"
    _snapshot_db(paths.db_file, tmp_db)
    try:
        con = sqlite3.connect(str(tmp_db))
        try:
            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.write(tmp_db, "dentec.db")
                tables = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for t in tables:
                    cur = con.execute(f'SELECT * FROM "{t}"')
                    cols = [c[0] for c in cur.description]
                    if t == "users":  # never export password hashes
                        idx = cols.index("password_hash")
                        rows = [tuple("***" if i == idx else v for i, v in enumerate(r)) for r in cur]
                    else:
                        rows = cur.fetchall()
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(cols)
                    w.writerows(rows)
                    z.writestr(f"csv/{t}.csv", buf.getvalue())
        finally:
            con.close()
    finally:
        tmp_db.unlink(missing_ok=True)
    return dest
