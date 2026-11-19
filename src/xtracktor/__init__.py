"""xtracktor - extract, verify and inspect the samples in .mse banks.

One tool replacing mse_index.py, pitch_check.py, wav_check.py, mse_survey.py
and make_quirk_fixture.py. Python 3.9+, standard library only (numpy is used
by `pitch` if present, with identical results without it).

Commands
--------
  verify     parse records, resolve presets, report health - run first
  index      list every sample record
  zones      probe the zone block statistically
  zonecheck  parse and validate every zone block
  extract    write WAVs (+ smpl chunks + one SFZ per preset) from the banks
  audit      prove an extraction is complete and byte-accurate
  sfz        rewrite only the .sfz files of an existing extraction
  match      map already-extracted WAVs back to their records by PCM hash
  pitch      measure each WAV's pitch and compare it with its smpl root
  wavcheck   diagnose structurally broken WAV files
  survey     dump the raw string layout of a bank, with offsets
  fixture    build the synthetic quirk fixture (the regression gate)

Invariants - a change that breaks one of these is a bug
------------------------------------------------------
1. Audio is copied byte for byte: never re-encoded, never through floats.
2. Banks are read with mmap or streaming I/O only; never loaded whole.
3. Root note: fixed_pitch == 1 -> root is key_low and SFZ gets
   pitch_keytrack=0; otherwise root is root_note (+0).
4. Preset display names join to folders on their stem, never by regex.
5. A half-written file never survives under its real name.
6. Dry run is the default for anything that writes.
7. `sfz` stays separate from `extract`.
"""

__version__ = "1.2.2"
