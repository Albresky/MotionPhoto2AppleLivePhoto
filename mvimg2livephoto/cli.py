"""Command-line interface for mvimg2livephoto."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .builder import _DEFAULT_WORKERS, convert_batch, convert_one, find_motion_photos
from .parser import parse_motion_photo


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_convert(args: argparse.Namespace) -> int:
    """Convert one or more Motion Photo files."""
    sources = [Path(p) for p in args.input]
    output_dir = Path(args.output)

    # Validate inputs
    for src in sources:
        if not src.exists():
            print(f"Error: file not found: {src}", file=sys.stderr)
            return 1

    total = len(sources)
    workers = args.workers
    print(f"Converting {total} file(s) → {output_dir}/  [workers={workers}]")

    def on_progress(completed, tot, path):
        print(f"  [{completed}/{tot}] {path.name}")

    successes, failures = convert_batch(
        sources,
        output_dir,
        hdr=args.hdr,
        workers=workers,
        on_progress=on_progress,
        skip_errors=not args.strict,
    )

    print(f"\nDone: {len(successes)} succeeded, {len(failures)} failed")
    for src, err in failures:
        print(f"  FAIL {src.name}: {err}", file=sys.stderr)

    return 0 if not failures else 2


def cmd_scan(args: argparse.Namespace) -> int:
    """Scan a directory and list Motion Photos."""
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Error: not a directory: {directory}", file=sys.stderr)
        return 1

    paths = find_motion_photos(directory, recursive=not args.no_recursive)
    if not paths:
        print("No Motion Photos found.")
        return 0

    print(f"Found {len(paths)} Motion Photo(s):")
    for p in paths:
        print(f"  {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mvimg2livephoto",
        description="Convert Xiaomi Motion Photos (MVIMG) to Apple Live Photos (HEIC+MOV)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # convert subcommand
    p_convert = sub.add_parser("convert", help="Convert Motion Photo(s) to Live Photo")
    p_convert.add_argument("input", nargs="+", help="Input MVIMG .jpg file(s)")
    p_convert.add_argument("-o", "--output", required=True, help="Output directory")
    p_convert.add_argument(
        "--hdr", action="store_true",
        help="Inject HDR GainMap into HEIC as Apple aux image (requires source to have GainMap)"
    )
    p_convert.add_argument(
        "-j", "--workers", type=int, default=_DEFAULT_WORKERS,
        help=f"Parallel worker threads (default: {_DEFAULT_WORKERS})"
    )
    p_convert.add_argument(
        "--strict", action="store_true",
        help="Abort on first error instead of skipping"
    )

    # scan subcommand
    p_scan = sub.add_parser("scan", help="Scan directory for Motion Photos")
    p_scan.add_argument("directory", help="Directory to scan")
    p_scan.add_argument("--no-recursive", action="store_true",
                        help="Do not recurse into subdirectories")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "convert":
        return cmd_convert(args)
    elif args.command == "scan":
        return cmd_scan(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
