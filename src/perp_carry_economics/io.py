"""Atomic file writes, content hashes and a deterministic group-by."""

import hashlib
from pathlib import Path

import polars as pl
from polars.dataframe.group_by import GroupBy


def write_text_atomic(path: Path, text: str) -> None:
    """Write text via a .part file then a rename, so a crash never leaves a half-written target."""
    part = path.with_name(path.name + ".part")
    part.write_text(text, encoding="utf-8", newline="\n")  # same bytes on every platform
    part.replace(path)


def write_parquet_atomic(df: pl.DataFrame, path: Path) -> None:
    """Write df as parquet via a .part file then a rename."""
    part = path.with_name(path.name + ".part")
    df.write_parquet(part)
    part.replace(path)


def ordered_group_by(df: pl.DataFrame, key: str) -> GroupBy:
    """`df.group_by(key)` whose float sums are the same bits on every run.

    polars' parallel group sums add a group's rows in a different order each call; on rows
    stably sorted by the key each group is one contiguous slice, summed in row order. polars
    does not promise this, so `test_ordered_group_by_is_deterministic` guards it."""
    return df.sort(key, maintain_order=True).group_by(key, maintain_order=True)


def file_hash(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def text_hash(path: Path) -> str:
    """`file_hash` of a git-tracked text file read with LF line endings: a Windows checkout
    (core.autocrlf) has CRLF where every other checkout has LF."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
