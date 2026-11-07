"""Bounded, disposable recording caches. Never edits original video files."""
from pathlib import Path
import os
import re
import time

MIB = 1024 * 1024
SEGMENT = r'(?:\d{8}-\d{6}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-\d+)?)\.mp4'
THUMBNAIL = re.compile(rf'(?P<source>{SEGMENT})(?:\.\d+-\d+\.\d+)?\.jpg')
THUMBNAIL_TEMP = re.compile(rf'\.(?P<source>{SEGMENT})(?:\.\d+-\d+\.\d+)?\.jpg\.\d+\.tmp')
PLAYBACK_NAMES = {'timeline.mp4', 'timeline.tmp.mp4', 'timeline.json', 'timeline.ffconcat'}


def managed_files(root: Path, kind: str) -> list[tuple[Path, os.stat_result]]:
    if root.is_symlink() or not root.is_dir():
        return []
    result = []
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
        for name in files:
            path = Path(parent) / name
            if kind == 'playback':
                eligible = name in PLAYBACK_NAMES and len(path.relative_to(root).parts) in (2, 3)
            else:
                eligible = bool(THUMBNAIL.fullmatch(name) or THUMBNAIL_TEMP.fullmatch(name))
            if not eligible or path.is_symlink():
                continue
            try:
                if path.is_file():
                    result.append((path, path.stat()))
            except OSError:
                continue
    return result


def prune_cache(root: Path, kind: str, byte_limit: int, max_files: int = 2048,
                max_age: int = 172800, source_roots: dict | None = None,
                protected: set | None = None, now: float | None = None) -> dict:
    """LRU eviction of recognized generated files; refuse links and foreign files."""
    now = time.time() if now is None else now
    protected = protected or set()
    files = managed_files(root, kind)
    kept, removed, freed = [], 0, 0
    removed_parents = set()

    def unlink(path, stat):
        nonlocal removed, freed
        try:
            if not path.is_symlink():
                path.unlink()
                removed_parents.add(path.parent)
                removed += 1
                freed += stat.st_size
        except OSError:
            return False
        return True

    for path, stat in files:
        obsolete = kind == 'thumbnail' and bool(THUMBNAIL_TEMP.fullmatch(path.name))
        if kind == 'thumbnail' and source_roots is not None:
            key = path.relative_to(root).parts[0]
            source_root = source_roots.get(key)
            source_name = (THUMBNAIL.fullmatch(path.name) or THUMBNAIL_TEMP.fullmatch(path.name))['source']
            # Unknown/removed camera caches expire by age/budget. Never assume
            # its original recordings were deleted or delete those recordings.
            if source_root is not None:
                source = source_root / source_name
                try:
                    obsolete = obsolete or not source.exists()
                except OSError:
                    pass
        expired = max_age >= 0 and now - stat.st_mtime > max_age
        if kind != 'playback' and path not in protected and (obsolete or expired or byte_limit <= 0):
            if unlink(path, stat):
                continue
        kept.append((path, stat))
    if kind == 'playback':
        # A day MP4 and its index form one disposable cache. Evicting just the
        # small index would leave a large, unusable MP4 behind.
        groups = {}
        for path, stat in kept:
            groups.setdefault(path.parent, []).append((path, stat))
        kept = []
        for group in groups.values():
            touched = max(stat.st_mtime for _, stat in group)
            expired = max_age >= 0 and now - touched > max_age
            protected_group = any(path in protected for path, _ in group)
            if not protected_group and (expired or byte_limit <= 0):
                for path, stat in group:
                    if not unlink(path, stat):
                        kept.append((path, stat))
            else:
                kept.extend(group)
    used = sum(stat.st_size for _, stat in kept)
    count = len(kept)
    candidates = sorted(kept, key=lambda item: item[1].st_mtime)
    if kind == 'playback':
        groups = {}
        for path, stat in candidates:
            groups.setdefault(path.parent, []).append((path, stat))
        candidates = [group for group in groups.values()]
        candidates.sort(key=lambda group: max(stat.st_mtime for _, stat in group))
    else:
        candidates = [[item] for item in candidates]
    for group in candidates:
        if used <= byte_limit and count <= max_files:
            break
        if any(path in protected for path, _ in group):
            continue
        for path, stat in group:
            if unlink(path, stat):
                used -= stat.st_size
                count -= 1
    # Remove empty generated folders only; nonempty foreign folders survive.
    for parent in sorted(removed_parents, key=lambda path: len(path.parts), reverse=True):
        while parent != root and root in parent.parents and not parent.is_symlink():
            try:
                parent.rmdir()
                parent = parent.parent
            except OSError:
                break
    return {'bytes': used, 'files': count, 'removed': removed, 'freed_bytes': freed}


def trim_log(path: Path, limit: int = 4 * MIB, tail: int = MIB) -> int:
    """Truncate in place: an external recorder may keep this inode open."""
    if path.is_symlink():
        return 0
    try:
        if path.stat().st_size <= limit:
            return 0
        with path.open('r+b') as handle:
            before = os.fstat(handle.fileno()).st_size
            if before <= limit:
                return 0
            handle.seek(max(0, before - tail))
            data = handle.read(tail)
            # Discard an incomplete first line, keep the most recent diagnostics.
            data = data.partition(b'\n')[2] if b'\n' in data else data
            handle.seek(0)
            handle.write(data)
            handle.truncate()
        return before - len(data)
    except OSError:
        return 0
