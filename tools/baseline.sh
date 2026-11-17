#!/bin/bash
# baseline.sh - run the xtracktor acceptance gates against the quirk fixture
# with xtracktor, log every command's output, and fingerprint every file the
# extraction writes.
#
# Usage:
#   bash baseline.sh /path/to/xtracktor.pyz                # results in ./baseline-<timestamp>
#   bash baseline.sh /path/to/xtracktor.pyz ./after        # results in ./after
#
# Since 1.1.0 the fixture is byte-identical on every machine and Python
# version, so the output fingerprint is checked against a fixed reference
# (REFERENCE below). To compare two runs in detail:
#   diff before/manifest.txt after/manifest.txt     # no output = identical files
#   diff -r before/logs after/logs                  # printed output, command by command
#
# When output changes ON PURPOSE, update REFERENCE to the new fingerprint.
#
# Exit status: 0 if every gate passes, 1 otherwise.
# Works with macOS's stock bash 3.2 and on Linux.

X="${1:?usage: bash baseline.sh PATH/TO/xtracktor.pyz [RESULTS_DIR]}"
R="${2:-$PWD/baseline-$(date +%Y%m%d-%H%M%S)}"
FX="${FX:-/tmp/fx}"

if [ ! -f "$X" ]; then
  echo "not found: $X" >&2
  exit 2
fi

# sha256 of manifest.txt for xtracktor 1.2.0 - every WAV and SFZ it extracts
# from the fixture.
REFERENCE="08c98592455aeeebd90c5beebb9a32fad2d265b318a3d49aa39cc67826ae2298"

mkdir -p "$R/logs"
rm -rf "$FX"
fail=0

run() {            # run NAME CMD...   -> $R/logs/NAME.txt and NAME.exit
  local name=$1; shift
  "$@" > "$R/logs/$name.txt" 2>&1
  echo $? > "$R/logs/$name.exit"
}

gate() {           # gate LABEL LOGNAME REGEX
  if grep -Eq "$3" "$R/logs/$2.txt"; then
    echo "PASS  $1"
  else
    echo "FAIL  $1   (see $R/logs/$2.txt)"
    fail=1
  fi
}

{
  echo "date:      $(date)"
  echo "python:    $(python3 --version 2>&1)"
  echo "numpy:     $(python3 -c 'import numpy; print(numpy.__version__)' 2>/dev/null || echo 'not installed')"
  echo "xtracktor: $(python3 "$X" --version 2>&1) ($X)"
  echo "fixture:   $FX"
} > "$R/env.txt"

# Fixture
run 00_fixture        python3 "$X" fixture "$FX"

# The three gates plus structural health
run 01_zonecheck      python3 "$X" zonecheck --mse-dir "$FX/banks"
run 02a_extract_dry   python3 "$X" extract --mse-dir "$FX/banks" --out-dir "$FX/out"
run 02b_extract       python3 "$X" extract --mse-dir "$FX/banks" --out-dir "$FX/out" --apply
run 02c_audit         python3 "$X" audit --mse-dir "$FX/banks" --out-dir "$FX/out" --zones
run 03_pitch          python3 "$X" pitch --dir "$FX/out/03-Bell Pad"
run 04_wavcheck       python3 "$X" wavcheck --dir "$FX/out"

# The remaining commands, logged so their output can be diffed
run 05_verify         python3 "$X" verify --mse-dir "$FX/banks"
run 06_index          python3 "$X" index --mse-dir "$FX/banks"
run 07_zones          python3 "$X" zones --mse-dir "$FX/banks"
run 08_sfz_dry        python3 "$X" sfz --mse-dir "$FX/banks" --out-dir "$FX/out"
run 09_match          python3 "$X" match --mse-dir "$FX/banks" --wav-dir "$FX/extracted"
run 10_survey         python3 "$X" survey "$FX/banks"
run 11_pitch_all      python3 "$X" pitch --dir "$FX/out"

# Fingerprint every output file (WAVs and SFZs)
if command -v sha256sum >/dev/null 2>&1; then H="sha256sum"; else H="shasum -a 256"; fi
( cd "$FX/out" && find . -type f | LC_ALL=C sort | while IFS= read -r f; do $H "$f"; done ) \
  > "$R/manifest.txt"
FINGERPRINT=$($H < "$R/manifest.txt" | cut -c1-64)
echo "$FINGERPRINT" > "$R/fingerprint.txt"

echo
gate "GATE 1  zone layout parses"          01_zonecheck "VERDICT: zone layout holds for every record"
gate "GATE 2  byte-accurate extraction"    02c_audit    "VERDICT: COMPLETE AND BYTE-ACCURATE"
gate "GATE 3  roots match measured pitch"  03_pitch     "VERDICT: every stored root matches the measured pitch"
gate "HEALTH  13 structurally ok WAVs"     04_wavcheck  "^  ok +13$"
if [ "$FINGERPRINT" = "$REFERENCE" ]; then
  echo "PASS  MATCH   output identical to the reference"
else
  echo "FAIL  MATCH   output differs from the reference ($FINGERPRINT)"
  fail=1
fi
echo
echo "output files fingerprinted: $(wc -l < "$R/manifest.txt" | tr -d ' ')   (expect 17: 13 WAV + 4 SFZ)"
echo "results: $R"
exit $fail
