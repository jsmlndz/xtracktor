"""WAV files: one RIFF chunk walker and everything built on it - writing a
WAV straight from a bank, smpl chunks, data-chunk digests and structural
health checks."""

import os
import struct

from .util import CHUNK, HASH_BYTES, sha256_range, write_atomic

FMT = struct.Struct("<HHIIHH")   # tag, channels, rate, byte rate, align, bits


def check_riff(handle):
    """Read the 12-byte RIFF/WAVE header or raise ValueError."""
    head = handle.read(12)
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    return head


def walk_chunks(handle):
    """Yield (chunk_id, body_offset, declared_size) for each chunk after the
    RIFF header. Moves by seeking, so a body is only read if the caller reads
    it; the caller may move the handle freely between steps."""
    pos = 12
    while True:
        handle.seek(pos)
        header = handle.read(8)
        if len(header) < 8:
            return
        (size,) = struct.unpack_from("<I", header, 4)
        yield header[0:4], pos + 8, size
        pos += 8 + size + (size & 1)


# --------------------------------------------------------------------------
# Writing

def smpl_chunk(rate, root_note, loops):
    """A WAV `smpl` chunk: root note plus any loop points."""
    sample_period = int(round(1e9 / rate)) if rate else 0
    body = struct.pack("<9I", 0, 0, sample_period, root_note & 0x7F,
                       0, 0, 0, len(loops), 0)
    for index, (loop_type, start, end) in enumerate(loops):
        body += struct.pack("<6I", index, loop_type, start, end, 0, 0)
    return b"smpl" + struct.pack("<I", len(body)) + body


def write_wav(dest, bank, pcm_at, pcm_len, rate, channels, bits,
              trailing=None):
    """Write a WAV whose data chunk is streamed byte for byte from `bank`.

    `trailing` (e.g. a smpl chunk) is appended after the data. The file is
    written under a temporary name and only renamed into place when complete.
    """
    byte_rate = rate * channels * bits // 8
    block = channels * bits // 8
    fmt = FMT.pack(1, channels, rate, byte_rate, block, bits)
    pad = pcm_len & 1
    extra = trailing or b""
    riff_size = 4 + (8 + len(fmt)) + (8 + pcm_len + pad) + len(extra)

    def produce(out):
        out.write(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE")
        out.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        out.write(b"data" + struct.pack("<I", pcm_len))
        bank.seek(pcm_at)
        remaining = pcm_len
        while remaining:
            chunk = bank.read(min(CHUNK, remaining))
            if not chunk:
                raise IOError("short read from bank at %d" % pcm_at)
            out.write(chunk)
            remaining -= len(chunk)
        if pad:
            out.write(b"\x00")
        if extra:
            out.write(extra)

    write_atomic(dest, produce)


# --------------------------------------------------------------------------
# Reading

def data_digest(path, full=True):
    """sha256 of a WAV's data chunk, streamed.

    Returns (declared data size, hex digest, (rate, channels, bits)). With
    full=False only the first 64 KiB is hashed. The format comes from a fmt
    chunk preceding the data chunk, else (0, 0, 0). A full digest of a data
    chunk that runs past the end of the file raises ValueError.
    """
    with open(path, "rb") as handle:
        check_riff(handle)
        fmt = (0, 0, 0)
        for cid, at, size in walk_chunks(handle):
            if cid == b"data":
                want = size if full else min(HASH_BYTES, size)
                digest, got = sha256_range(handle, at, want)
                if full and got != size:
                    raise ValueError("truncated: data claims %d, read %d"
                                     % (size, got))
                return size, digest, fmt
            if cid == b"fmt ":
                handle.seek(at)
                body = handle.read(min(size, 40))
                if len(body) >= 16:
                    _t, channels, rate, _br, _al, bits = FMT.unpack_from(
                        body, 0)
                    fmt = (rate, channels, bits)
    raise ValueError("no data chunk")


def read_smpl(path):
    """Return (root_note, [(type, start, end)]) from a WAV's smpl chunk, or
    (None, None) when there is no usable one."""
    with open(path, "rb") as handle:
        try:
            check_riff(handle)
        except ValueError:
            return None, None
        for cid, at, size in walk_chunks(handle):
            if cid != b"smpl":
                continue
            handle.seek(at)
            body = handle.read(size)
            if len(body) < 36:
                return None, None
            fields = struct.unpack_from("<9I", body, 0)
            root, count = fields[3], fields[7]
            loops = []
            for index in range(count):
                offset = 36 + index * 24
                if offset + 24 > len(body):
                    break
                _cue, ltype, start, end, _frac, _play = \
                    struct.unpack_from("<6I", body, offset)
                loops.append((ltype, start, end))
            return root, loops
    return None, None


# --------------------------------------------------------------------------
# Health

def inspect_wav(path):
    """Diagnose how a WAV is broken. Returns (verdict, detail, facts).

    Verdicts: ok, TRUNCATED, RIFF-SIZE, NO-DATA, EMPTY, NOT-RIFF, BAD-FMT,
    ODD. Reads only headers, so it is fast over thousands of files.
    """
    size = os.path.getsize(path)
    facts = {"file_size": size, "rate": 0, "channels": 0, "bits": 0,
             "data_size": 0, "data_present": 0}
    if size == 0:
        return "EMPTY", "zero-length file", facts
    with open(path, "rb") as handle:
        head = handle.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            return "NOT-RIFF", "missing RIFF/WAVE signature", facts
        (riff_size,) = struct.unpack_from("<I", head, 4)
        facts["riff_size"] = riff_size
        saw_fmt = False
        for cid, body_at, chunk_size in walk_chunks(handle):
            if cid == b"fmt ":
                handle.seek(body_at)
                body = handle.read(min(chunk_size, 40))
                if len(body) >= 16:
                    tag, channels, rate, _br, _al, bits = FMT.unpack_from(
                        body, 0)
                    facts.update({"rate": rate, "channels": channels,
                                  "bits": bits, "format_tag": tag})
                    saw_fmt = (1 <= channels <= 8 and 1000 <= rate <= 384000
                               and bits in (8, 16, 24, 32, 64))
            elif cid == b"data":
                facts["data_size"] = chunk_size
                facts["data_present"] = max(0, size - body_at)
                if chunk_size > facts["data_present"]:
                    return ("TRUNCATED",
                            "data chunk claims %d bytes, only %d present "
                            "(missing %d)" % (chunk_size,
                                              facts["data_present"],
                                              chunk_size
                                              - facts["data_present"]),
                            facts)
                if not saw_fmt:
                    return "BAD-FMT", "data chunk before a valid fmt", facts
                if riff_size != size - 8:          # RIFF size = file size - 8
                    return ("RIFF-SIZE",
                            "RIFF size %d, expected %d" % (riff_size,
                                                           size - 8), facts)
                return "ok", "", facts
            following = body_at + chunk_size + (chunk_size & 1)
            if following <= body_at:
                return "ODD", "zero or negative chunk advance", facts
            if following > size:
                return ("ODD", "chunk '%s' runs past end of file"
                        % cid.decode("ascii", "replace"), facts)
    if not saw_fmt:
        return "BAD-FMT", "no valid fmt chunk", facts
    return "NO-DATA", "no data chunk found", facts
