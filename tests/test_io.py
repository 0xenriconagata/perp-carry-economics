import numpy as np
import polars as pl

from perp_carry_economics import io


def test_write_text_atomic_leaves_no_part_file(tmp_path):
    target = tmp_path / "a.txt"
    io.write_text_atomic(target, "a\n")
    assert target.read_bytes() == b"a\n"
    assert list(tmp_path.glob("*.part")) == []


def test_write_parquet_atomic_round_trips_without_part_file(tmp_path):
    df = pl.DataFrame({"a": [1, 2], "b": [0.5, 1.5]})
    target = tmp_path / "x.parquet"
    io.write_parquet_atomic(df, target)
    assert pl.read_parquet(target).equals(df)
    assert list(tmp_path.glob("*.part")) == []


def test_text_hash_ignores_crlf(tmp_path):
    crlf, lf = tmp_path / "crlf.txt", tmp_path / "lf.txt"
    crlf.write_bytes(b"a\r\nb")
    lf.write_bytes(b"a\nb")
    assert io.text_hash(crlf) == io.text_hash(lf)
    assert io.file_hash(crlf) != io.file_hash(lf)


def test_ordered_group_by_is_deterministic():
    rng = np.random.default_rng(0)
    df = pl.DataFrame({"k": rng.integers(0, 7, 200_000), "v": rng.normal(size=200_000)})
    runs = [io.ordered_group_by(df, "k").agg(pl.col("v").sum()) for _ in range(5)]
    assert all(run.equals(runs[0]) for run in runs[1:])
    # equals() compares -0.0 == 0.0 and NaNs loosely; compare the raw bits too
    assert all(run["v"].to_numpy().tobytes() == runs[0]["v"].to_numpy().tobytes() for run in runs)
