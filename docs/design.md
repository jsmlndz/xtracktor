# xtracktor: design and verification

[README](../README.md) · [Usage guide](usage.md) · [Notes](notes.md) · **Design and verification**

- **Version:** 1.2.2
- **What it is:** a standard-library-only Python CLI that turns `.mse` sample banks into per-preset WAV folders plus one SFZ instrument per preset, and proves the result correct.
- **History:** consolidated from five single-purpose scripts (`mse_index.py`, `pitch_check.py`, `wav_check.py`, `mse_survey.py`, `make_quirk_fixture.py`) into one tool, without changing a byte of output.

## Contents

1. [Commands](#1-commands)
2. [Source layout](#2-source-layout)
3. [Acceptance gates](#3-acceptance-gates)
4. [Invariants a change must not break](#4-invariants-a-change-must-not-break)
5. [Verification record](#5-verification-record)
6. [Change history](#6-change-history)
7. [Lessons learned](#7-lessons-learned)
8. [Known issues and possible future work](#8-known-issues-and-possible-future-work)

---

## 1. Commands

| Command | Purpose | Formerly |
|---|---|---|
| `verify` | Parse records, resolve presets, report health. **Run first** | `mse_index.py verify` |
| `index` | List every sample record | `mse_index.py index` |
| `zones` | Probe the zone block statistically, for unfamiliar libraries | `mse_index.py zones` |
| `zonecheck` | Decode and validate every zone against constraints | `mse_index.py zonecheck` |
| `extract` | WAVs + `smpl` chunks + one SFZ per preset (dry run unless `--apply`) | `mse_index.py extract` |
| `audit` | Prove an extraction complete and byte-accurate | `mse_index.py audit` |
| `sfz` | Rewrite only the `.sfz` files (dry run unless `--apply`) | `mse_index.py sfz` |
| `match` | Identify already-extracted WAVs by their audio | `mse_index.py match` |
| `pitch` | Measured pitch vs stored root note | `pitch_check.py` |
| `wavcheck` | Diagnose structurally broken WAVs | `wav_check.py` |
| `survey` | Raw string layout of a bank, with offsets | `mse_survey.py` |
| `fixture OUT_DIR` | Build the synthetic test bank | `make_quirk_fixture.py` |

`--version` prints the version. Ctrl-C exits with status 130 and leaves no partial files. The [usage guide](usage.md) shows every command with example output.

## 2. Source layout

Python 3.9+, tested on 3.9–3.13, standard library only (the code also parses under the 3.8 grammar). numpy is used by `pitch` if it is installed, with identical results without it; only `pitch` loads it.

| Module | Holds |
|---|---|
| [`__init__.py`](../src/xtracktor/__init__.py) | Version, command list, the 7 invariants |
| [`cli.py`](../src/xtracktor/cli.py) | Argument definitions and dispatch only |
| [`commands.py`](../src/xtracktor/commands.py) | One function per command: drives the core and prints the report |
| [`mse.py`](../src/xtracktor/mse.py) | Bank parsing: string scan (mmap + regex), records, preset stems, zone blocks, root-note rule, destination planning |
| [`wav.py`](../src/xtracktor/wav.py) | One RIFF chunk walker, plus WAV writing, `smpl`, data digests, health inspection |
| [`sfz.py`](../src/xtracktor/sfz.py) | SFZ generation |
| [`pitch.py`](../src/xtracktor/pitch.py) | Autocorrelation pitch detection |
| [`fixture.py`](../src/xtracktor/fixture.py) | The synthetic test bank |
| [`util.py`](../src/xtracktor/util.py) | Progress output, file discovery, CSV, atomic writes, streamed hashing |

**Build:** `python3 tools/build_pyz.py` writes `dist/xtracktor.pyz`, a zipapp that runs as `python3 xtracktor.pyz <command>`. [`src/__main__.py`](../src/__main__.py) calls `sys.exit(main())`. Don't switch to zipapp's `-m` option: its generated `__main__` discards the return value, so exit codes would be lost.

## 3. Acceptance gates

[`tools/baseline.sh`](../tools/baseline.sh) runs everything in one command:

```bash
python3 tools/build_pyz.py
bash tools/baseline.sh dist/xtracktor.pyz results
```

It builds the synthetic test bank in a new temporary folder, runs every command against it, and reports five checks. It exits 1 if any fail. The temporary folder is removed when the script exits, even after a failure or Ctrl-C, and is the only thing the script deletes; logs, the manifest and the fingerprint stay in the results folder.

1. **GATE 1:** `zonecheck` → `VERDICT: zone layout holds for every record`
2. **GATE 2:** `extract --apply`, then `audit --zones` → `VERDICT: COMPLETE AND BYTE-ACCURATE`
3. **GATE 3:** `pitch` on the pitched test preset → `VERDICT: every stored root matches the measured pitch`
4. **HEALTH:** `wavcheck` → `ok 13`
5. **MATCH:** the SHA-256 of the output manifest (every WAV and SFZ written) equals the reference fingerprint. For 1.2.0, 1.2.1 and 1.2.2 that is `08c98592455aeeebd90c5beebb9a32fad2d265b318a3d49aa39cc67826ae2298`. It is a hash of synthetic test output, not a secret.

A passing run ends like this:

```
PASS  GATE 1  zone layout parses
PASS  GATE 2  byte-accurate extraction
PASS  GATE 3  roots match measured pitch
PASS  HEALTH  13 structurally ok WAVs
PASS  MATCH   output identical to the reference

output files fingerprinted: 17   (expect 17: 13 WAV + 4 SFZ)
```

The test bank reproduces every quirk of the real banks:

- heading/folder prefix mismatches and an unprefixed folder
- junk strings between samples
- the bare-filename anomaly
- a sample shared by two presets
- drum and pitched zones, looped and unlooped zones, and a velocity-split pair

Its pitched samples are generated at their declared root, so GATE 3 is meaningful. It is byte-identical on every machine and Python version, so one fingerprint holds everywhere. No copyrighted audio is needed to test.

**When output changes on purpose, update `REFERENCE` in `baseline.sh`.**

Each gate has caught a real bug. GATE 2 alone is not enough: with the old [root-note bug](notes.md#root-notes-should-be-analyzed) reintroduced, GATE 2 still passes and only GATE 3 fails. Byte accuracy does not prove the roots are right.

The [CI workflow](../.github/workflows/ci.yml) runs `baseline.sh` on Linux and macOS with Python 3.9–3.13 on every push and pull request.

## 4. Invariants a change must not break

1. **Audio is copied byte for byte.** Never re-encode it, and never round-trip it through a float buffer.
2. **`mmap` and streaming only.** Banks are 0.6–1.1 GB each, and nothing loads a whole bank into RAM.
3. **Root-note rule.** Fixed-pitch (drum and one-shot) zones use their own key as the root, and the SFZ gets `pitch_keytrack=0`. Every other zone uses its stored root note.
4. **Preset names join on the stem**, never by regex on heading strings.
5. **No half-written file survives under its real name.** WAVs and SFZs are written to `<name>.part` and renamed only when complete. On any error or Ctrl-C the `.part` file is removed.
6. **Dry run is the default** for anything that writes.
7. **`sfz` stays separate from `extract`.** Regenerating text files must never require re-extracting 2.3 GB.

## 5. Verification record

**1.0.0, the consolidation (a pure refactor):**

- **Differential suite:** 58 cases covering every command and flag, sabotaged extractions and deliberately broken WAVs, comparing the old scripts and `xtracktor` on identical input. 56 were identical. The 2 differences were a deliberate fix: a new `zone-mismatch` count in the audit summary.
- **Test bank, macOS, Python 3.9.6:** all gates pass, and all 17 output files are byte-identical to the old scripts' output.
- **Real banks:**
  - `audit --zones`: `COMPLETE AND BYTE-ACCURATE`, 2,051/2,051, all 206 folders reconcile
  - `wavcheck`: `ok 2051`
  - `pitch` on a pitched preset: delta 0 on all 7 samples
  - All of this after re-extracting the whole library from scratch with `xtracktor`.

**1.1.0:**

- **Test bank:** byte-identical across Python 3.9.6 (macOS) and 3.11, 3.12 and 3.13 (Linux), and across any `PYTHONHASHSEED`. `baseline.sh` shows 5 × PASS on both platforms.
- **Differential suite:** 55 identical. The 3 differences are all intended: the test bank's new seeds and the `zone-mismatch` line.

**1.2.0:**

- **Engine unchanged:** every module except the test bank is identical to 1.1.0 apart from comments and the version number (checked by comparing syntax trees).
- **Test bank:** 5 × PASS on Python 3.11, 3.12 and 3.13 (Linux) with any `PYTHONHASHSEED`, against the new reference, and 5 × PASS on Python 3.9 and 3.10 (Linux).
- **macOS:** 5 × PASS on Python 3.9, 3.10, 3.11, 3.12 and 3.13 on the GitHub Actions `macos-latest` runner (CI).

**1.2.1:**

- **One report line changed:** the `zones` legend. Every other command prints the same as 1.2.0 on the test bank, and the output manifest is identical.
- **Test bank:** 5 × PASS on Python 3.9, 3.10, 3.11, 3.12 and 3.13 (Linux), against the unchanged reference.

**1.2.2:**

- **Only `baseline.sh` changed;** the tool and its output are identical to 1.2.1.
- **Test bank:** 5 × PASS on Python 3.9, 3.10, 3.11, 3.12 and 3.13 (Linux), against the unchanged reference.
- **Clean-up:** no temporary folder is left behind after a passing run, a failing run, Ctrl-C (exit 130) or a kill signal (exit 143), and a folder named by an `FX` environment variable is left untouched.

## 6. Change history

The [changelog](../CHANGELOG.md) has the same history in summary form.

**1.2.2: safer acceptance script.**

- **`baseline.sh`:** builds the test bank in a new temporary folder (`mktemp -d`) and removes only that folder on exit. Before, it ran `rm -rf` on `/tmp/fx`, or on the folder named by an `FX` environment variable, without checking it.

**1.2.1: cosmetic fix.**

- **`zones`:** the report's legend line printed `%%midi` / `%%<=frames` instead of `%midi` / `%<=frames`. The bug was inherited from the original script, and nothing reads that line.

**1.2.0: test bank renamed.**

- **Renamed** the synthetic test bank's bank, preset, file and header names. Every quirk is still reproduced.
- **New reference fingerprint** in `baseline.sh`, because the test bank's output changed.

**1.1.0: deliberate output changes.**

- **Test-bank seeds** come from CRC-32 instead of Python's `hash()`, so the test bank is identical everywhere.
- **SFZ first line** now reads `// generated by xtracktor from .mse zone data`.
- **`baseline.sh`** gained the MATCH check against a fixed reference fingerprint.

**1.0.0: consolidation. Output is identical on a clean run.**

- **Atomic writes:** the old `extract` left a partial WAV under its real name on Ctrl-C (tested: 2 MB of a 96 MB file). Now nothing is left, and the exit status is 130.
- **`audit`:** the summary now counts `zone-mismatch`.
- **`verify --max`:** now works; it was a no-op.
- **`survey`:** uses mmap + regex instead of a whole-file read and a per-byte loop. On a 384 MB bank, private memory dropped from 401 MB to 26 MB, and it runs 3.5× faster.
- **`pitch`:** reads only the 4,096-frame analysis window instead of the whole file.
- **Merged duplicates:**
  - five RIFF chunk readers became one walker
  - two string scanners became one
  - zone reading is unified

## 7. Lessons learned

1. **Never seed test data from Python's `hash()`.** It is salted per process, and Python 3.11 changed the string algorithm from SipHash-2-4 to SipHash-1-3. So `PYTHONHASHSEED=0` gave different test audio on Python 3.9 and 3.13. The giveaway: files not seeded by `hash()` matched across machines, and the 11 that were did not.
2. **Prove a refactor by output identity, not just by gates.** The manifest diff and the 58-case differential suite are what proved the consolidation safe. Gates alone would have allowed silent drift.
3. **RSS overstates mmap memory.** Mapped file pages count toward RSS but are reclaimable cache. Measure private (anonymous) memory to compare against a whole-file read.
4. **Internal consistency is not correctness** (see [the notes](notes.md#root-notes-should-be-analyzed)). The root-note bug passed a byte-accurate audit and was caught only by measuring pitch.

## 8. Known issues and possible future work

- **`pitch` on drum kits** reports meaningless deltas. Drum zones play at natural pitch, so their root is their trigger key. Run `pitch` on pitched presets only.
- **`audit --quick`** compares only the first 64 KiB of each sample, so it misses damage beyond that point. The full audit is the proof.
- **Untested:** non-16-bit or FLAC-sourced banks, and `.mse` libraries other than the one it was developed against.
- **GUI (not started):**
  - The full audit reads about 4.7 GB, so a GUI needs a worker thread with progress and cancel.
  - Progress goes through a single hook, `util.progress()`.
  - Cancel can rely on invariant 5.
