# Additional Notes 

[Design and verification](design.md)

## Contents

- [Root notes should be analyzed](#root-notes-should-be-analyzed)
- [Audio characteristics](#audio-characteristics)
- [Known anomaly](#known-anomaly)
- [Results](#results)
- [Why SFZ as well as `smpl`](#why-sfz-as-well-as-smpl)
- [Lessons learned](#lessons-learned)
- [Comparison with MSE-Extract-Tool](#comparison-with-mse-extract-tool)
- [Legal note](#legal-note)

---

## Root notes should be analyzed

**Each zone stores its own root note.** The field holding it was first misread as a zone index, and the bottom key of each zone's key range was used as the root instead. The result was multisampled presets playing up to 39 semitones sharp, with an audible pitch jump at every zone boundary.

Two rules:

1. **Drum and one-shot zones**, which the bank marks as fixed-pitch: the sample plays at its natural pitch whatever key triggers it, and its stored root is an **unused counter**. In the drum kit examined, applying it would have transposed every drum up three octaves. **Use the zone's own key as the root, and emit `pitch_keytrack=0` so the sampler doesn't transpose it.**
2. **Pitched zones:** **use the stored root note directly.**

In pitched presets, each zone covers a few keys around its root note, and the lowest and highest zones are stretched so the whole keyboard is covered.

**Verified by measuring the audio.** `xtracktor pitch` estimates each sample's fundamental frequency and compares it with the stored root. On two pitched presets (16 samples), the stored root matched the measured pitch exactly. After the fix, every pitched preset reports `delta 0`.

This is why the acceptance tests include a pitch gate: see [Acceptance gates](design.md#3-acceptance-gates).

## Audio characteristics

Uniform across every sample examined: **44,100 Hz, 2 channels, 16-bit, uncompressed.**

## Known anomaly

One record in one bank was a bare filename, with no directory and no valid field block. It is a metadata reference, not a sample. xtracktor skips it, and no audio is missing.

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

A WAV `smpl` chunk carries only a root note and loop points. It **cannot express key ranges or velocity layers**, which multisampled instruments depend on, so writing only `smpl` would discard them. `extract` therefore writes both: `smpl` chunks for compatibility with other tools, and one SFZ per preset that keeps everything.

```
<region> sample=BP000.wav
  lokey=0 hikey=39 pitch_keycenter=36 lovel=0 hivel=127 end=9599
  loop_mode=loop_continuous loop_start=2400 loop_end=9599
```

**SFZ compatibility:** confirmed in DirectWave. If a sampler with partial SFZ support rejects regions, `--no-keytrack` drops the one non-core opcode, `pitch_keytrack`. Drum zones stay correct without it, because they are single-key and `pitch_keycenter` already equals their only key. See [Rebuild only the SFZ files](usage.md#24-rebuild-only-the-sfz-files-seconds-audio-untouched).

## Lessons learned

1. The audit proved every byte of audio and every zone field self-consistent while the root-note *interpretation* was wrong. Only measuring the actual pitch of the audio caught it. Validate against physical reality, not just against your own parser.
2. A field that is zero in one preset type may be load-bearing in another. The fixed-pitch flag and the real root were invisible while only drum kits were examined.
3. Zero-filled output files (all NUL bytes, correct sizes) were traced to memory pressure on the machine during a run, not to a bug. Re-running fixed it. If output looks structurally wrong, check the bytes before suspecting the generator.

## Comparison with MSE-Extract-Tool

| | xtracktor | MSE-Extract-Tool |
|---|---|---|
| Extract PCM → WAV | ✅ | ✅ |
| Loop points in `smpl` | ✅ | ✅ |
| Root note | ✅ (read from data, drum-aware) | ✅ |
| File-level header decoded | ✅ | ❌ |
| Preset grouping and display names | ✅ | ❌ |
| Key ranges, velocity layers | ✅ SFZ | ❌ |
| Playable instrument output | ✅ SFZ | ❌ |
| Layout validation and byte-accuracy audit | ✅ | ❌ |
| Pitch verification against audio | ✅ | ❌ |
| Non-16-bit / FLAC-sourced banks | ⚠️ untested | ✅ |

While xtracktor does have a few extra bells and whistles, the features have only been tested for the banks it was developed against, not in general: non-16-bit coverage is untested, and `extract` always writes format tag 1 (integer PCM).

## Legal note

Extracting audio from a sample library you own, for your own use, is the normal case. **Redistribution or resale of extracted samples is typically prohibited** by sample-library licences; each library's own licence governs. MSE-Extract-Tool (MIT) permits reuse, including commercially. This is not legal advice.
