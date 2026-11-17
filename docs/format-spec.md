# The `.mse` sample-bank format

**Scope:** documented from the bank files themselves, for interoperability. No disassembly was needed: the format yielded to a string scan and a `struct` unpack.
**Confirmed against:** three banks of a commercial sample library, about 2.4 GB in total.
**Starting point:** the per-sample record layout documented by [MSE-Extract-Tool](https://github.com/robmurru/MSE-Extract-Tool) (MIT). It was confirmed against those banks and extended here with the file header, zone data, preset grouping and root-note rules.

All findings below were confirmed from the files, not inferred. Example names (`01-Drum Kit`, `DK000.wav`, …) are generic, taken from xtracktor's synthetic test bank, which reproduces the same structure.

---

## File header

| Offset | Bytes | Meaning |
|---|---|---|
| `0x00` | `4D 53 45 20` | Magic: ASCII `"MSE "` |
| `0x04` | u32 | `231` — purpose unknown |
| `0x08` | u8 | `0x01` — unknown |
| `0x09` | cstring | Bank name |
| `0x15` (approx.) | cstring | First preset name |
| `0x22`, `0x26` | u32 | `44100` — sample rate, twice |
| `0x2A`–`0x39` | 4× u32 | `1, 1, 1, 1` |
| `0x3A`, `0x3E` | u32 | `32`, `32` — unresolved |
| `0x42` | u32 | `2` — channel count |
| `0x72` | i32 | `-48` — unresolved |
| `0x272` | cstring | Name of an embedded GUI image, e.g. `GUI-01-<w>x<h>.jpg` |
| later in the header | cstring | 32-hex bank ID, one per file, before the first preset |

The two cstrings near the start vary in length, so the offsets after them are approximate where noted.

**Little-endian throughout**, proven twice independently: `44100` as `44 AC 00 00`, `-48` as `D0 FF FF FF`.

## Sample record layout

```
<original path>\0          null-terminated, Windows-style, ends .wav
bytes_per_sample  : u32    observed uniformly 2
sample_rate       : u32    observed uniformly 44100
channels          : u32    observed uniformly 2
unknown           : u32    observed 0
frame_count       : u64
<raw PCM>                  frame_count × channels × bytes_per_sample
<zone block>               fixed 608 bytes, layout below
```

Validated across every record in the three banks. The record's own path sits immediately before its audio, so every sample is self-describing. Paths look like `..\..\..\..\..\<bank>\000-Drum Kit\DK000.wav`.

## Zone block — 608 bytes, fixed

| Offset | Type | Field |
|---|---|---|
| `+0` | u32 | **root_note** |
| `+4` | u32 | key_low |
| `+8` | u32 | key_high |
| `+12` | u32 | velocity_high |
| `+16` | u32 | velocity_low |
| `+20` | u32 | loop_mode (`0` = none, `2` = forward) |
| `+24` | u64 | sample_start |
| `+32` | u64 | sample_end (= `frame_count − 1` in every record) |
| `+40` | u64 | loop_start (`0` whenever loop_mode is 0, in every record) |
| `+48` | u64 | loop_end |
| `+76` | f32 | unknown — differs between presets (values seen: 60.0, 100.0, 150.0) |
| `+84` | u32 | **fixed_pitch** — `1` = drum / one-shot |

Everything else in the 608 bytes is zero. The u64 positions explain why every second dword in `24..55` reads zero.

Gaps larger than 608 bytes occur at preset boundaries, where the gap also carries the next preset's heading string. The small spread in those sizes is heading-name length.

## Root notes — the subtlety that caused a real bug

**`+0` is the root note, not a zone index.** It was first misread as an index, and `key_low` was used as the root instead. The result was multisampled presets playing up to 39 semitones sharp, with an audible pitch jump at every zone boundary.

Two rules:

1. **`fixed_pitch == 1`** (drum / one-shot zones): the sample plays at natural pitch whatever key triggers it. Its `root_note` is an **unused ascending counter**: in the drum kit examined, `key − root` came out to a constant 36, so applying it would transpose every drum up three octaves. **Use `key_low` as the root and emit `pitch_keytrack=0`.**
2. **`fixed_pitch == 0`** (pitched): **use `root_note` directly.**

Pitched presets are laid out as a ladder where `root = key_low + 2`. The exceptions are the lowest zone, whose `key_low` is stretched to 0, and the highest, whose `key_high` is stretched to 125, so the keyboard is fully covered.

**Verified by measuring the audio.** `xtracktor pitch` estimates each sample's fundamental and compares it to the stored root. On two pitched presets (16 samples), `+0` matched the measured pitch exactly. After the fix, every pitched preset reports `delta 0`.

## Preset grouping — two signals

1. **The path's parent folder** (`000-Drum Kit`): authoritative, per sample.
2. **Preset heading strings** (`01-Drum Kit`): the display names.

**Gotcha 1:** the numeric prefixes do *not* match between them. All three of these patterns occur:

| Heading | Folder | Pattern |
|---|---|---|
| `01-Drum Kit` | `000-Drum Kit` | different prefix width |
| `03-Bell Pad` | `23-Bell Pad` | different number |
| `23-Soft Lead` | `Soft Lead` | no prefix at all |

**Gotcha 2:** a bank holds **tens of thousands of printable byte runs from PCM** that look like strings, against only a few hundred real paths. Many mimic headings (`3-4`, `9-:`, `5-6`).

**Solution:** join headings to folders on their *stem* (leading `NN-` stripped, lowercased). Junk matches no folder stem and eliminates itself. This resolved 100% of folders, with none left unnamed. **Do not identify headings by regex.** That was tried and produced badly wrong attribution.

## Audio characteristics

Uniform across every sample examined: **44,100 Hz, 2 channels, 16-bit, uncompressed, unencrypted.**

## Known anomaly

One record in one bank was a bare filename, with no directory and no valid field block. It is a metadata reference, not a sample. xtracktor skips it, and no audio is missing.

## Unresolved fields

None of these affect extraction, pitch or mapping:
- header fields past ~`0x66`
- the `231` at `0x04`
- the `32, 32` pair
- the `-48` at `0x72`
- the per-record `unknown` u32
- the zone block's `+76` float

---

## Results

On the three banks the format was developed against (~2.4 GB):
- **Records:** 2,052 sample records, 2,051 clean. The one skipped record is the bare-filename anomaly.
- **Presets:** 206 preset folders, none without a display name.
- **`audit --zones`:** `COMPLETE AND BYTE-ACCURATE`, 2,051/2,051, all folders reconcile.
- **`wavcheck`:** every file ok.
- **`pitch` on pitched presets:** `delta 0` across the board.
- **In a sampler:** DirectWave (FL Studio) loads the SFZ instruments with correct mapping and pitch.

These results were reproduced by re-extracting the whole library from scratch with `xtracktor`.

Preset sizes vary legitimately. Drum kits can hold dozens of samples, while single-sample synth patches have exactly one. **A one-file preset folder is normal, not a failure.**

## Why SFZ as well as `smpl`

A WAV `smpl` chunk carries only a root note and loop points. It **cannot express key ranges or velocity layers**, which multisampled instruments depend on. Writing only `smpl` would discard that. So `extract` emits both: `smpl` for tool parity, and an SFZ per preset that keeps everything.

```
<region> sample=BP000.wav
  lokey=0 hikey=39 pitch_keycenter=36 lovel=0 hivel=127 end=9599
  loop_mode=loop_continuous loop_start=2400 loop_end=9599
```

**SFZ compatibility:** confirmed in DirectWave. If a sampler with partial SFZ support rejects regions, `--no-keytrack` drops the one non-core opcode, `pitch_keytrack`. Drum zones stay correct without it, because they are single-key and `pitch_keycenter` already equals their only key.

## Lessons learned

1. **A third-party extraction produced 2,016 of 2,051 files as `NOT-RIFF`**: raw payloads with no WAV header, unplayable. Only the WAV health check caught it. Direct extraction avoids that intermediate step.
2. **Internal consistency is not correctness.** The audit proved every byte of audio and every zone field self-consistent while the root-note *interpretation* was wrong. Only measuring the actual audio pitch caught it. Validate against physical reality, not just against your own parser.
3. **A field that is zero in one preset type may be load-bearing in another.** `fixed_pitch` and the real root were invisible while only drum kits were examined.
4. **Zero-filled output files** (all NUL bytes, correct sizes) were traced to memory pressure on the machine during a run, not to a bug. Re-running fixed it. If output looks structurally wrong, check the bytes before suspecting the generator.

## Comparison with MSE-Extract-Tool

| | xtracktor | MSE-Extract-Tool |
|---|---|---|
| Extract PCM → WAV | ✅ | ✅ |
| Loop points in `smpl` | ✅ | ✅ |
| Root note | ✅ (read from data, drum-aware) | ✅ |
| File-level header documented | ✅ | ❌ |
| Preset grouping and display names | ✅ | ❌ |
| Key ranges, velocity layers | ✅ SFZ | ❌ |
| Playable instrument output | ✅ SFZ | ❌ |
| Layout validation and byte-accuracy audit | ✅ | ❌ |
| Pitch verification against audio | ✅ | ❌ |
| Non-16-bit / FLAC-sourced banks | ⚠️ untested | ✅ |

xtracktor is a superset for the banks it was developed against, but not in general: non-16-bit coverage is untested, and `extract` always writes format tag 1 (integer PCM).

## Legal note

Extracting audio from a sample library you own, for your own use, is the normal case. **Redistribution or resale of extracted samples is typically prohibited** by sample-library licences; each library's own licence governs. MSE-Extract-Tool (MIT) permits reuse, including commercially. Not legal advice.
