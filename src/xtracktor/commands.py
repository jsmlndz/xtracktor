"""One function per command. Each takes the parsed arguments, drives the
core modules, prints its report and returns the exit status.

Printed output and CSV columns match the five original scripts line for
line - apart from hints that name the command to run next, and the audit
summary's zone-mismatch count - so results can be diffed across versions.
"""

import os
import shutil
import struct
import sys

from . import fixture, mse, wav
from .mse import (each_mse, effective_root, parse_records, plan_destinations,
                  read_zone, read_zone_checked, zone_loops, zone_regions)
from .sfz import sfz_for_group
from .util import (HASH_BYTES, find_files, invocation, progress,
                   sha256_range, write_atomic, write_csv)


def _no_banks(mse_dir):
    print("No .mse files under %s" % mse_dir, file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# verify / index

def cmd_verify(args):
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    total_bad = total_unnamed = 0
    for path in targets:
        info, records = parse_records(path, want_hash=False)
        bad = [r for r in records if r["problems"]]
        good = [r for r in records if not r["problems"]]
        total_bad += len(bad)
        print("=" * 72)
        print("%s   %.1f MB%s" % (info["name"], info["size"] / 1048576.0,
                                  "" if info["magic_ok"] else "  [BAD MAGIC]"))
        print("sample records: %d     parsed cleanly: %d / %d"
              % (info["records"], info["clean"], info["records"]))
        rates = sorted({r["sample_rate"] for r in good})
        chans = sorted({r["channels"] for r in good})
        depths = sorted({r["bytes_per_sample"] for r in good})
        print("audio: %s Hz   %s ch   %s bytes/sample"
              % (rates or "-", chans or "-", depths or "-"))
        folders = info["folders"]
        named = [f for f, n in folders.items() if n]
        unnamed = [f for f, n in folders.items() if not n]
        total_unnamed += len(unnamed)
        print("presets (distinct sample folders): %d   with display name: %d"
              % (len(folders), len(named)))
        print()
        counts = {}
        for rec in good:
            counts[rec["folder"]] = counts.get(rec["folder"], 0) + 1
        ordered = sorted(folders, key=lambda f: -counts.get(f, 0))
        for folder in ordered[:args.max]:
            display = folders[folder]
            arrow = "-> %-24s" % display if display else "-> (folder name used)   "
            print("  %-26s %s %4d samples"
                  % (folder[:26], arrow, counts.get(folder, 0)))
        if len(ordered) > args.max:
            print("  ... %d more preset folder(s) (raise --max)"
                  % (len(ordered) - args.max))
        if bad:
            print("\nrecords skipped (%d):" % len(bad))
            for rec in bad[:10]:
                print("  %-56s %s" % (rec["original_path"][:56],
                                      rec["problems"]))
        print()
    print("=" * 72)
    print("VERDICT: %d record(s) skipped, %d preset folder(s) without a "
          "display name" % (total_bad, total_unnamed))
    print("Skipped bare filenames are metadata references, not audio.")
    return 0


def cmd_index(args):
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    rows = []
    for path in targets:
        _info, records = parse_records(path, want_hash=False)
        rows.extend(r for r in records if not r["problems"])
    for rec in rows[:args.max]:
        print("%-20s %-26s %-16s %6dHz %dch %8d frames"
              % (rec["mse_file"][:20], rec["preset"][:26],
                 rec["sample_name"][:16], rec["sample_rate"],
                 rec["channels"], rec["frames"]))
    if len(rows) > args.max:
        print("... %d more (raise --max)" % (len(rows) - args.max))
    print("\n%d sample records across %d file(s)" % (len(rows), len(targets)))
    if args.csv:
        write_csv(args.csv, rows, [
            "mse_file", "preset", "folder", "sample_name", "original_path",
            "sample_rate", "channels", "bytes_per_sample", "frames",
            "pcm_offset", "pcm_length", "unknown"])
        print("Wrote %s" % args.csv)
    return 0


# --------------------------------------------------------------------------
# match

def _match_row(wav_file, preset="", folder="", mse_file="", original="",
               suggested="", rate=0, channels=0, bits=0, status=""):
    return {"wav_file": wav_file, "preset": preset, "folder": folder,
            "mse_file": mse_file, "original_path": original,
            "suggested_name": suggested, "sample_rate": rate,
            "channels": channels, "bits": bits, "status": status}


def cmd_match(args):
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    by_key, by_digest, total = {}, {}, 0
    for path in targets:
        progress("indexing %s ..." % os.path.basename(path))
        _info, records = parse_records(path, want_hash=True)
        for rec in records:
            if rec["problems"] or not rec["pcm_sha256_64k"]:
                continue
            total += 1
            by_key.setdefault((rec["pcm_length"], rec["pcm_sha256_64k"]),
                              []).append(rec)
            by_digest.setdefault(rec["pcm_sha256_64k"], []).append(rec)
    progress("indexed %d sample records" % total)

    wavs = find_files(args.wav_dir, ".wav")
    if not wavs:
        print("No .wav files under %s" % args.wav_dir, file=sys.stderr)
        return 1

    rows, exact, fuzzy, missing = [], 0, 0, 0
    for path in wavs:
        rel = os.path.relpath(path, args.wav_dir)
        try:
            length, digest, (rate, channels, bits) = wav.data_digest(
                path, full=False)
        except (OSError, ValueError) as exc:
            rows.append(_match_row(rel, status="error: %s" % exc))
            missing += 1
            continue
        hits, how = by_key.get((length, digest)), "exact"
        if not hits:
            hits, how = by_digest.get(digest), "audio-only (length differs)"
        was_exact = how == "exact"
        if not hits:
            rows.append(_match_row(rel, rate=rate, channels=channels,
                                   bits=bits, status="no match"))
            missing += 1
            continue
        first = hits[0]
        if len(hits) > 1:
            others = sorted({h["preset"] for h in hits})
            how += " | shared by %d presets: %s" % (len(hits),
                                                    ", ".join(others))
        rows.append(_match_row(rel, preset=first["preset"],
                               folder=first["folder"],
                               mse_file=first["mse_file"],
                               original=first["original_path"],
                               suggested=first["sample_name"], rate=rate,
                               channels=channels, bits=bits, status=how))
        if was_exact:
            exact += 1
        else:
            fuzzy += 1

    width = min(30, max(len(r["wav_file"]) for r in rows))
    for row in rows[:args.max]:
        note = "" if row["status"] == "exact" else "   <- %s" % row["status"]
        print("%-*s -> %-26s %s%s" % (width, row["wav_file"][:width],
                                      row["preset"] or "-",
                                      row["suggested_name"] or "-", note))
    if len(rows) > args.max:
        print("... %d more (see the CSV)" % (len(rows) - args.max))
    print("\n%d exact, %d audio-only, %d unmatched, of %d WAV files"
          % (exact, fuzzy, missing, len(rows)))
    if args.csv:
        write_csv(args.csv, rows, [
            "wav_file", "preset", "folder", "suggested_name", "original_path",
            "mse_file", "sample_rate", "channels", "bits", "status"])
        print("Wrote %s" % args.csv)
    return 0


# --------------------------------------------------------------------------
# extract / sfz / audit

def _write_sfz(path, text):
    write_atomic(path, lambda out: out.write(text), mode="w",
                 encoding="utf-8")


def cmd_extract(args):
    """Write WAVs directly from the banks into per-preset folders."""
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)

    jobs, groups, total_bytes = plan_destinations(
        targets, args.out_dir, args.group_by)

    parent = args.out_dir
    while parent and not os.path.isdir(parent):
        parent = os.path.dirname(parent) or "."
    free = shutil.disk_usage(parent or ".").free
    gib = 1073741824.0
    print("\nEXTRACT %d sample(s) into %d preset folder(s) under %s"
          % (len(jobs), len(groups), args.out_dir))
    print("audio to write: %.2f GiB      free on volume: %.2f GiB"
          % (total_bytes / gib, free / gib))
    fits = total_bytes + (256 << 20) < free
    print("space check: %s" % ("OK" if fits else
                               "NOT ENOUGH SPACE (need the above plus "
                               "headroom) - free space or choose another "
                               "--out-dir"))
    print("mode: %s" % ("APPLY" if args.apply else "DRY RUN"))
    print()
    for group in sorted(groups)[:args.max]:
        print("  %-34s %4d sample(s)" % (group[:34], len(groups[group])))
    if len(groups) > args.max:
        print("  ... %d more folder(s)" % (len(groups) - args.max))

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply.")
        return 0
    if not fits and not args.force:
        print("\nRefusing to start: not enough free space. Use --force to "
              "override.", file=sys.stderr)
        return 1

    done = failed = no_zone = looped = 0
    open_banks, by_group = {}, {}
    try:
        for bank_path, dest, rec in jobs:
            handle = open_banks.get(bank_path)
            if handle is None:
                handle = open_banks[bank_path] = open(bank_path, "rb")
            try:
                zone = None if args.no_zones else read_zone(handle, rec)
                if zone is None and not args.no_zones:
                    no_zone += 1
                elif zone is not None and zone["loop_mode"]:
                    looped += 1
                trailing = None
                if zone is not None and not args.no_smpl:
                    trailing = wav.smpl_chunk(
                        rec["sample_rate"],
                        effective_root(zone, args.root_note),
                        zone_loops(zone))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                # Written under a temporary name and renamed when complete,
                # so a failure or Ctrl-C never leaves a partial WAV.
                wav.write_wav(dest, handle, rec["pcm_offset"],
                              rec["pcm_length"], rec["sample_rate"],
                              rec["channels"], rec["bytes_per_sample"] * 8,
                              trailing)
                group = os.path.basename(os.path.dirname(dest))
                by_group.setdefault(group, []).append(
                    {"group": group, "dest": dest, "zone": zone})
                done += 1
                if done % 200 == 0:
                    progress("  written %d/%d ..." % (done, len(jobs)))
            except (OSError, IOError) as exc:
                print("error: %s: %s" % (dest, exc), file=sys.stderr)
                failed += 1
    finally:
        for handle in open_banks.values():
            handle.close()

    sfz_written = 0
    if not args.no_sfz and not args.no_zones:
        for group, rows in by_group.items():
            path = os.path.join(args.out_dir, group, "%s.sfz" % group)
            try:
                _write_sfz(path, sfz_for_group(
                    rows, args.root_note, keytrack=not args.no_keytrack))
                sfz_written += 1
            except OSError as exc:
                print("error writing %s: %s" % (path, exc), file=sys.stderr)

    run = invocation()
    print("\nWrote %d sample(s)%s into %s"
          % (done, "" if not failed else ", %d FAILED" % failed,
             args.out_dir))
    if not args.no_zones:
        print("zone data: %d looped, %d without usable zone data"
              % (looped, no_zone))
        if not args.no_smpl:
            print("smpl chunks written with root-note policy '%s'"
                  % args.root_note)
        if sfz_written:
            print("SFZ instruments written: %d (one per preset folder)"
                  % sfz_written)
            print("  to rewrite only these later, without touching audio:")
            print("  %s sfz --mse-dir ... --out-dir ... --apply" % run)
    print("Verify with:  %s wavcheck --dir %s" % (run, args.out_dir))
    print("          and %s audit --mse-dir %s --out-dir %s"
          % (run, args.mse_dir, args.out_dir))
    return 1 if failed else 0


def cmd_sfz(args):
    """Rewrite only the .sfz files for an existing extraction.

    Audio is untouched, so trying a different SFZ dialect costs seconds
    instead of a full re-extraction.
    """
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    jobs, _groups, _bytes = plan_destinations(targets, args.out_dir,
                                              args.group_by)
    by_group, missing, banks = {}, 0, {}
    try:
        for bank_path, dest, rec in jobs:
            if not os.path.exists(dest):
                missing += 1
                continue
            handle = banks.get(bank_path)
            if handle is None:
                handle = banks[bank_path] = open(bank_path, "rb")
            group = os.path.basename(os.path.dirname(dest))
            by_group.setdefault(group, []).append(
                {"group": group, "dest": dest, "zone": read_zone(handle, rec)})
    finally:
        for handle in banks.values():
            handle.close()

    keytrack = not args.no_keytrack
    written = 0
    for group, rows in sorted(by_group.items()):
        path = os.path.join(args.out_dir, group, "%s.sfz" % group)
        text = sfz_for_group(rows, args.root_note, keytrack=keytrack)
        if args.apply:
            try:
                _write_sfz(path, text)
                written += 1
            except OSError as exc:
                print("error writing %s: %s" % (path, exc), file=sys.stderr)
        else:
            written += 1
    print("\n%s %d SFZ file(s) for %d preset folder(s)"
          % ("Wrote" if args.apply else "Would write", written,
             len(by_group)))
    print("pitch_keytrack on fixed-pitch zones: %s"
          % ("omitted (--no-keytrack)" if args.no_keytrack else "included"))
    if missing:
        print("%d expected WAV(s) were absent and skipped" % missing)
    if not args.apply:
        example = sorted(by_group)[0] if by_group else None
        if example:
            print("\npreview of %s.sfz:\n" % example)
            for line in sfz_for_group(by_group[example], args.root_note,
                                      keytrack=keytrack).splitlines()[:12]:
                print("  %s" % line)
        print("\nDRY RUN - nothing written. Re-run with --apply.")
    return 0


def _check_smpl(dest, handle, rec, policy):
    """Compare a written smpl chunk against the bank's zone data."""
    zone = read_zone(handle, rec)
    if zone is None:
        return ""                      # no usable zone, nothing was claimed
    expected_root = effective_root(zone, policy)
    expected_loops = zone_loops(zone)
    root, loops = wav.read_smpl(dest)
    if root is None:
        return "no smpl chunk in the WAV"
    if root != expected_root:
        return "smpl root %d, bank zone implies %d" % (root, expected_root)
    if loops != expected_loops:
        return "smpl loops %s, bank zone implies %s" % (loops, expected_loops)
    return ""


def cmd_audit(args):
    """Prove the extraction is complete and every byte of audio is correct."""
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    jobs, groups, _bytes = plan_destinations(targets, args.out_dir,
                                             args.group_by)
    full = not args.quick
    progress("\nauditing %d expected sample(s) in %d preset folder(s)"
             % (len(jobs), len(groups)))
    progress("comparison: %s" % ("full PCM sha256" if full
                                 else "first 64 KiB sha256"))

    expected_paths = set()
    results, banks = [], {}
    counters = {"ok": 0, "missing": 0, "size-mismatch": 0,
                "audio-mismatch": 0, "unreadable": 0}
    try:
        for index, (bank_path, dest, rec) in enumerate(jobs, 1):
            expected_paths.add(os.path.abspath(dest))
            verdict, detail = "ok", ""
            if not os.path.exists(dest):
                verdict, detail = "missing", "no file at expected path"
            else:
                try:
                    size, wav_digest, _fmt = wav.data_digest(dest, full)
                    if size != rec["pcm_length"]:
                        verdict = "size-mismatch"
                        detail = ("wav data %d bytes, bank record %d"
                                  % (size, rec["pcm_length"]))
                    else:
                        handle = banks.get(bank_path)
                        if handle is None:
                            handle = banks[bank_path] = open(bank_path, "rb")
                        length = rec["pcm_length"]
                        bank_digest, _got = sha256_range(
                            handle, rec["pcm_offset"],
                            length if full else min(HASH_BYTES, length))
                        if bank_digest != wav_digest:
                            verdict = "audio-mismatch"
                            detail = "sha256 differs from bank PCM"
                        elif args.zones:
                            bad = _check_smpl(dest, handle, rec,
                                              args.root_note)
                            if bad:
                                verdict, detail = "zone-mismatch", bad
                except (OSError, ValueError) as exc:
                    verdict, detail = "unreadable", str(exc)
            counters[verdict] = counters.get(verdict, 0) + 1
            results.append({
                "expected_file": os.path.relpath(dest, args.out_dir),
                "preset": rec["preset"], "mse_file": rec["mse_file"],
                "original_path": rec["original_path"],
                "pcm_length": rec["pcm_length"], "verdict": verdict,
                "detail": detail})
            if index % 250 == 0:
                progress("  checked %d/%d ..." % (index, len(jobs)))
    finally:
        for handle in banks.values():
            handle.close()

    extras = []
    for root, _dirs, names in os.walk(args.out_dir):
        for name in names:
            if name.lower().endswith(".wav"):
                full_path = os.path.abspath(os.path.join(root, name))
                if full_path not in expected_paths:
                    extras.append(os.path.relpath(full_path, args.out_dir))

    bad = [r for r in results if r["verdict"] != "ok"]
    for row in bad[:args.max]:
        print("%-16s %-44s %s" % (row["verdict"],
                                  row["expected_file"][-44:], row["detail"]))
    if len(bad) > args.max:
        print("... %d more problem(s)" % (len(bad) - args.max))

    # Per-folder reconciliation. Counting files alone would let a deletion
    # plus an unrelated extra file cancel out, so count present-and-correct
    # expected files and unexpected files separately.
    present = {}
    for row in results:
        folder = os.path.dirname(row["expected_file"]) or "."
        stats = present.setdefault(folder, {"expected": 0, "present": 0})
        stats["expected"] += 1
        if row["verdict"] == "ok":
            stats["present"] += 1
    extra_by_folder = {}
    for name in extras:
        folder = os.path.dirname(name) or "."
        extra_by_folder[folder] = extra_by_folder.get(folder, 0) + 1

    print("\nper-preset reconciliation:")
    shown = 0
    for folder in sorted(set(present) | set(extra_by_folder)):
        stats = present.get(folder, {"expected": 0, "present": 0})
        extra = extra_by_folder.get(folder, 0)
        bad_folder = (stats["present"] != stats["expected"]) or extra
        if bad_folder or args.all:
            note = "   <-- MISMATCH" if bad_folder else ""
            print("  %-34s %4d expected  %4d verified  %3d extra%s"
                  % (folder[:34], stats["expected"], stats["present"],
                     extra, note))
            shown += 1
    if shown == 0:
        print("  all %d folder(s) reconcile exactly" % len(present))

    print("\n%d expected sample(s) audited" % len(results))
    for key in ("ok", "missing", "size-mismatch", "audio-mismatch",
                "zone-mismatch", "unreadable"):
        if counters.get(key):
            print("  %-16s %d" % (key, counters[key]))
    if extras:
        print("  %-16s %d (files on disk with no matching record)"
              % ("extra", len(extras)))
        for name in extras[:10]:
            print("      %s" % name)
        if len(extras) > 10:
            print("      ... %d more" % (len(extras) - 10))

    clean = (counters["ok"] == len(results) and not extras)
    print("\nVERDICT: %s" % (
        "COMPLETE AND BYTE-ACCURATE - every expected sample is present and "
        "its audio matches the bank exactly"
        if clean else "problems found, see above"))
    if args.csv:
        write_csv(args.csv, results, [
            "expected_file", "preset", "mse_file", "original_path",
            "pcm_length", "verdict", "detail"])
        print("Wrote %s" % args.csv)
    return 0 if clean else 1


# --------------------------------------------------------------------------
# zones / zonecheck

def cmd_zonecheck(args):
    """Parse the zone block for every record and validate the layout."""
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)
    rows, bad_rows = [], []
    agree = {"end_is_frames_minus_1": 0, "loopstart0_when_mode0": 0,
             "loopend_is_sampleend": 0, "mode0": 0, "full_velocity": 0,
             "single_key": 0, "fixed": 0, "fixed_single_key": 0,
             "root_in_range": 0, "pitched_root_eq_keylow": 0, "pitched": 0}
    total = 0
    for path in targets:
        progress("parsing zones in %s ..." % os.path.basename(path))
        with open(path, "rb") as handle:
            for rec, _start, length in zone_regions(path):
                zone, problems = read_zone_checked(handle, rec)
                if zone is None:
                    continue
                total += 1
                frames = rec["frames"]
                if zone["sample_end"] == frames - 1:
                    agree["end_is_frames_minus_1"] += 1
                if zone["loop_mode"] == 0:
                    agree["mode0"] += 1
                    if zone["loop_start"] == 0:
                        agree["loopstart0_when_mode0"] += 1
                if zone["loop_end"] == zone["sample_end"]:
                    agree["loopend_is_sampleend"] += 1
                if (zone["velocity_low"], zone["velocity_high"]) == (0, 127):
                    agree["full_velocity"] += 1
                if zone["key_low"] == zone["key_high"]:
                    agree["single_key"] += 1
                if zone["fixed_pitch"]:
                    agree["fixed"] += 1
                    if zone["key_low"] == zone["key_high"]:
                        agree["fixed_single_key"] += 1
                else:
                    agree["pitched"] += 1
                    if zone["key_low"] <= zone["root_note"] <= zone["key_high"]:
                        agree["root_in_range"] += 1
                    if zone["root_note"] == zone["key_low"]:
                        agree["pitched_root_eq_keylow"] += 1
                row = {"mse_file": rec["mse_file"], "preset": rec["preset"],
                       "sample_name": rec["sample_name"],
                       "frames": frames, "zone_bytes": length,
                       "effective_root": effective_root(zone),
                       "problems": "; ".join(problems)}
                row.update(zone)
                rows.append(row)
                if problems:
                    bad_rows.append(row)

    def pct(n):
        return 100.0 * n / (total or 1)

    print("\nparsed %d zone block(s)" % total)
    print("constraint violations: %d" % len(bad_rows))
    for row in bad_rows[:args.max]:
        print("  %-28s %-18s %s" % (row["preset"][:28],
                                    row["sample_name"][:18], row["problems"]))
    if len(bad_rows) > args.max:
        print("  ... %d more" % (len(bad_rows) - args.max))

    print("\ninternal agreement (these corroborate the field assignments):")
    print("  sample_end == frames-1                %6.1f%%"
          % pct(agree["end_is_frames_minus_1"]))
    print("  loop_mode == 0                        %6.1f%%"
          % pct(agree["mode0"]))
    print("  loop_start == 0 where loop_mode == 0  %6.1f%%  (of %d)"
          % (100.0 * agree["loopstart0_when_mode0"] / (agree["mode0"] or 1),
             agree["mode0"]))
    print("  loop_end == sample_end                %6.1f%%"
          % pct(agree["loopend_is_sampleend"]))
    print("  velocity range is full 0..127         %6.1f%%"
          % pct(agree["full_velocity"]))
    print("  key_low == key_high (single key)      %6.1f%%"
          % pct(agree["single_key"]))
    print("\nroot note and key tracking:")
    print("  fixed-pitch zones (drums/one-shots)   %6.1f%%  (%d)"
          % (pct(agree["fixed"]), agree["fixed"]))
    print("    of those, single key                %6.1f%%"
          % (100.0 * agree["fixed_single_key"] / (agree["fixed"] or 1)))
    print("  pitched zones                         %6.1f%%  (%d)"
          % (pct(agree["pitched"]), agree["pitched"]))
    print("    root_note within key_low..key_high  %6.1f%%"
          % (100.0 * agree["root_in_range"] / (agree["pitched"] or 1)))
    print("    root_note == key_low                %6.1f%%"
          % (100.0 * agree["pitched_root_eq_keylow"] / (agree["pitched"] or 1)))

    print("\nsample zones:")
    for row in rows[:args.sample]:
        print("  %-22s %-14s root=%-3d eff=%-3d %s key=%d-%-3d "
              "vel=%d-%-3d loop=%d %d-%d"
              % (row["preset"][:22], row["sample_name"][:14],
                 row["root_note"], row["effective_root"],
                 "FIXED" if row["fixed_pitch"] else "pitch",
                 row["key_low"], row["key_high"],
                 row["velocity_low"], row["velocity_high"], row["loop_mode"],
                 row["loop_start"], row["loop_end"]))

    print("\nVERDICT: %s" % (
        "zone layout holds for every record" if not bad_rows
        else "%d record(s) violate the layout" % len(bad_rows)))
    if args.csv:
        write_csv(args.csv, rows, [
            "mse_file", "preset", "sample_name", "frames", "zone_bytes",
            "root_note", "effective_root", "fixed_pitch", "key_low",
            "key_high", "velocity_low", "velocity_high", "loop_mode",
            "sample_start", "sample_end", "loop_start", "loop_end",
            "param76", "problems"])
        print("Wrote %s" % args.csv)
    return 0 if not bad_rows else 1


def cmd_zones(args):
    """Probe the zone data after each PCM block, slot by slot.

    Every candidate 4-byte slot is tested against constraints a real field
    must satisfy: a MIDI note is 0-127, a sample or loop position is
    0-frame_count, a mode flag is small. A slot that satisfies one constraint
    across thousands of records is almost certainly that field. Kept for
    investigating an unfamiliar library.
    """
    targets = each_mse(args.mse_dir)
    if not targets:
        return _no_banks(args.mse_dir)

    sizes, slots, dumps, total = {}, {}, [], 0
    for path in targets:
        progress("probing %s ..." % os.path.basename(path))
        with open(path, "rb") as handle:
            for rec, start, length in zone_regions(path):
                if args.filter and (args.filter.lower()
                                    not in rec["preset"].lower()
                                    and args.filter.lower()
                                    not in rec["sample_name"].lower()):
                    continue
                total += 1
                sizes[length] = sizes.get(length, 0) + 1
                handle.seek(start)
                block = handle.read(min(length, args.window))
                if len(dumps) < args.dump:
                    dumps.append((os.path.basename(path),
                                  rec["sample_name"], rec["frames"],
                                  length, block))
                frames = rec["frames"]
                for offset in range(0, (len(block) // 4) * 4, 4):
                    (signed,) = struct.unpack_from("<i", block, offset)
                    slot = slots.setdefault(offset, {
                        "n": 0, "zero": 0, "midi": 0, "in_frames": 0,
                        "eq_frames": 0, "neg": 0, "small": 0,
                        "min": None, "max": None, "values": set()})
                    slot["n"] += 1
                    if signed == 0:
                        slot["zero"] += 1
                    if 0 <= signed <= 127:
                        slot["midi"] += 1
                    if 0 <= signed <= frames:
                        slot["in_frames"] += 1
                    if signed == frames:
                        slot["eq_frames"] += 1
                    if signed < 0:
                        slot["neg"] += 1
                    if 0 <= signed <= 8:
                        slot["small"] += 1
                    slot["min"] = (signed if slot["min"] is None
                                   else min(slot["min"], signed))
                    slot["max"] = (signed if slot["max"] is None
                                   else max(slot["max"], signed))
                    if len(slot["values"]) < 24:
                        slot["values"].add(signed)

    print("\nzone block sizes (bytes between PCM end and next record):")
    for length in sorted(sizes, key=lambda s: -sizes[s])[:12]:
        share = 100.0 * sizes[length] / total
        print("  %8d bytes   %6d record(s)  %5.1f%%" % (length, sizes[length],
                                                        share))
    if len(sizes) > 12:
        print("  ... %d more distinct size(s)" % (len(sizes) - 12))
    print("  (larger gaps are expected at preset boundaries, where the gap "
          "also holds the next heading)")

    print("\ncandidate fields - %d record(s) examined" % total)
    print("  each row is one 4-byte little-endian slot in the zone block")
    print("  %midi = share in 0..127   %<=frames = share in 0..frame_count")
    print()
    print("  %-6s %7s %7s %9s %9s %7s %12s %12s"
          % ("offset", "%zero", "%midi", "%<=frames", "%=frames", "%neg",
             "min", "max"))
    hidden = 0
    for offset in sorted(slots):
        slot = slots[offset]
        n = float(slot["n"]) or 1.0
        if args.only_interesting and slot["zero"] == slot["n"]:
            hidden += 1
            continue
        print("  +%-5d %6.1f%% %6.1f%% %8.1f%% %8.1f%% %6.1f%% %12d %12d"
              % (offset, 100 * slot["zero"] / n, 100 * slot["midi"] / n,
                 100 * slot["in_frames"] / n, 100 * slot["eq_frames"] / n,
                 100 * slot["neg"] / n, slot["min"], slot["max"]))
    if hidden:
        print("  (%d always-zero slot(s) hidden)" % hidden)

    print("\ndistinct values seen (first 24 per slot) - a slot with few "
          "distinct small values is a mode/flag:")
    for offset in sorted(slots):
        values = sorted(slots[offset]["values"])
        if len(values) <= 12:
            print("  +%-5d %s" % (offset, values))

    if dumps:
        print("\nnon-zero slots per zone block (offset = value), first %d "
              "record(s):" % len(dumps))
        print("  every 4-byte slot that is not zero, so a field that is zero "
              "in one library\n  but used in another becomes obvious")
        for bank, name, frames, length, block in dumps:
            print("\n  %s / %s   frames=%d  zone_len=%d"
                  % (bank, name, frames, length))
            parts = []
            for offset in range(0, (len(block) // 4) * 4, 4):
                (value,) = struct.unpack_from("<i", block, offset)
                if value:
                    parts.append("+%d=%d" % (offset, value))
            if parts:
                line = "    "
                for part in parts:
                    if len(line) + len(part) > 76:
                        print(line)
                        line = "    "
                    line += part + "  "
                print(line.rstrip())
            else:
                print("    (all zero)")
    print("\nSend this output back and the field layout can be pinned down.")
    return 0


# --------------------------------------------------------------------------
# pitch / wavcheck / survey / fixture (the former standalone scripts)

def cmd_pitch(args):
    """Measure each WAV's pitch and compare it with its smpl root note."""
    from . import pitch      # imported here so only this command loads numpy
    files = find_files(args.dir, ".wav")
    if not files:
        print("No .wav files under %s" % args.dir, file=sys.stderr)
        return 1
    if not pitch.have_numpy():
        print("(numpy not found - using the pure-Python path, which is "
              "slower)", file=sys.stderr)

    rows, deltas = [], []
    for path in files:
        rel = os.path.relpath(path, args.dir)
        try:
            samples, rate, stored = pitch.read_analysis_chunk(path)
            hz, confidence = pitch.detect_pitch(samples, rate)
        except (OSError, ValueError) as exc:
            rows.append({"file": rel, "note": "", "detected_midi": "",
                         "hz": "", "confidence": "", "stored_root": "",
                         "delta": "", "verdict": "error: %s" % exc})
            continue
        midi = pitch.midi_from_hz(hz) if hz else None
        if midi is None or confidence < args.min_confidence:
            verdict = "unpitched/low confidence"
            delta = ""
        else:
            delta = ("" if stored is None
                     else int(round(midi)) - int(stored))
            verdict = ("matches stored root" if delta == 0
                       else "stored root off by %+d semitone(s)" % delta
                       if delta != "" else "no stored root")
            if delta != "":
                deltas.append(delta)
        rows.append({
            "file": rel,
            "note": pitch.note_name(midi) if midi is not None else "",
            "detected_midi": "" if midi is None else round(midi, 2),
            "hz": "" if hz is None else round(hz, 2),
            "confidence": round(confidence, 3),
            "stored_root": "" if stored is None else stored,
            "delta": delta, "verdict": verdict})

    width = min(34, max(len(r["file"]) for r in rows))
    print("%-*s %6s %8s %6s %6s %7s  %s"
          % (width, "file", "note", "midi", "conf", "root", "delta",
             "verdict"))
    for row in rows[:args.max]:
        print("%-*s %6s %8s %6s %6s %7s  %s"
              % (width, row["file"][-width:], row["note"],
                 row["detected_midi"], row["confidence"], row["stored_root"],
                 row["delta"], row["verdict"]))
    if len(rows) > args.max:
        print("... %d more (see the CSV)" % (len(rows) - args.max))

    if deltas:
        same = len(set(deltas)) == 1
        print("\n%d pitched sample(s) compared with a stored root" %
              len(deltas))
        print("  deltas seen: %s" % sorted(set(deltas)))
        if same and deltas[0] == 0:
            print("  VERDICT: every stored root matches the measured pitch")
        elif same:
            print("  VERDICT: every stored root is off by the SAME %+d "
                  "semitones - a constant offset, easily corrected"
                  % deltas[0])
        else:
            print("  VERDICT: stored roots disagree with measured pitch by "
                  "varying amounts - the root is not being read from the "
                  "right field")
    else:
        print("\nNo pitched samples with a stored root were found to compare.")

    if args.csv:
        write_csv(args.csv, rows, [
            "file", "note", "detected_midi", "hz", "confidence",
            "stored_root", "delta", "verdict"])
        print("Wrote %s" % args.csv)
    return 0


def cmd_wavcheck(args):
    """Find structurally broken WAV files and say how they are broken."""
    files = find_files(args.dir, ".wav")
    if not files:
        print("No .wav files under %s" % args.dir, file=sys.stderr)
        return 1

    rows, counts = [], {}
    for path in files:
        try:
            verdict, detail, facts = wav.inspect_wav(path)
        except OSError as exc:
            verdict, detail, facts = "ODD", "read error: %s" % exc, {}
        counts[verdict] = counts.get(verdict, 0) + 1
        row = {"file": os.path.relpath(path, args.dir), "verdict": verdict,
               "detail": detail}
        row.update({k: facts.get(k, "") for k in
                    ("file_size", "data_size", "data_present", "rate",
                     "channels", "bits")})
        rows.append(row)

    bad = [r for r in rows if r["verdict"] != "ok"]
    for row in bad[:args.max]:
        print("%-10s %-44s %s" % (row["verdict"], row["file"][-44:],
                                  row["detail"]))
    if len(bad) > args.max:
        print("... %d more problem file(s)" % (len(bad) - args.max))

    print("\n%d file(s) checked" % len(rows))
    for verdict in sorted(counts, key=lambda v: (v != "ok", -counts[v])):
        print("  %-12s %d" % (verdict, counts[verdict]))
    if args.csv:
        write_csv(args.csv, rows, [
            "file", "verdict", "detail", "file_size", "data_size",
            "data_present", "rate", "channels", "bits"])
        print("Wrote %s" % args.csv)
    return 0


def _survey_one(path, max_lines, names_only, min_len):
    with mse.mapped(path) as view:
        size = len(view)
        magic = bytes(view[:4])
        items = [(offset, text, is_path) for offset, text, is_path
                 in mse.scan_strings(view, min_len)]
    print("=" * 72)
    print("FILE   %s" % os.path.basename(path))
    print("SIZE   %d bytes" % size)
    print("MAGIC  %r%s" % (magic, "" if magic == mse.MAGIC
                           else "  <- UNEXPECTED"))
    paths = [s for s in items if s[2]]
    names = [s for s in items if not s[2]]
    print("STRINGS  %d total: %d PATH, %d NAME"
          % (len(items), len(paths), len(names)))
    print("-" * 72)
    shown = names if names_only else items
    for offset, text, is_path in shown[:max_lines]:
        print("0x%08x  %-4s  %s" % (offset, "PATH" if is_path else "NAME",
                                    text))
    if len(shown) > max_lines:
        print("... %d more (raise --max to see them)"
              % (len(shown) - max_lines))
    print()


def cmd_survey(args):
    """Dump every printable string in a bank with its offset, tagged PATH or
    NAME, so the preset/sample structure of an unfamiliar bank is visible."""
    if os.path.isdir(args.target):
        targets = each_mse(args.target)
    else:
        targets = [args.target]
    if not targets:
        print("No .mse files found at %s" % args.target, file=sys.stderr)
        return 1
    for path in targets:
        try:
            _survey_one(path, args.max, args.names_only, args.min_len)
        except OSError as exc:
            print("error reading %s: %s" % (path, exc), file=sys.stderr)
    return 0


def cmd_fixture(args):
    """Build the synthetic quirk fixture."""
    records, wavs = fixture.make_fixture(args.out_dir)
    print("built %d records, %d wavs" % (records, wavs))
    return 0
