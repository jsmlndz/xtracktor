"""The quirk fixture: a synthetic .mse reproducing every structural quirk
seen in real banks. It is the regression gate. All names are generic.

 - heading/folder prefix mismatch   ("03-Bell Pad" vs "23-Bell Pad")
 - a folder with no numeric prefix  ("Soft Lead")
 - junk headings from PCM           ("3-4", "9-:", "5-6")
 - a bare filename reference that is not a record ("BP000.wav")
 - a sample shared by two presets
 - drum (fixed_pitch=1) and pitched zones, looped and unlooped zones,
   and a velocity-split pair

Pitched samples are generated AT their declared root, so `pitch` validates
the written root notes end to end.

Every byte is deterministic: seeds come from CRC-32, not Python's hash(),
which is salted per run and changed algorithm in Python 3.11. So the fixture,
and everything extracted from it, is identical on any machine and any Python
version, and one reference fingerprint holds everywhere.
"""

import math
import os
import random
import struct
import zlib

JUNK_HEADINGS = [b"3-4", b"9-:", b"5-6", b"7-7", b"0-1", b"2-3", b"4-4`4",
                 b"9-8w8", b"kmjMk#j", b"GMj", b"Y21", b"TT20"]

ZONE_BLOCK = 608    # fixed size observed in real banks

# (display heading, folder on disk, filename prefix, sample count)
KITS = [
    ("01-Drum Kit",      "000-Drum Kit",    "DK", 4),   # 000- vs 01-
    ("03-Bell Pad",      "23-Bell Pad",     "BP", 3),   # 23- vs 03-
    ("23-Soft Lead",     "Soft Lead",       "SL", 2),   # no prefix at all
    ("04-Bass & Pluck",  "24-Bass & Pluck", "BA", 2),   # ampersand
]


def pcm(seed, frames, channels=2, midi=None):
    """Audio for a fixture sample.

    With `midi` the tone is generated AT that note, so `pitch` can verify end
    to end that the written root matches the audio. Otherwise arbitrary.
    """
    rng = random.Random(seed)
    if midi is None:
        freq = 90.0 * (1 + seed % 11)
        harmonics = (1.0,)
        noise = 500
    else:
        freq = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        harmonics = (1.0, 0.5, 0.3, 0.15)
        noise = 120
    out = bytearray()
    for n in range(frames):
        base = 0.0
        for index, amp in enumerate(harmonics, start=1):
            base += amp * math.sin(2 * math.pi * freq * index * n / 44100.0)
        base /= sum(harmonics)
        for _c in range(channels):
            val = int(base * 11000) + rng.randint(-noise, noise)
            out += struct.pack("<h", max(-32768, min(32767, val)))
    return bytes(out)


def plain_wav(path, data, rate=44100, channels=2, bits=16):
    """A minimal WAV, standing in for a third-party extractor's output."""
    fmt = struct.pack("<HHIIHH", 1, channels, rate,
                      rate * channels * bits // 8, channels * bits // 8, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    if len(data) & 1:
        body += b"\x00"
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


def record(win_path, data, rate=44100, channels=2, width=2, rng=None,
           root=36, key_low=36, key_high=36, looped=False, vel=(0, 127),
           fixed_pitch=0):
    """One record plus its 608-byte zone block, laid out like real banks."""
    frames = len(data) // (channels * width)
    out = win_path.encode("ascii") + b"\x00"
    out += struct.pack("<IIIIQ", width, rate, channels, 0, frames)
    out += data
    loop_start = (frames // 4) if looped else 0
    zone = bytearray(ZONE_BLOCK)
    struct.pack_into("<6I4Q", zone, 0, root, key_low, key_high,
                     vel[1], vel[0], 2 if looped else 0,
                     0, frames - 1, loop_start, frames - 1)
    struct.pack_into("<f", zone, 76, 100.0)
    struct.pack_into("<I", zone, 84, 1 if fixed_pitch else 0)
    out += bytes(zone)
    if rng and rng.random() < 0.7:          # junk between records
        out += rng.choice(JUNK_HEADINGS) + b"\x00"
    return out


def build_bank(path, bank):
    """Write the fixture bank. Returns [(heading, folder, name, pcm)]."""
    rng = random.Random(11)
    head = b"MSE " + struct.pack("<I", 231) + b"\x01"
    head += bank.encode() + b"\x00" + b"First Preset\x00"
    head += b"\x00" * (0x272 - len(head))
    head += b"GUI-01-640x480.jpg\x00JFIF\x00Ducky\x00Adobe\x00"
    for j in JUNK_HEADINGS[:6]:
        head += j + b"\x00" + bytes(rng.randrange(256) for _ in range(32))
    head += b"0123456789abcdef0123456789abcdef\x00"

    body, produced = b"", []
    shared = pcm(4242, 700)
    for heading, folder, prefix, count in KITS:
        body += heading.encode() + b"\x00"
        drum = "Kit" in heading                 # drum kits are fixed-pitch
        for i in range(count):
            name = "%s%03d.wav" % (prefix, i)
            win = "..\\..\\..\\..\\..\\%s\\%s\\%s" % (bank, folder, name)
            # vary the zone shape: some looped, one velocity-split pair
            vel = (104, 127) if i == 1 else (0, 103) if i == 2 else (0, 127)
            seed = zlib.crc32(("%s|%d" % (folder, i)).encode()) % 9999
            if drum:
                # one key per pad, root is an unused ascending counter
                kwargs = dict(root=i, key_low=36 + i, key_high=36 + i,
                              fixed_pitch=1)
                data = pcm(seed, 4800)
            else:
                # multisampled ladder: root sits 2 above key_low, first zone
                # stretched down to 0
                root = 36 + i * 6
                kwargs = dict(root=root, key_low=0 if i == 0 else root - 2,
                              key_high=root + 3, fixed_pitch=0)
                data = pcm(seed, 9600, midi=root)
            body += record(win, data, rng=rng, looped=(i % 3 == 0), vel=vel,
                           **kwargs)
            produced.append((heading, folder, name, data))
        # a bare filename reference that is not a record, as seen in real banks
        if prefix == "BP":
            body += b"BP000.wav\x00" + bytes(
                rng.randrange(256) for _ in range(28))
    # same audio in two presets
    for heading, folder, prefix in (("01-Drum Kit", "000-Drum Kit", "DK"),
                                    ("23-Soft Lead", "Soft Lead", "SL")):
        win = "..\\..\\..\\..\\..\\%s\\%s\\%sSHARED.wav" % (bank, folder,
                                                            prefix)
        body += record(win, shared, rng=rng)
    produced.append(("01-Drum Kit", "000-Drum Kit", "DKSHARED.wav", shared))

    with open(path, "wb") as fh:
        fh.write(head + body)
    return produced


def make_fixture(out_dir):
    """Build banks/Test Bank-Q.mse plus extracted/ WAVs (as a third-party
    extractor would name them, with one stray). Returns (records, wavs)."""
    mse_dir = os.path.join(out_dir, "banks")
    wav_dir = os.path.join(out_dir, "extracted")
    os.makedirs(mse_dir, exist_ok=True)
    os.makedirs(wav_dir, exist_ok=True)
    made = build_bank(os.path.join(mse_dir, "Test Bank-Q.mse"), "TEST-Q")
    for n, (_h, _f, _name, data) in enumerate(made, 1):
        plain_wav(os.path.join(wav_dir, "sample_%03d.wav" % n), data)
    plain_wav(os.path.join(wav_dir, "stray.wav"), pcm(777777, 250, 1),
              22050, 1)
    return len(made), len(made) + 1
