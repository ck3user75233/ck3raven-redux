"""
QBuilder Discovery — Canonical Phase 1

Crash-safe file enumeration with fingerprint binding.

Key behaviors:
- discovery_queue references cvid ONLY (paths derived via joins)
- Every file gets upserted with fingerprint (mtime, size, hash)
- Every file gets build_queue row with fingerprint binding
- Commits frequently for crash safety
- Resume via last_path_processed

No parallel constructs:
- No root_type/root_path/root_name in queue
- No files_discovered/files_queued counters
- Logging derives paths via canonical joins
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
import sqlite3

from ck3lens.paths import ROOT_USER_DOCS
from ck3raven.db.content import store_file_content
from qbuilder.identity import new_cvid

# ---------------------------------------------------------------------------
# BOM Artifact Repair
# ---------------------------------------------------------------------------
# UTF-8 BOM (EF BB BF) decoded as latin-1/cp1252 produces "ï»¿".
# These signatures appear at the start of files with doubled or mis-decoded BOMs.
_BOM_ARTIFACT_BYTES = (
    b'\xef\xbb\xbf\xef\xbb\xbf',              # double UTF-8 BOM
    b'\xef\xbb\xbf\xc3\xaf\xc2\xbb\xc2\xbf',  # UTF-8 BOM + "ï»¿" as UTF-8
    b'\xef\xbb\xbf\xef',                       # UTF-8 BOM + start of another BOM
)
_LATIN1_BOM_AS_UTF8 = b'\xc3\xaf\xc2\xbb\xc2\xbf'  # "ï»¿" as UTF-8 at file start


def _repair_encoding_if_needed(path: Path) -> bool:
    """
    Rewrite *path* with correct UTF-8-with-BOM encoding if BOM artifacts are detected.

    Returns True if the file was rewritten, False otherwise.
    Only call for paths the process is allowed to write (user_docs mods).
    """
    import re as _re
    try:
        raw = path.read_bytes()
    except OSError:
        return False

    has_artifact = (
        raw.startswith(_LATIN1_BOM_AS_UTF8)
        or any(raw.startswith(sig) for sig in _BOM_ARTIFACT_BYTES)
    )
    if not has_artifact:
        return False

    try:
        text = raw.decode('utf-8-sig', errors='replace')
        text = _re.sub(r'^(?:\ufeff|ï»¿)+', '', text)
        text = text.replace('\ufeff', '')
    except Exception:
        return False

    try:
        path.write_text(text, encoding='utf-8-sig', newline='')
        print(f"[EncodingRepair] Rewrote {path.name} with clean utf-8-sig BOM")
        return True
    except OSError as exc:
        print(f"[EncodingRepair] Could not rewrite {path}: {exc}")
        return False


_LOCAL_MOD_ROOT: Path = ROOT_USER_DOCS / "mod"

# Commit progress every N files
COMMIT_BATCH_SIZE = 500

# Lease duration in seconds
DISCOVERY_LEASE_SECONDS = 300  # 5 minutes


@dataclass
class FileRecord:
    """Discovered file with fingerprint and optional raw bytes for persistence."""
    relpath: str
    mtime: float      # Unix timestamp (seconds)
    size: int         # Bytes
    hash: str         # SHA256 hex
    raw_bytes: bytes | None = None


def get_routing_table() -> dict:
    """Load routing table from JSON file."""
    routing_path = Path(__file__).parent / "routing_table.json"
    if routing_path.exists():
        with open(routing_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Minimal fallback
    return {
        "extension_to_type": {".txt": "script", ".yml": "localization"},
        "type_to_envelope": {"script": "E_SCRIPT", "localization": "E_LOC", "unknown": "E_SKIP"},
        "skip_extensions": [".dds", ".png", ".jpg", ".wav", ".mp3", ".mesh", ".anim"]
    }


def get_envelope_for_file(relpath: str, routing_table: dict) -> str:
    """Route file to envelope based on path rules first, then extension."""
    from pathlib import Path as P
    relpath_normalized = relpath.replace('\\', '/').lower()
    
    # Check path rules first (lookups, skip folders)
    for rule in routing_table.get('path_rules', []):
        if rule['match'].lower() in relpath_normalized:
            return rule['envelope']
    
    # Then check skip_extensions
    ext = P(relpath).suffix.lower()
    if ext in routing_table.get('skip_extensions', []):
        return 'E_SKIP'
    
    # Finally, extension-based routing
    ext_to_type = routing_table.get('extension_to_type', {})
    type_to_env = routing_table.get('type_to_envelope', {})
    
    file_type = ext_to_type.get(ext, 'unknown')
    return type_to_env.get(file_type, 'E_SKIP')


def get_file_type(relpath: str, routing_table: dict) -> str:
    """Get file type from extension."""
    ext = Path(relpath).suffix.lower()
    return routing_table.get('extension_to_type', {}).get(ext, 'unknown')


def compute_file_fingerprint(filepath: Path) -> Optional[FileRecord]:
    """
    Compute complete fingerprint for a file.
    
    Returns FileRecord with mtime, size, and hash.
    Returns None if file cannot be read.
    """
    try:
        stat = filepath.stat()
        
        # Compute hash
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        
        relpath = ""  # Will be set by caller
        return FileRecord(
            relpath=relpath,
            mtime=stat.st_mtime,
            size=stat.st_size,
            hash=h.hexdigest()
        )
    except (OSError, IOError):
        return None


def compute_file_hash(filepath: Path) -> Optional[str]:
    """Compute SHA256 of a file. Returns None on read error."""
    try:
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, IOError):
        return None


def enumerate_files_stat(
    root_path: Path,
    resume_after: Optional[str] = None,
) -> Iterator[tuple]:
    """
    Enumerate files with stat only (relpath, filepath, mtime, size) — no hash.

    Used by discovery's mtime-gated path: hash is only computed when mtime or
    size differs from the cached DB value, making re-discovery cheap for large
    playsets with few real changes.
    """
    SKIP_DIRS = {'.git', '.svn', '.hg', '.vscode', '.idea', '__pycache__', 'node_modules', '.github'}

    all_files: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            filepath = Path(dirpath) / filename
            relpath = str(filepath.relative_to(root_path)).replace('\\', '/')
            all_files.append((relpath, filepath))

    all_files.sort(key=lambda x: x[0])

    past_resume = resume_after is None
    for relpath, filepath in all_files:
        if not past_resume:
            if relpath == resume_after:
                past_resume = True
            continue
        try:
            stat = filepath.stat()
            yield relpath, filepath, stat.st_mtime, stat.st_size
        except (OSError, IOError):
            continue


def enumerate_files(root_path: Path, resume_after: Optional[str] = None) -> Iterator[FileRecord]:
    """
    Enumerate all files under root_path with fingerprints.
    
    Yields FileRecord for each file, sorted by relpath for deterministic resume.
    Skips hidden/infrastructure directories (.git, .vscode, __pycache__, etc.).
    """
    # Directories to never descend into during file discovery
    SKIP_DIRS = {'.git', '.svn', '.hg', '.vscode', '.idea', '__pycache__', 'node_modules', '.github'}
    
    # Collect and sort for deterministic order
    all_files = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune excluded directories IN-PLACE to prevent os.walk from descending
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        
        for filename in filenames:
            filepath = Path(dirpath) / filename
            relpath = str(filepath.relative_to(root_path)).replace('\\', '/')
            all_files.append((relpath, filepath))
    
    all_files.sort(key=lambda x: x[0])
    
    # Resume logic
    past_resume = resume_after is None
    
    for relpath, filepath in all_files:
        if not past_resume:
            if relpath == resume_after:
                past_resume = True
            continue
        
        record = compute_file_fingerprint(filepath)
        if record:
            record.relpath = relpath
            yield record


def playset_scan(
    conn: sqlite3.Connection,
    playset_path: Optional[Path] = None,
) -> dict:
    """Ensure CVIDs and enqueue discovery from canonical Session.mods."""
    from ck3lens.workspace import load_session

    session = load_session(playset_path=playset_path) if playset_path else load_session()
    now = time.time()
    identities = []
    missing = []

    for mod in session.mods:
        if not mod.path.exists():
            missing.append({"name": mod.name, "path": str(mod.path)})
            continue

        name = mod.name
        cvid = _ensure_cvid(
            conn,
            name=name,
            source_path=str(mod.path),
            workshop_id=mod.workshop_id,
        )
        _enqueue_discovery(conn, cvid, now)
        identities.append({
            "name": name,
            "path": str(mod.path),
            "load_order": mod.load_order,
            "cvid": cvid,
        })

    conn.commit()
    return {
        "playset_name": session.playset_name,
        "mods_total": len(session.mods),
        "mods_ready": len(identities),
        "identities": identities,
        "missing": missing,
        "discovery_tasks_enqueued": len(identities),
        "ready": not missing and len(identities) == len(session.mods),
    }


class IdentityConflictError(RuntimeError):
    """Multiple database rows claim one validated source path."""


def _ensure_cvid(
    conn: sqlite3.Connection,
    name: str,
    source_path: str,
    workshop_id: str | None,
) -> str:
    """Get or create a content identity using canonical descriptor metadata.

    Identities match only by their validated source path. Names and workshop
    IDs are mutable metadata on the matched identity.
    """
    source_path = str(Path(source_path))
    rows = conn.execute(
        "SELECT content_version_id, name, source_path, workshop_id "
        "FROM content_versions WHERE source_path = ?",
        (source_path,),
    ).fetchall()

    if len(rows) > 1:
        raise IdentityConflictError(
            f"Multiple content_versions rows claim source path {source_path}. "
            "Path identity must be unique; rebuild or migrate the database."
        )

    if rows:
        row = rows[0]
        cvid, db_name, _, db_workshop_id = row

        updates: dict[str, object] = {}

        if name != db_name:
            updates["name"] = name

        if workshop_id and workshop_id != db_workshop_id:
            updates["workshop_id"] = workshop_id

        if updates:
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE content_versions SET {assignments} "
                "WHERE content_version_id = ?",
                (*updates.values(), cvid),
            )
            conn.commit()
        return cvid

    # Not found: create new row with provisional hash (finalized after discovery)
    seed = f"pending|{name}|{source_path}|{workshop_id or ''}"
    content_root_hash = hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]

    for _ in range(5):
        cvid = new_cvid()
        try:
            conn.execute("""
                INSERT INTO content_versions (
                    content_version_id, name, source_path, workshop_id, content_root_hash
                ) VALUES (?, ?, ?, ?, ?)
            """, (cvid, name, source_path, workshop_id, content_root_hash))
        except sqlite3.IntegrityError:
            collision = conn.execute(
                "SELECT 1 FROM content_versions WHERE content_version_id = ?",
                (cvid,),
            ).fetchone()
            if collision is not None:
                continue
            raise
        conn.commit()
        return cvid

    raise RuntimeError("Could not allocate a unique content-version identity")


def _enqueue_discovery(conn: sqlite3.Connection, cvid: str, now: float) -> None:
    """Enqueue a discovery task for cvid (idempotent)."""
    conn.execute("""
        INSERT INTO discovery_queue (content_version_id, status, created_at)
        VALUES (?, 'pending', ?)
        ON CONFLICT (content_version_id) DO NOTHING
    """, (cvid, now))


class IncrementalDiscovery:
    """
    Crash-safe incremental file discovery.
    
    Processes one discovery_queue task at a time:
    1. Claim task with lease (cvid only)
    2. Resolve root path via canonical join
    3. Enumerate files with fingerprints
    4. Upsert files and build_queue rows
    5. Commit frequently
    6. Mark complete
    """
    
    def __init__(self, conn: sqlite3.Connection, worker_id: Optional[str] = None):
        self.conn = conn
        self.worker_id = worker_id or f"worker-{os.getpid()}"
        self.routing_table = get_routing_table()
        self.router = None
        try:
            from qbuilder.routing import get_router
            self.router = get_router()
        except Exception:
            pass  # Fall back to v1 functions if v2 Router fails
    
    def _get_display_name(self, cvid: str) -> str:
        """Get display name for a content version."""
        row = self.conn.execute(
            "SELECT name FROM content_versions WHERE content_version_id = ?",
            (cvid,)
        ).fetchone()
        return row[0] if row and row[0] else f"cvid={cvid}"
    
    def claim_task(self) -> Optional[dict]:
        """Claim next available discovery task."""
        now = time.time()
        lease_until = now + DISCOVERY_LEASE_SECONDS
        
        cursor = self.conn.execute("""
            UPDATE discovery_queue
            SET status = 'processing',
                lease_expires_at = ?,
                lease_holder = ?,
                started_at = COALESCE(started_at, ?)
            WHERE discovery_id = (
                SELECT discovery_id FROM discovery_queue
                WHERE status = 'pending'
                   OR (status = 'processing' AND lease_expires_at < ?)
                ORDER BY discovery_id
                LIMIT 1
            )
            RETURNING discovery_id, content_version_id, last_path_processed
        """, (lease_until, self.worker_id, now, now))
        
        row = cursor.fetchone()
        self.conn.commit()
        
        if row:
            return {
                'discovery_id': row[0],
                'cvid': row[1],
                'last_path_processed': row[2]
            }
        return None
    
    def process_task(self, task: dict) -> dict:
        """
        Process discovery task: enumerate files and enqueue build work.

        Uses mtime+size gating: files whose stat matches the DB cache skip
        SHA256 recomputation. Files no longer present on disk are hard-deleted
        from the DB (build_queue → asts → files, with cascade to symbols/refs).

        Returns summary with file count.
        """
        discovery_id = task['discovery_id']
        cvid = task['cvid']
        resume_after = task.get('last_path_processed')

        # Resolve root path from content_versions.source_path
        row = self.conn.execute("""
            SELECT cv.source_path
            FROM content_versions cv
            WHERE cv.content_version_id = ?
        """, (cvid,)).fetchone()

        if not row or not row[0]:
            self._mark_error(discovery_id, f"No source_path for cvid={cvid}")
            return {'error': f"No source_path for cvid={cvid}"}

        root_path = Path(row[0])

        if not root_path or not root_path.exists():
            self._mark_error(discovery_id, f"Root path does not exist: {root_path}")
            return {'error': f"Root path does not exist: {root_path}"}

        # Get display name for logging
        display_name = self._get_display_name(cvid)
        print(f"Processing: {display_name} ({root_path})")

        # BOM repair is only safe for writable user mod paths
        is_local_mod = root_path.is_relative_to(_LOCAL_MOD_ROOT)

        # Load fingerprint cache for mtime+size gating (avoids re-hashing unchanged files)
        fp_cache = self._load_fingerprint_cache(cvid)
        cache_hits = 0

        file_count = 0
        total_size = 0
        seen_relpaths: set[str] = set()
        batch: list[FileRecord] = []
        manifest_pairs: list[tuple[str, str]] = []

        for relpath, filepath, mtime, size in enumerate_files_stat(root_path, resume_after):
            seen_relpaths.add(relpath)
            total_size += size

            raw_bytes: bytes | None = None

            # Gate: reuse cached hash if mtime and size are unchanged
            cached = fp_cache.get(relpath)
            if cached and cached[0] == mtime and cached[1] == size:
                file_hash = cached[2]
                cache_hits += 1

                # Backfill missing blob rows for legacy databases that indexed files without file_contents.
                if not self._content_exists(file_hash):
                    try:
                        raw_bytes = filepath.read_bytes()
                        if hashlib.sha256(raw_bytes).hexdigest() != file_hash:
                            raw_bytes = None
                    except (OSError, IOError):
                        raw_bytes = None
            else:
                # For new/changed .txt files in user_docs mods, repair BOM artifacts
                # before hashing so the clean content is what gets indexed.
                if is_local_mod and filepath.suffix.lower() == '.txt':
                    _repair_encoding_if_needed(filepath)
                try:
                    raw_bytes = filepath.read_bytes()
                except (OSError, IOError):
                    continue  # Unreadable file; skip silently

                file_hash = hashlib.sha256(raw_bytes).hexdigest()

            manifest_pairs.append((relpath, file_hash))
            batch.append(FileRecord(relpath=relpath, mtime=mtime, size=size, hash=file_hash, raw_bytes=raw_bytes))
            file_count += 1

            if len(batch) >= COMMIT_BATCH_SIZE:
                self._commit_batch(cvid, batch, relpath, discovery_id)
                batch = []
                self._renew_lease(discovery_id)

        # Final batch
        if batch:
            self._commit_batch(cvid, batch, batch[-1].relpath, discovery_id)

        # Stale file reconciliation: hard-delete files no longer on disk
        stale_deleted = self._delete_stale_files(cvid, seen_relpaths)

        # Finalize content version metadata from discovered manifest
        content_root_hash = self._compute_manifest_hash(manifest_pairs)
        self._finalize_content_version(cvid, content_root_hash, file_count, total_size)

        # Mark complete
        now = time.time()
        self.conn.execute("""
            UPDATE discovery_queue
            SET status = 'completed', completed_at = ?
            WHERE discovery_id = ?
        """, (now, discovery_id))
        self.conn.commit()

        print(f"  Discovered: {file_count} files "
              f"({cache_hits} hash cache hits, {stale_deleted} stale removed)")
        return {
            'cvid': cvid,
            'file_count': file_count,
            'stale_deleted': stale_deleted,
            'content_root_hash': content_root_hash,
        }
    
    def _load_fingerprint_cache(self, cvid: str) -> dict:
        """
        Load {relpath: (mtime, size, hash)} from DB for the given cvid.

        Used to gate SHA256 computation: if mtime+size match, the file
        content has not changed and the stored hash can be reused.
        """
        rows = self.conn.execute(
            """
            SELECT relpath, file_mtime, file_size, file_hash
            FROM files
            WHERE content_version_id = ?
              AND file_mtime IS NOT NULL
              AND file_size IS NOT NULL
              AND file_hash IS NOT NULL
            """,
            (cvid,),
        ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _delete_stale_files(self, cvid: str, seen_relpaths: set) -> int:
        """
        Hard-delete file records for a cvid that were not seen on disk.

        Cascades:
          build_queue rows  → deleted explicitly (no FK cascade)
          asts rows         → deleted explicitly; symbols/refs cascade from asts
          files row         → deleted last

        Returns the number of file rows removed.
        """
        rows = self.conn.execute(
            "SELECT file_id, relpath FROM files WHERE content_version_id = ?",
            (cvid,),
        ).fetchall()

        deleted = 0
        for file_id, relpath in rows:
            if relpath not in seen_relpaths:
                self.conn.execute("DELETE FROM build_queue WHERE file_id = ?", (file_id,))
                self.conn.execute("DELETE FROM asts WHERE file_id = ?", (file_id,))
                self.conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
                deleted += 1

        if deleted:
            self.conn.commit()

        return deleted

    def _content_exists(self, content_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM file_contents WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        return row is not None

    def _compute_manifest_hash(self, pairs: list[tuple[str, str]]) -> str:
        h = hashlib.sha256()
        for relpath, file_hash in sorted(pairs, key=lambda p: p[0]):
            h.update(relpath.encode('utf-8'))
            h.update(b'\0')
            h.update(file_hash.encode('ascii'))
            h.update(b'\n')
        return h.hexdigest()[:32]

    def _finalize_content_version(self, cvid: str, content_root_hash: str, file_count: int, total_size: int) -> None:
        existing = self.conn.execute(
            "SELECT content_version_id FROM content_versions WHERE content_root_hash = ?",
            (content_root_hash,),
        ).fetchone()

        # Reuse existing hash owner if this cvid already finalized before; otherwise keep provisional hash.
        if existing and existing[0] != cvid:
            self.conn.execute(
                "UPDATE content_versions SET file_count = ?, total_size = ? WHERE content_version_id = ?",
                (file_count, total_size, cvid),
            )
            return

        self.conn.execute(
            """
            UPDATE content_versions
            SET content_root_hash = ?,
                file_count = ?,
                total_size = ?,
                source_mtime = datetime('now')
            WHERE content_version_id = ?
            """,
            (content_root_hash, file_count, total_size, cvid),
        )

    def _commit_batch(self, cvid: str, batch: list[FileRecord],
                      last_path: str, discovery_id: int) -> None:
        """
        Commit a batch of files atomically.
        
        For each file:
        1. Upsert into files with fingerprint
        2. Route to envelope
        3. Upsert into build_queue with fingerprint binding
        """
        now = time.time()
        
        for record in batch:
            # Ensure file blob exists for content-addressed historical retrieval.
            if record.raw_bytes is not None:
                store_file_content(self.conn, record.raw_bytes, content_hash=record.hash)
            elif not self._content_exists(record.hash):
                # Should be rare (legacy cache edge case), but keep DB consistent.
                continue

            # Route file via v2 Router (or v1 fallback)
            if self.router:
                rr = self.router.route(record.relpath)
                file_type = rr.file_type
                envelope = rr.envelope
            else:
                file_type = get_file_type(record.relpath, self.routing_table)
                envelope = get_envelope_for_file(record.relpath, self.routing_table)

            # Upsert file with fingerprint
            cursor = self.conn.execute("""
                INSERT INTO files (content_version_id, relpath, content_hash, 
                                   file_type, file_mtime, file_size, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (content_version_id, relpath) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    file_type = excluded.file_type,
                    file_mtime = excluded.file_mtime,
                    file_size = excluded.file_size,
                    file_hash = excluded.file_hash
                RETURNING file_id
            """, (cvid, record.relpath, record.hash, file_type,
                  record.mtime, record.size, record.hash))
            
            file_id = cursor.fetchone()[0]
            
            # Skip queueing files with no work to do
            if envelope == 'SKIP' or envelope == 'E_SKIP':
                continue
            
            # Upsert build_queue with fingerprint binding (priority=0 for batch discovery)
            self.conn.execute("""
                INSERT INTO build_queue 
                    (file_id, envelope, priority, work_file_mtime, work_file_size, 
                     work_file_hash, status, created_at)
                VALUES (?, ?, 0, ?, ?, ?, 'pending', ?)
                ON CONFLICT (file_id, envelope, work_file_mtime, work_file_size, 
                             COALESCE(work_file_hash, '')) 
                DO NOTHING
            """, (file_id, envelope, record.mtime, record.size, record.hash, now))
        
        # Update progress
        self.conn.execute("""
            UPDATE discovery_queue
            SET last_path_processed = ?
            WHERE discovery_id = ?
        """, (last_path, discovery_id))
        
        self.conn.commit()
    
    def _renew_lease(self, discovery_id: int) -> None:
        """Renew lease to prevent timeout."""
        lease_until = time.time() + DISCOVERY_LEASE_SECONDS
        self.conn.execute("""
            UPDATE discovery_queue SET lease_expires_at = ?
            WHERE discovery_id = ?
        """, (lease_until, discovery_id))
        self.conn.commit()
    
    def _mark_error(self, discovery_id: int, message: str) -> None:
        """Mark discovery task as error."""
        self.conn.execute("""
            UPDATE discovery_queue
            SET status = 'error', error_message = ?
            WHERE discovery_id = ?
        """, (message, discovery_id))
        self.conn.commit()


def run_discovery(conn: sqlite3.Connection, max_tasks: Optional[int] = None) -> dict:
    """
    Run discovery worker until no more tasks.
    
    Args:
        conn: Database connection
        max_tasks: Maximum tasks to process (None = unlimited)
    
    Returns summary of work done.
    """
    discovery = IncrementalDiscovery(conn)
    
    tasks_processed = 0
    total_files = 0
    
    while True:
        if max_tasks and tasks_processed >= max_tasks:
            break
        
        task = discovery.claim_task()
        if not task:
            break
        
        result = discovery.process_task(task)
        tasks_processed += 1
        
        if 'file_count' in result:
            total_files += result['file_count']
    
    return {
        'tasks_processed': tasks_processed,
        'files_discovered': total_files,
    }
