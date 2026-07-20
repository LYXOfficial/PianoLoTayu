#!/usr/bin/env python3
"""Extract one file or a directory prefix from a zip / tar / tar.xz archive."""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def _norm(name: str) -> str:
    return name.replace("\\", "/").lstrip("./")


def extract_one(archive: Path, member: str, dest: Path) -> None:
    member = _norm(member)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip" or archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            names = [_norm(n) for n in zf.namelist()]
            if member not in names:
                sample = ", ".join(names[:12])
                raise SystemExit(f"member not in zip: {member!r} (sample: {sample})")
            # zip stores original key; find original
            key = next(n for n in zf.namelist() if _norm(n) == member)
            with zf.open(key) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            names = [_norm(n) for n in tf.getnames()]
            if member not in names:
                sample = ", ".join(names[:12])
                raise SystemExit(f"member not in tar: {member!r} (sample: {sample})")
            key = next(n for n in tf.getnames() if _norm(n) == member)
            f = tf.extractfile(key)
            if f is None:
                raise SystemExit(f"not a regular file in tar: {member!r}")
            with f, open(dest, "wb") as dst:
                shutil.copyfileobj(f, dst)
    print(f"extracted {member} -> {dest}")


def extract_prefix(archive: Path, prefix: str, dest_dir: Path) -> int:
    """Extract all files under prefix/ into dest_dir/ (strip prefix)."""
    prefix = _norm(prefix).rstrip("/") + "/"
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    if archive.suffix.lower() == ".zip" or archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                p = _norm(name)
                if not p.startswith(prefix) or p.endswith("/"):
                    continue
                rel = p[len(prefix) :]
                if not rel:
                    continue
                out = dest_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += 1
    else:
        with tarfile.open(archive) as tf:
            for name in tf.getnames():
                p = _norm(name)
                if not p.startswith(prefix) or p.endswith("/"):
                    continue
                rel = p[len(prefix) :]
                if not rel:
                    continue
                member = tf.getmember(name)
                if not member.isfile():
                    continue
                out = dest_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(member)
                if f is None:
                    continue
                with f, open(out, "wb") as dst:
                    shutil.copyfileobj(f, dst)
                n += 1
    if n == 0:
        raise SystemExit(f"no files under prefix {prefix!r} in {archive}")
    print(f"extracted {n} files from {prefix} -> {dest_dir}/")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("one", help="extract a single member to a file path")
    one.add_argument("archive")
    one.add_argument("member")
    one.add_argument("dest")

    pref = sub.add_parser("prefix", help="extract all files under a prefix dir")
    pref.add_argument("archive")
    pref.add_argument("prefix")
    pref.add_argument("dest_dir")

    args = ap.parse_args(argv)
    if args.cmd == "one":
        extract_one(Path(args.archive), args.member, Path(args.dest))
    else:
        extract_prefix(Path(args.archive), args.prefix, Path(args.dest_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
