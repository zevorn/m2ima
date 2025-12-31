#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import Iterable, Iterator, Optional, TextIO, Tuple

import format

def run_git(repo: str, args: list[str], *, stdout=subprocess.PIPE) -> subprocess.Popen:
    return subprocess.Popen(
        ["git", "-C", repo, *args],
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_output(repo: str, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git failed: {' '.join(args)}")
    return proc.stdout


def normalize_iso_date(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty date")
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    raise ValueError(f"unsupported date format: {value!r}")


def iter_commits_for_range(
    repo: str,
    since: Optional[str],
    until: Optional[str],
    rev_range: Optional[str],
) -> list[Tuple[str, str]]:
    args = ["log", "--reverse", "--date=short", "--pretty=format:%H%x00%ad"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if rev_range:
        args.append(rev_range)
    out = git_output(repo, args)
    commits: list[Tuple[str, str]] = []
    for line in out.splitlines():
        sha, date = line.split("\x00", 1)
        commits.append((sha, date))
    return commits


def list_range(repo: str, since: Optional[str], until: Optional[str], rev_range: Optional[str]) -> int:
    args = ["log", "--reverse", "--date=short", "--pretty=format:%ad %h %s"]
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if rev_range:
        args.append(rev_range)
    sys.stdout.write(git_output(repo, args))
    if not sys.stdout.isatty():
        sys.stdout.write("\n")
    return 0


def get_first_parent(repo: str, commit: str) -> Optional[str]:
    out = git_output(repo, ["rev-list", "--parents", "-n", "1", commit]).strip()
    parts = out.split()
    if len(parts) <= 1:
        return None
    return parts[1]


@dataclass
class GitPatchBatch:
    tmp: TemporaryDirectory
    patch_dir: str
    patch_files: list[str]


def generate_patches(repo: str, rev_spec: str, *, include_root: bool) -> GitPatchBatch:
    tmp = TemporaryDirectory(prefix="m2a-patches-")
    args = [
        "format-patch",
        "-o",
        tmp.name,
        "--no-signature",
        "--start-number=1",
        "--quiet",
    ]
    if include_root:
        args.append("--root")
    args.append(rev_spec)
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tmp.cleanup()
        raise RuntimeError(proc.stderr.strip() or "git format-patch failed")

    patch_files = sorted(
        [os.path.join(tmp.name, name) for name in os.listdir(tmp.name) if name.endswith(".patch")]
    )
    if not patch_files:
        tmp.cleanup()
        raise RuntimeError("git format-patch produced no patch files")

    return GitPatchBatch(tmp=tmp, patch_dir=tmp.name, patch_files=patch_files)


def cleanup_patches(batch: GitPatchBatch) -> None:
    batch.tmp.cleanup()


@dataclass
class RollingWriter:
    out_dir: str
    prefix_date: str
    max_bytes: int

    part: int = 0
    current_path: Optional[str] = None
    current_fp: Optional[TextIO] = None
    current_bytes: int = 0

    def _open_next(self) -> None:
        self.close()
        self.part += 1
        filename = f"{self.prefix_date}-{self.part}.txt"
        self.current_path = os.path.join(self.out_dir, filename)
        self.current_fp = open(self.current_path, "w", encoding="utf-8", newline="\n")
        self.current_bytes = 0

    def write_record(self, record_text: str) -> None:
        record_bytes = len(record_text.encode("utf-8"))
        if self.current_fp is None:
            self._open_next()
        elif self.current_bytes > 0 and (self.current_bytes + record_bytes) > self.max_bytes:
            self._open_next()
        assert self.current_fp is not None
        self.current_fp.write(record_text)
        self.current_bytes += record_bytes

    def close(self) -> None:
        if self.current_fp is not None:
            self.current_fp.close()
            self.current_fp = None


def export(
    repo: str,
    out_dir: str,
    since: Optional[str],
    until: Optional[str],
    rev_range: Optional[str],
    prefix_date: Optional[str],
    max_mib: float,
) -> int:
    commits = iter_commits_for_range(repo, since, until, rev_range)
    if not commits:
        raise RuntimeError("no commits found for the given range")

    inferred_prefix = commits[0][1]
    prefix = normalize_iso_date(prefix_date) if prefix_date else normalize_iso_date(inferred_prefix)

    first_sha = commits[0][0]
    last_sha = commits[-1][0]
    include_root = False
    if rev_range:
        rev_spec = rev_range
    else:
        parent = get_first_parent(repo, first_sha)
        if parent is None:
            include_root = True
            rev_spec = last_sha
        else:
            rev_spec = f"{parent}..{last_sha}"

    os.makedirs(out_dir, exist_ok=True)
    max_bytes = int(max_mib * 1024 * 1024)
    if max_bytes <= 0:
        raise ValueError("--max-mib must be > 0")

    writer = RollingWriter(out_dir=out_dir, prefix_date=prefix, max_bytes=max_bytes)
    total = 0

    batch = generate_patches(repo, rev_spec, include_root=include_root)
    try:
        for patch_path in batch.patch_files:
            record = format.extract_record_from_patch_file(patch_path).to_text()
            writer.write_record(record)
            total += 1
    finally:
        cleanup_patches(batch)
        writer.close()

    sys.stderr.write(f"exported {total} messages into {writer.part} file(s)\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m2a",
        description="Export git format-patch messages into size-limited text files.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", default=".", help="Path to the target git repository.")
        p.add_argument("--since", help="Git date expression, e.g. 2025-01-01.")
        p.add_argument("--until", help="Git date expression, e.g. 2025-02-01.")
        p.add_argument("--range", dest="rev_range", help="Git revision range, e.g. A..B.")

    p_list = sub.add_parser("list", help="List commits in the selected range.")
    add_common_args(p_list)

    p_export = sub.add_parser("export", help="Export patch bodies + dates into txt file(s).")
    add_common_args(p_export)
    p_export.add_argument("-o", "--out-dir", required=True, help="Output directory.")
    p_export.add_argument(
        "--prefix-date",
        help="Override output filename prefix date (YYYY-MM-DD). Defaults to the first commit date in range.",
    )
    p_export.add_argument("--max-mib", type=float, default=64.0, help="Max size per output txt file.")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "list":
        return list_range(args.repo, args.since, args.until, args.rev_range)
    if args.cmd == "export":
        return export(
            args.repo,
            args.out_dir,
            args.since,
            args.until,
            args.rev_range,
            args.prefix_date,
            args.max_mib,
        )
    raise RuntimeError(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
