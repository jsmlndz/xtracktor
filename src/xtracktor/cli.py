"""Command-line interface: argument definitions and dispatch only.

Flags match the original scripts exactly, so existing command lines carry
over with `python3 mse_index.py <mode>` replaced by `python3 xtracktor.pyz
<mode>`, and the other scripts becoming the pitch, wavcheck, survey and
fixture commands.
"""

import argparse
import sys

from . import __version__, commands
from .mse import MIN_STRING, ZONE_BLOCK_BYTES
from .util import invocation

ROOT_POLICIES = ("auto", "keylow", "keyhigh", "center")


def build_parser():
    parser = argparse.ArgumentParser(
        prog=invocation(),
        description="Extract, verify and inspect the samples in .mse banks.")
    parser.add_argument("--version", action="version",
                        version="xtracktor %s" % __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def command(name, func, help_text):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(func=func)
        return sp

    # --- bank inspection ---------------------------------------------------
    sp = command("verify", commands.cmd_verify,
                 "parse records, resolve presets, report health (run first)")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=400,
                    help="preset folders listed per bank (default 400)")

    sp = command("index", commands.cmd_index, "list every sample record")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=100)

    sp = command("zones", commands.cmd_zones,
                 "probe the zone data after each PCM block to identify its "
                 "fields")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--window", type=int, default=ZONE_BLOCK_BYTES,
                    help="bytes of each zone block to analyse (default %d, "
                         "the full observed block)" % ZONE_BLOCK_BYTES)
    sp.add_argument("--dump", type=int, default=6,
                    help="zone blocks to print as raw bytes (default 6)")
    sp.add_argument("--only-interesting", action="store_true",
                    help="hide slots that are always zero")
    sp.add_argument("--filter",
                    help="only examine zones whose preset or sample name "
                         "contains this text")

    sp = command("zonecheck", commands.cmd_zonecheck,
                 "parse every zone block and validate the field layout")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=20)
    sp.add_argument("--sample", type=int, default=12,
                    help="example zones to print (default 12)")

    sp = command("survey", commands.cmd_survey,
                 "dump the raw string layout of a bank, with byte offsets")
    sp.add_argument("target", help="a .mse file or a directory of them")
    sp.add_argument("--max", type=int, default=300,
                    help="max lines per file (default 300)")
    sp.add_argument("--names-only", action="store_true",
                    help="hide PATH entries; show only candidate names")
    sp.add_argument("--min-len", type=int, default=MIN_STRING,
                    help="minimum string length (default %d)" % MIN_STRING)

    # --- extraction --------------------------------------------------------
    sp = command("extract", commands.cmd_extract,
                 "write WAVs straight from the banks (dry run unless --apply)")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--group-by", choices=("preset", "folder"),
                    default="preset")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--force", action="store_true",
                    help="write even if the space check fails")
    sp.add_argument("--root-note", choices=ROOT_POLICIES, default="auto",
                    help="auto reads root_note from the zone and honours the "
                         "fixed-pitch flag; the others are manual overrides")
    sp.add_argument("--no-smpl", action="store_true",
                    help="do not write smpl chunks (root note / loop points)")
    sp.add_argument("--no-sfz", action="store_true",
                    help="do not write an SFZ instrument per preset")
    sp.add_argument("--no-zones", action="store_true",
                    help="ignore zone data entirely (audio only)")
    sp.add_argument("--no-keytrack", action="store_true",
                    help="omit pitch_keytrack=0 from SFZ regions, for "
                         "samplers with partial SFZ support")
    sp.add_argument("--max", type=int, default=40)

    sp = command("sfz", commands.cmd_sfz,
                 "rewrite only the .sfz files of an existing extraction "
                 "(dry run unless --apply)")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--group-by", choices=("preset", "folder"),
                    default="preset")
    sp.add_argument("--root-note", choices=ROOT_POLICIES, default="auto")
    sp.add_argument("--no-keytrack", action="store_true",
                    help="omit pitch_keytrack=0 from SFZ regions")
    sp.add_argument("--apply", action="store_true")

    # --- verification ------------------------------------------------------
    sp = command("audit", commands.cmd_audit,
                 "prove an extraction is complete and byte-accurate")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--group-by", choices=("preset", "folder"),
                    default="preset")
    sp.add_argument("--quick", action="store_true",
                    help="compare only the first 64 KiB of each sample")
    sp.add_argument("--zones", action="store_true",
                    help="also verify each smpl chunk against the bank zone")
    sp.add_argument("--root-note", choices=ROOT_POLICIES, default="auto",
                    help="root-note policy the extraction used")
    sp.add_argument("--all", action="store_true",
                    help="list every preset folder, not just mismatches")
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=40)

    sp = command("pitch", commands.cmd_pitch,
                 "measure each WAV's pitch and compare it with its stored "
                 "root note")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--min-confidence", type=float, default=0.3,
                    help="below this, treat the sample as unpitched")
    sp.add_argument("--max", type=int, default=60)

    sp = command("wavcheck", commands.cmd_wavcheck,
                 "find structurally broken WAV files and say how")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=30,
                    help="problem lines to print (default 30)")

    sp = command("match", commands.cmd_match,
                 "map already-extracted WAVs to preset and original name")
    sp.add_argument("--mse-dir", required=True)
    sp.add_argument("--wav-dir", required=True)
    sp.add_argument("--csv")
    sp.add_argument("--max", type=int, default=60)

    # --- testing -----------------------------------------------------------
    sp = command("fixture", commands.cmd_fixture,
                 "build the synthetic quirk fixture (the regression gate)")
    sp.add_argument("out_dir")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
