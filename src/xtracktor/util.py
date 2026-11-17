"""Shared helpers: progress output, file discovery, CSV, atomic writes and
streamed hashing."""

import csv
import hashlib
import os
import sys

CHUNK = 1 << 20          # streaming read size
HASH_BYTES = 65536       # prefix length used for quick PCM fingerprints


def progress(message):
    """Progress and status lines go to stderr, keeping stdout for results."""
    print(message, file=sys.stderr)


def invocation():
    """How the user ran this tool, for hints such as 'Verify with: ...'."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.lower().endswith(".pyz"):
        return "python3 %s" % os.path.basename(argv0)
    return "python3 -m xtracktor"


def find_files(top, suffix):
    """Every file under `top` whose name ends in `suffix`, case-insensitive.
    Folders in os.walk order, names sorted within each folder."""
    found = []
    for root, _dirs, names in os.walk(top):
        for name in sorted(names):
            if name.lower().endswith(suffix):
                found.append(os.path.join(root, name))
    return found


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields,
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_atomic(dest, produce, mode="wb", encoding=None):
    """Write `dest` under a temporary name and rename it only when complete.

    `produce(handle)` writes the content. On any failure - an I/O error, a
    full disk, Ctrl-C - the temporary file is removed and the exception
    re-raised, so a half-written file never appears under its real name.
    """
    temp = dest + ".part"
    try:
        with open(temp, mode, encoding=encoding) as handle:
            produce(handle)
        os.replace(temp, dest)
    except BaseException:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def sha256_range(handle, offset, length):
    """sha256 of `length` bytes from `offset`, streamed.

    Returns (hex digest, bytes actually read); the count is short when the
    file ends early, which callers use to detect truncation.
    """
    handle.seek(offset)
    digest = hashlib.sha256()
    remaining, got = length, 0
    while remaining:
        chunk = handle.read(min(CHUNK, remaining))
        if not chunk:
            break
        digest.update(chunk)
        got += len(chunk)
        remaining -= len(chunk)
    return digest.hexdigest(), got
