"""Reading .mse banks: the string scan, sample records, preset names, zone
blocks, and where each record lands on disk.

Record layout (little-endian), confirmed across every record of the banks
it was developed against:

    <original path>\\0  bytes_per_sample:u32  sample_rate:u32  channels:u32
    unknown:u32  frame_count:u64  <raw PCM>  <zone block, 608 bytes>

The record's own path sits immediately before its audio, so its parent
folder ("000-Drum Kit") is the authoritative grouping. Preset display names
are separate heading strings ("01-Drum Kit"), but a bank also holds tens of
thousands of printable byte runs from PCM that look like strings. Headings
are therefore joined to folders on their *stem* (leading "NN-" stripped,
lowercased): junk such as "3-4" matches no folder and drops out by itself.
Do not identify headings by regex - that was tried and attributed badly.
"""

import contextlib
import hashlib
import mmap
import os
import re
import struct

from .util import HASH_BYTES, find_files, progress

MAGIC = b"MSE "
ILLEGAL_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
AUDIO_RE = re.compile(rb"\.(wav|flac|aif|aiff|ogg|mp3)\x00$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^\d{1,4}\s*-\s*")
MIN_STRING = 3

FIELDS = struct.Struct("<IIIIQ")   # bytes/sample, rate, channels, unknown, frames

MIN_RATE, MAX_RATE = 1000, 384000
MAX_CHANNELS = 8
MAX_BYTES_PER_SAMPLE = 8

_STRING_RES = {}


# --------------------------------------------------------------------------
# Strings and paths

def path_parts(text):
    return [p for p in re.split(r"[\\/]+", text.strip())
            if p not in ("", ".", "..")]


def folder_of(text):
    """The folder containing the sample, e.g. '000-Drum Kit'."""
    parts = path_parts(text)
    return parts[-2] if len(parts) >= 2 else ""


def stem_of(name):
    """Strip any leading 'NN-' and normalise, so '000-Drum Kit' == '01-Drum Kit'."""
    return PREFIX_RE.sub("", name).strip().lower()


@contextlib.contextmanager
def mapped(path):
    """Read-only mmap of a whole file; b"" for an empty one (mmap refuses
    zero-length files). Nothing is read into RAM up front."""
    with open(path, "rb") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            yield b""
            return
        view = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            yield view
        finally:
            view.close()


def scan_strings(view, min_len=MIN_STRING):
    """Yield (offset, text, is_audio_path) for every printable NUL-terminated
    run of at least `min_len` characters. C-speed regex over the mapping."""
    min_len = max(1, min_len)
    pattern = _STRING_RES.get(min_len)
    if pattern is None:
        pattern = _STRING_RES[min_len] = re.compile(
            rb"[\x20-\x7e]{%d,}\x00" % min_len)
    for match in pattern.finditer(view):
        raw = match.group(0)
        yield (match.start(), raw[:-1].decode("ascii", "replace"),
               bool(AUDIO_RE.search(raw)))


def each_mse(mse_dir):
    return find_files(mse_dir, ".mse")


# --------------------------------------------------------------------------
# Records and presets

def resolve_presets(records, headings):
    """Map each sample folder to a display name taken from the headings.

    A heading is accepted only if its stem matches a folder stem, which
    discards printable noise automatically. Where several headings share a
    stem, the one nearest *before* the folder's first sample wins, matching
    the observed layout of heading-then-samples.
    """
    first_seen = {}
    for rec in records:
        folder = rec["folder"]
        if folder and folder not in first_seen:
            first_seen[folder] = rec["path_offset"]

    by_stem = {}
    for offset, text in headings:
        by_stem.setdefault(stem_of(text), []).append((offset, text))

    names = {}
    for folder, folder_offset in first_seen.items():
        candidates = by_stem.get(stem_of(folder), [])
        before = [c for c in candidates if c[0] < folder_offset]
        pick = before[-1] if before else (candidates[0] if candidates else None)
        names[folder] = pick[1] if pick else ""
    return names


def parse_records(path, want_hash=True):
    """Parse every sample record in one .mse. Returns (info, records).

    A record with any entry in "problems" is not audio (for example the bare
    filename reference seen in real banks) and is skipped by every consumer.
    """
    size = os.path.getsize(path)
    records, headings, paths = [], [], []
    with mapped(path) as view:
        magic_ok = view[:4] == MAGIC
        for offset, text, is_audio in scan_strings(view):
            if is_audio:
                paths.append((offset, text))
            else:
                headings.append((offset, text))

        next_path = [p[0] for p in paths[1:]] + [size]
        for index, (offset, text) in enumerate(paths):
            after = offset + len(text) + 1
            if after + FIELDS.size > size:
                continue
            bps, rate, channels, unknown, frames = FIELDS.unpack_from(
                view, after)
            pcm_at = after + FIELDS.size
            pcm_len = frames * channels * bps
            parts = path_parts(text)
            problems = []
            if len(parts) < 2:
                problems.append("bare filename, not a record")
            if not 1 <= bps <= MAX_BYTES_PER_SAMPLE:
                problems.append("bytes_per_sample=%d" % bps)
            if not MIN_RATE <= rate <= MAX_RATE:
                problems.append("rate=%d" % rate)
            if not 1 <= channels <= MAX_CHANNELS:
                problems.append("channels=%d" % channels)
            if frames == 0:
                problems.append("frames=0")
            if pcm_at + pcm_len > size:
                problems.append("pcm overruns file")
            elif pcm_at + pcm_len > next_path[index]:
                problems.append("pcm overruns next record")

            digest = ""
            if want_hash and not problems and pcm_len:
                digest = hashlib.sha256(
                    view[pcm_at:pcm_at + min(HASH_BYTES, pcm_len)]
                ).hexdigest()

            records.append({
                "mse_file": os.path.basename(path),
                "original_path": text,
                "path_offset": offset,
                "folder": folder_of(text),
                "preset": "",
                "sample_name": parts[-1] if parts else text,
                "bytes_per_sample": bps, "sample_rate": rate,
                "channels": channels, "unknown": unknown, "frames": frames,
                "pcm_offset": pcm_at, "pcm_length": pcm_len,
                "pcm_sha256_64k": digest,
                "problems": "; ".join(problems),
            })

    good = [r for r in records if not r["problems"]]
    names = resolve_presets(good, headings)
    for rec in records:
        rec["preset"] = names.get(rec["folder"], "") or rec["folder"]
        rec["named"] = bool(names.get(rec["folder"]))

    info = {"path": path, "name": os.path.basename(path), "size": size,
            "magic_ok": magic_ok, "records": len(records),
            "clean": len(good), "folders": names,
            "headings": len(headings)}
    return info, records


def plan_destinations(targets, out_dir, group_by):
    """Decide where every clean record belongs on disk.

    Shared by `extract`, `audit` and `sfz`, so the audit checks exactly the
    paths the extraction wrote, collision suffixes included.
    """
    jobs, groups, total_bytes = [], {}, 0
    for path in targets:
        progress("indexing %s ..." % os.path.basename(path))
        _info, records = parse_records(path, want_hash=False)
        for rec in records:
            if rec["problems"]:
                continue
            group = (rec["preset"] if group_by == "preset"
                     else rec["folder"]) or "_ungrouped"
            group = ILLEGAL_RE.sub("_", group).strip().rstrip(". ") \
                or "_ungrouped"
            name = ILLEGAL_RE.sub("_", rec["sample_name"]) or "sample.wav"
            if not name.lower().endswith(".wav"):
                name += ".wav"
            dest = os.path.join(out_dir, group, name)
            seen = groups.setdefault(group, set())
            stem, ext = os.path.splitext(name)
            index = 2
            while dest in seen:
                dest = os.path.join(out_dir, group,
                                    "%s (%d)%s" % (stem, index, ext))
                index += 1
            seen.add(dest)
            jobs.append((path, dest, rec))
            total_bytes += rec["pcm_length"] + 44
    return jobs, groups, total_bytes


# --------------------------------------------------------------------------
# Zone blocks
#
# The 608-byte block after each record's PCM. Only the first 88 bytes carry
# data; the rest is zero.
#
#   +0  u32 root_note      +4  u32 key_low      +8  u32 key_high
#   +12 u32 velocity_high  +16 u32 velocity_low +20 u32 loop_mode
#   +24 u64 sample_start   +32 u64 sample_end
#   +40 u64 loop_start     +48 u64 loop_end
#   +76 f32 param76 (purpose unknown)           +84 u32 fixed_pitch
#
# +0 is the root note, confirmed by measuring the audio pitch of two pitched
# presets. fixed_pitch == 1 marks a drum / one-shot zone: it plays at natural
# pitch on any key and its root_note is an unused counter - applying it would
# transpose every drum up 36 semitones.

ZONE = struct.Struct("<6I4Q5IfII")     # 88 bytes
ZONE_BLOCK_BYTES = 608
ZONE_FIELDS = ("root_note", "key_low", "key_high", "velocity_high",
               "velocity_low", "loop_mode", "sample_start", "sample_end",
               "loop_start", "loop_end",
               "_pad0", "_pad1", "_pad2", "_pad3", "_pad4",
               "param76", "_pad5", "fixed_pitch")


def parse_zone(block):
    """Decode the leading 88 bytes of a zone block into named fields."""
    if len(block) < ZONE.size:
        return None
    zone = dict(zip(ZONE_FIELDS, ZONE.unpack_from(block, 0)))
    return {key: value for key, value in zone.items()
            if not key.startswith("_")}


def effective_root(zone, policy="auto"):
    """The note at which this sample plays untransposed.

    A fixed-pitch (drum) zone must not be transposed, so its root is its own
    key. Otherwise the stored root_note is used. The non-auto policies remain
    only as manual overrides.
    """
    if zone.get("fixed_pitch"):
        return zone["key_low"]
    if policy == "keyhigh":
        return zone["key_high"]
    if policy == "center":
        return (zone["key_low"] + zone["key_high"]) // 2
    if policy == "keylow":
        return zone["key_low"]
    return zone["root_note"]


def zone_loops(zone):
    """The zone's loop as smpl-style [(type, start, end)]; [] when unlooped.
    The one rule used by smpl chunks, SFZ regions and the audit alike."""
    if zone["loop_mode"] and zone["loop_end"] > zone["loop_start"]:
        return [(0, zone["loop_start"], zone["loop_end"])]   # 0 = forward
    return []


def zone_problems(zone, frames):
    """Constraint violations. An empty list means the layout holds."""
    bad = []
    if not 0 <= zone["root_note"] <= 127:
        bad.append("root_note=%d" % zone["root_note"])
    if zone["fixed_pitch"] not in (0, 1):
        bad.append("fixed_pitch=%d" % zone["fixed_pitch"])
    if not 0 <= zone["key_low"] <= 127:
        bad.append("key_low=%d" % zone["key_low"])
    if not 0 <= zone["key_high"] <= 127:
        bad.append("key_high=%d" % zone["key_high"])
    if zone["key_low"] > zone["key_high"]:
        bad.append("key_low>key_high")
    if not 0 <= zone["velocity_low"] <= 127:
        bad.append("vel_low=%d" % zone["velocity_low"])
    if not 0 <= zone["velocity_high"] <= 127:
        bad.append("vel_high=%d" % zone["velocity_high"])
    if zone["velocity_low"] > zone["velocity_high"]:
        bad.append("vel_low>vel_high")
    if zone["loop_mode"] not in (0, 1, 2, 3):
        bad.append("loop_mode=%d" % zone["loop_mode"])
    if zone["sample_start"] > zone["sample_end"]:
        bad.append("sample_start>sample_end")
    if zone["sample_end"] >= max(frames, 1) + 1:
        bad.append("sample_end=%d beyond frames=%d"
                   % (zone["sample_end"], frames))
    if zone["loop_start"] > zone["loop_end"]:
        bad.append("loop_start>loop_end")
    if zone["loop_end"] >= max(frames, 1) + 1:
        bad.append("loop_end=%d beyond frames=%d"
                   % (zone["loop_end"], frames))
    return bad


def read_zone_checked(handle, rec):
    """Read a record's zone from an open bank. Returns (zone, problems);
    zone is None when the file ends before a full zone."""
    handle.seek(rec["pcm_offset"] + rec["pcm_length"])
    zone = parse_zone(handle.read(ZONE.size))
    if zone is None:
        return None, None
    return zone, zone_problems(zone, rec["frames"])


def read_zone(handle, rec):
    """A record's zone, or None if it is missing or violates the layout."""
    zone, problems = read_zone_checked(handle, rec)
    return None if zone is None or problems else zone


def zone_regions(path):
    """Yield (record, zone_start, zone_length) for every clean record.

    The zone block is whatever sits between a record's PCM and the start of
    the next record's path. At preset boundaries that gap also contains the
    next preset's heading string, so those gaps read longer.
    """
    info, records = parse_records(path, want_hash=False)
    ordered = sorted(records, key=lambda r: r["path_offset"])
    following = {}
    for index, rec in enumerate(ordered):
        following[rec["path_offset"]] = (
            ordered[index + 1]["path_offset"] if index + 1 < len(ordered)
            else info["size"])
    for rec in ordered:
        if rec["problems"]:
            continue
        start = rec["pcm_offset"] + rec["pcm_length"]
        yield rec, start, max(0, following[rec["path_offset"]] - start)
