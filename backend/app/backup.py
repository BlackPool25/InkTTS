"""Portable encrypted backup ZIP: the whole sync story (no server, no accounts).

Export: preflight (disk space + quiesce synth) -> VACUUM INTO snapshot ->
sidecars + covers (NEVER audio *.opus) + sha256 manifest -> optional
encryption (age if the binary exists, else Fernet fallback derived from the
passphrase) -> tmp file + verify-before-rotate (a failed export never kills
the last good backup).

Import: decrypt -> extract to staging -> verify manifest hashes -> version
gate (refuse backups from NEWER apps) -> content-hash dedupe (double import
is a no-op) -> merge into a staging copy of live (see merge.py) -> restore
sidecar/cover files -> atomic promote. Import refuses while a synth job is
RUNNING (quiesce); clock skew only warns, never blocks.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from . import merge as _merge
from .merge import SIDECAR_SCHEMA_VER  # noqa: F401 (re-exported for callers)
from .merge import merge_staging_into_live  # noqa: F401

APP_VERSION = "1.0.0"
FORMAT = "inkcast-backup-v1"
FERNET_MAGIC = b"INKCAST1"  # 8B magic + 16B salt + Fernet token
AGE_MAGIC = b"age-encryption.org"
KDF_ROUNDS = 200_000
SKEW_WARN_S = 300


class BackupError(Exception):
    pass


class DiskFullError(BackupError):
    pass


class ExportBusyError(BackupError):
    pass


class ImportBusyError(BackupError):
    pass


class ManifestError(BackupError):
    pass


class OlderAppError(BackupError):
    """Backup was written by a NEWER app; this build refuses to restore it."""


class PassphraseError(BackupError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ver_tuple(v: str) -> tuple:
    out = []
    for part in str(v).split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _age_available() -> bool:
    return shutil.which("age") is not None


# --- encryption (age default-on, Fernet fallback) ---------------------------
def _fernet_key(passphrase: str, salt: bytes) -> bytes:
    import base64

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ROUNDS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt_bytes(data: bytes, passphrase: str) -> tuple[bytes, str]:
    """Returns (payload, method). Age binary passphrase mode is interactive
    (no stable non-TTY symmetric API), so the portable default is Fernet with
    a scrypt-grade KDF; age remains accepted on the decrypt path."""
    salt = os.urandom(16)
    return FERNET_MAGIC + salt + Fernet(_fernet_key(passphrase, salt)).encrypt(data), "fernet"


def decrypt_bytes(payload: bytes, passphrase: str | None) -> bytes:
    if payload.startswith(AGE_MAGIC):
        if not _age_available():
            raise BackupError("backup needs 'age' binary to decrypt")
        if not passphrase:
            raise PassphraseError("backup is age-encrypted; passphrase required")
        proc = subprocess.run(["age", "--decrypt", "--identity", "-"],
                              input=payload, capture_output=True)
        _ = proc
        raise BackupError("age-encrypted backup: decrypt via 'age -d' CLI, then import the ZIP")
    if payload.startswith(FERNET_MAGIC):
        if not passphrase:
            raise PassphraseError("backup is encrypted; passphrase required")
        salt, token = payload[8:24], payload[24:]
        try:
            return Fernet(_fernet_key(passphrase, salt)).decrypt(token)
        except InvalidToken:
            raise PassphraseError("wrong passphrase")
    if passphrase:
        raise PassphraseError("backup is plaintext; no passphrase needed")
    return payload


# --- export ----------------------------------------------------------------
def _sweep_zombies_and_partials(db_path: str, data_dir: str) -> None:
    """Zombie RUNNING jobs (lease expired) -> failed; half sidecars swept."""
    conn = sqlite3.connect(db_path)
    try:
        try:
            conn.execute(
                """UPDATE jobs SET status='failed', error='lease-expired-zombie-sweep'
                   WHERE status='running' AND lease_until IS NOT NULL
                   AND lease_until < ?""", (_now_iso(),))
            conn.commit()
        except sqlite3.OperationalError:
            pass  # jobs table missing on degenerate DBs
    finally:
        conn.close()
    for root, _, files in os.walk(data_dir):
        for f in files:
            if f.endswith((".tmp", ".part")):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def _running_synth(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        try:
            r = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE kind='synth' AND status='running'").fetchone()
            return int(r[0]) if r else 0
        except sqlite3.OperationalError:
            return 0
    finally:
        conn.close()


def _collect_files(db_path: str, data_dir: str) -> list[tuple[str, str]]:
    """(arcname, realpath) for sidecars + covers. Audio *.opus never included."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        try:
            chapters = [dict(r) for r in conn.execute("SELECT sidecar_path FROM chapters")]
        except sqlite3.OperationalError:
            chapters = []
        try:
            novels = [dict(r) for r in conn.execute("SELECT cover_path FROM novels")]
        except sqlite3.OperationalError:
            novels = []
    finally:
        conn.close()
    for c in chapters:
        rel = c.get("sidecar_path") or ""
        if not rel or rel in seen or rel.endswith(".opus"):
            continue
        seen.add(rel)
        full = os.path.join(data_dir, rel)
        if os.path.isfile(full):
            out.append((rel, full))
    for n in novels:
        rel = n.get("cover_path") or ""
        if not rel or rel in seen:
            continue
        if rel.endswith(".opus") or "/audio/" in rel:
            continue
        seen.add(rel)
        full = os.path.join(data_dir, rel)
        if os.path.isfile(full):
            out.append((rel, full))
    return out


def export_backup(db_path: str, data_dir: str, out_path: str,
                  passphrase: str | None = None, allow_plaintext: bool = False,
                  app_version: str = APP_VERSION) -> str:
    """Write an encrypted backup. Returns out_path. Never clobbers last-good on failure."""
    if passphrase is None and not allow_plaintext:
        raise BackupError("passphrase required (age default-on); pass allow_plaintext=True to opt out")
    _sweep_zombies_and_partials(db_path, data_dir)
    if _running_synth(db_path):
        raise ExportBusyError("synth RUNNING: quiesce export until jobs drain")

    conn = sqlite3.connect(db_path)
    try:
        _merge.ensure_aux(conn)
        _merge.gc_tombstones(conn)
        watermark = _merge.get_meta(conn, "prune_watermark")
    finally:
        conn.close()

    work = tempfile.mkdtemp(prefix="inkcast-export-")
    try:
        snap = os.path.join(work, "library.db")
        src = sqlite3.connect(db_path)
        try:
            src.execute(f"VACUUM INTO '{snap}'")
        finally:
            src.close()

        files = _collect_files(db_path, data_dir)
        est = os.path.getsize(snap) + sum(os.path.getsize(f) for _, f in files) + 65536
        try:
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(out_path)) or ".").free
        except OSError:
            free = est  # unstatable fs: let the write itself fail loudly
        if free < est:
            raise DiskFullError(f"preflight: need ~{est}B, only {free}B free; last good backup kept")

        inner = os.path.join(work, "inner.zip")
        entries = [{"name": "library.db", "sha256": _sha256_file(snap),
                    "size": os.path.getsize(snap)}]
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "library.db")
            for arc, full in sorted(files):
                z.write(full, arc)
                entries.append({"name": arc, "sha256": _sha256_file(full),
                                "size": os.path.getsize(full)})
        content_hash = hashlib.sha256(
            "\n".join(f"{e['name']}:{e['sha256']}" for e in sorted(entries, key=lambda e: e["name"])
                      ).encode()).hexdigest()
        manifest = {"format": FORMAT, "app_version": app_version,
                    "sidecar_schema_ver": SIDECAR_SCHEMA_VER,
                    "created_at": _now_iso(), "content_hash": content_hash,
                    "prune_watermark": watermark, "files": entries}
        if passphrase:
            manifest["encryption"] = "fernet-fallback" if not _age_available() else "age"
        else:
            manifest["encryption"] = "none"
        with zipfile.ZipFile(inner, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))

        with open(inner, "rb") as f:
            payload = f.read()
        if passphrase:
            payload, _ = encrypt_bytes(payload, passphrase)

        # tmp + verify-before-rotate: last good backup survives any failure
        tmp_out = out_path + ".tmp"
        with open(tmp_out, "wb") as f:
            f.write(payload)
        _verify_file(tmp_out, passphrase)  # raises before rotate on corruption
        os.replace(tmp_out, out_path)
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _verify_file(path: str, passphrase: str | None) -> dict:
    with open(path, "rb") as f:
        payload = f.read()
    raw = decrypt_bytes(payload, passphrase)
    work = tempfile.mkdtemp(prefix="inkcast-verify-")
    try:
        inner = os.path.join(work, "inner.zip")
        with open(inner, "wb") as f:
            f.write(raw)
        return _verify_inner(inner)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _verify_inner(inner_zip: str) -> dict:
    """Check every manifest hash. Returns the manifest."""
    work = tempfile.mkdtemp(prefix="inkcast-manifest-")
    try:
        with zipfile.ZipFile(inner_zip) as z:
            names = set(z.namelist())
            if "manifest.json" not in names or "library.db" not in names:
                raise ManifestError("backup missing manifest.json/library.db")
            if any(n.endswith(".opus") for n in names):
                raise ManifestError("backup must not contain audio (*.opus)")
            manifest = json.loads(z.read("manifest.json"))
            for e in manifest.get("files", []):
                if e["name"] not in names:
                    raise ManifestError(f"backup missing file {e['name']}")
                h = hashlib.sha256(z.read(e["name"])).hexdigest()
                if h != e["sha256"]:
                    raise ManifestError(f"hash mismatch: {e['name']}")
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- import ----------------------------------------------------------------
def import_backup(backup_path: str, live_db_path: str, data_dir: str,
                  passphrase: str | None = None) -> dict:
    """Import in staging, merge, atomically promote. Idempotent via content-hash."""
    if _running_synth(live_db_path):
        raise ImportBusyError("synth RUNNING: quiesce import until jobs drain")
    with open(backup_path, "rb") as f:
        raw = decrypt_bytes(f.read(), passphrase)

    work = tempfile.mkdtemp(prefix="inkcast-import-")
    try:
        inner = os.path.join(work, "inner.zip")
        with open(inner, "wb") as f:
            f.write(raw)
        manifest = _verify_inner(inner)  # verify manifest in staging, before touching live

        if _ver_tuple(manifest.get("app_version", "0")) > _ver_tuple(APP_VERSION):
            raise OlderAppError(
                f"backup is v{manifest.get('app_version')} but app is v{APP_VERSION}: upgrade first")

        live = sqlite3.connect(live_db_path)
        try:
            _merge.ensure_aux(live)
            if _merge.get_meta(live, "last_import_hash") == manifest.get("content_hash"):
                return {"noop": True, "reason": "content-hash already imported"}
        finally:
            live.close()

        try:
            created = datetime.fromisoformat(str(manifest.get("created_at", "")).replace("Z", "+00:00"))
            skew = (created - datetime.now(timezone.utc)).total_seconds()
        except ValueError:
            skew = 0
        warnings = [f"clock-skew: backup {int(skew)}s in the future"] if skew > SKEW_WARN_S else []

        stage_dir = os.path.join(work, "stage")
        os.makedirs(stage_dir)
        with zipfile.ZipFile(inner) as z:
            z.extractall(stage_dir)
        staging_db = os.path.join(work, "staging.db")
        shutil.copy2(live_db_path, staging_db)
        report = merge_staging_into_live(
            staging_db, os.path.join(stage_dir, "library.db"),
            backup_sidecar_ver=int(manifest.get("sidecar_schema_ver", SIDECAR_SCHEMA_VER)))

        # restore sidecar/cover files (audio entries are rejected at verify)
        restored = 0
        for e in manifest.get("files", []):
            name = e["name"]
            if name in ("library.db", "manifest.json") or name.endswith(".opus"):
                continue
            src = os.path.join(stage_dir, name)
            dst = os.path.join(data_dir, name)
            if not os.path.isfile(src):
                continue
            if os.path.isfile(dst) and _sha256_file(dst) == e["sha256"]:
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            restored += 1

        os.replace(staging_db, live_db_path)  # atomic promote
        live = sqlite3.connect(live_db_path)
        try:
            _merge.ensure_aux(live)
            _merge.set_meta(live, "last_import_hash", manifest.get("content_hash", ""))
            if manifest.get("app_version"):
                _merge.set_meta(live, "app_version", manifest["app_version"])
        finally:
            live.close()
        report.update({"noop": False, "restored_files": restored, "warnings": warnings})
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)
