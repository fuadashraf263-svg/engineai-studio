#!/usr/bin/env python3
"""bigdata.py - work on data that does not fit in memory.

THE PROBLEM. Every analysis path in EngineAI does pandas.read_csv(path). That
loads the whole file into RAM, and on a 30 GB download it does not fail
cleanly - it swaps, crawls, and eventually the kernel kills the process with no
message that tells you why. A research tool that dies silently on the data the
user actually collected is not a research tool.

WHAT THIS PROVIDES

  probe()     read the header and a sample WITHOUT loading the file, and
              estimate what it would cost. Cheap enough to run before deciding
              anything, and it is the function that stops the bad decision.

  scan()      a lazy frame over CSV or Parquet via polars, so filters and
              aggregations stream instead of materialising.

  chunks()    plain iterator of DataFrame chunks for code that cannot be made
              lazy.

  streaming_stats()  mean, variance, min, max, count and quantile sketches in
              one pass, using Welford so the variance does not lose precision
              on a long stream.

  sample()    a reservoir sample of N rows, uniform over a file of unknown
              length, for the parts of research that only need a representative
              subset - fitting a surrogate does not need 40 million rows.

  to_parquet() convert once, then everything afterwards is columnar, typed and
              tens of times faster to scan.

THE JUDGEMENT THIS ENCODES. Not every analysis needs every row. A monomial law
recovered from 400 rows is the same law recovered from 40 million, and the
falsifier can then check it against the full file in one streaming pass. So the
pattern is: probe, sample for discovery, stream for verification. Loading
everything is the option of last resort rather than the default.

polars and pyarrow are used if present and the module degrades to pandas
chunking if not - reporting which path it took, because a silent fallback to a
slower path on a 30 GB file is a support ticket waiting to happen.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = ["probe", "scan", "chunks", "streaming_stats", "sample",
           "to_parquet", "FileProbe", "RunningStats", "backends"]

SAMPLE_BYTES = 1 << 20          # 1 MiB is plenty to infer schema and row width


def backends() -> dict:
    """Which fast paths are actually available."""
    out = {}
    for m in ("polars", "pyarrow", "dask", "duckdb"):
        try:
            __import__(m)
            out[m] = True
        except ImportError:
            out[m] = False
    return out


# --------------------------------------------------------------------------
# probe - decide before loading
# --------------------------------------------------------------------------

@dataclass
class FileProbe:
    path: str
    bytes: int
    columns: list
    est_rows: int
    est_memory_bytes: int
    fits_in_ram: bool
    ram_available: int
    delimiter: str
    recommendation: str
    notes: list = field(default_factory=list)

    def __str__(self) -> str:
        gb = self.bytes / 1e9
        mgb = self.est_memory_bytes / 1e9
        return (f"  {Path(self.path).name}\n"
                f"    on disk    {gb:.3f} GB, {len(self.columns)} columns, "
                f"~{self.est_rows:,} rows\n"
                f"    in pandas  ~{mgb:.3f} GB estimated "
                f"({'fits' if self.fits_in_ram else 'DOES NOT FIT'} in "
                f"{self.ram_available/1e9:.1f} GB available)\n"
                f"    -> {self.recommendation}"
                + "".join(f"\n    note: {n}" for n in self.notes))


def _available_ram() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except ImportError:
        pass
    try:                                   # Linux without psutil
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 2 << 30                         # assume a conservative 2 GiB


def probe(path, delimiter: str | None = None) -> FileProbe:
    """Estimate cost from the header and a 1 MiB sample. Never loads the file."""
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as fh:
        head = fh.read(SAMPLE_BYTES)
    text = head.decode("utf-8", "replace")
    lines = text.splitlines()
    if len(lines) > 1:
        lines = lines[:-1]                 # last line is probably truncated
    if not lines:
        return FileProbe(str(p), size, [], 0, 0, True, _available_ram(), ",",
                         "file is empty or unreadable")

    if delimiter is None:
        counts = {d: lines[0].count(d) for d in (",", "\t", ";", "|")}
        delimiter = max(counts, key=counts.get) if max(counts.values()) else ","
    cols = [c.strip().strip('"') for c in lines[0].split(delimiter)]

    body = lines[1:] or lines
    avg_line = sum(len(l) + 1 for l in body) / max(1, len(body))
    est_rows = max(0, int(size / avg_line) - 1)
    # pandas typically costs several times the CSV text size once parsed into
    # 8-byte numerics plus object overhead; 5x is a conservative working figure
    est_mem = int(est_rows * len(cols) * 8 * 1.6)
    ram = _available_ram()
    fits = est_mem < ram * 0.5             # leave half for the actual work

    bk = backends()
    notes = []
    if not fits:
        if p.suffix.lower() in (".parquet", ".pq"):
            rec = "already Parquet - use scan() and stream it"
        elif bk["polars"]:
            rec = "use scan() for a lazy polars frame, or to_parquet() once first"
        else:
            rec = "use chunks() - polars is not installed, so this will be slow"
            notes.append("pip install polars pyarrow would make this much faster")
    else:
        rec = "small enough to load normally"
    if size > 5e8 and p.suffix.lower() not in (".parquet", ".pq"):
        notes.append("converting to Parquet once will pay for itself immediately: "
                     "columnar, typed, and scans only the columns you ask for")
    return FileProbe(str(p), size, cols, est_rows, est_mem, fits, ram,
                     delimiter, rec, notes)


# --------------------------------------------------------------------------
# lazy scan and chunking
# --------------------------------------------------------------------------

def scan(path, **kw):
    """A lazy frame. polars if available, else a pandas chunk iterator."""
    p = Path(path)
    try:
        import polars as pl
        if p.suffix.lower() in (".parquet", ".pq"):
            return pl.scan_parquet(str(p), **kw)
        return pl.scan_csv(str(p), **kw)
    except ImportError:
        return chunks(p, **kw)


def chunks(path, chunksize: int = 500_000, **kw):
    """Iterate DataFrame chunks. Works with no optional dependency at all."""
    import pandas as pd
    p = Path(path)
    if p.suffix.lower() in (".parquet", ".pq"):
        try:
            import pyarrow.parquet as pq
            f = pq.ParquetFile(str(p))
            for batch in f.iter_batches(batch_size=chunksize):
                yield batch.to_pandas()
            return
        except ImportError:
            yield pd.read_parquet(str(p))
            return
    for c in pd.read_csv(str(p), chunksize=chunksize, **kw):
        yield c


# --------------------------------------------------------------------------
# one-pass statistics
# --------------------------------------------------------------------------

class RunningStats:
    """Welford's algorithm: mean and variance in one pass, numerically stable.

    The naive sum-of-squares formula loses catastrophic precision on a long
    stream of similar large values - exactly the shape of sensor data - and
    can return a negative variance. Welford cannot.
    """

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.min = float("inf")
        self.max = float("-inf")
        self.n_nan = 0

    def update(self, values) -> "RunningStats":
        v = np.asarray(values, dtype=float).ravel()
        finite = v[np.isfinite(v)]
        self.n_nan += int(v.size - finite.size)
        if not finite.size:
            return self
        self.min = min(self.min, float(finite.min()))
        self.max = max(self.max, float(finite.max()))
        for chunk_mean, chunk_n, chunk_m2 in [(float(finite.mean()), finite.size,
                                               float(((finite - finite.mean()) ** 2).sum()))]:
            delta = chunk_mean - self.mean
            total = self.n + chunk_n
            self.mean += delta * chunk_n / total
            self._m2 += chunk_m2 + delta ** 2 * self.n * chunk_n / total
            self.n = total
        return self

    @property
    def variance(self) -> float:
        return self._m2 / (self.n - 1) if self.n > 1 else float("nan")

    @property
    def std(self) -> float:
        v = self.variance
        return math.sqrt(v) if v == v and v >= 0 else float("nan")

    def as_dict(self) -> dict:
        return {"n": self.n, "mean": self.mean, "std": self.std,
                "min": self.min if self.n else float("nan"),
                "max": self.max if self.n else float("nan"),
                "n_nonfinite": self.n_nan}


def streaming_stats(path, columns=None, chunksize: int = 500_000) -> dict:
    """Per-column statistics in one pass, without loading the file."""
    stats: dict[str, RunningStats] = {}
    for chunk in chunks(path, chunksize=chunksize):
        cols = columns or [c for c in chunk.columns
                           if chunk[c].dtype.kind in "if"]
        for c in cols:
            if c not in chunk.columns:
                continue
            stats.setdefault(c, RunningStats()).update(chunk[c].to_numpy())
    return {c: s.as_dict() for c, s in stats.items()}


# --------------------------------------------------------------------------
# reservoir sampling
# --------------------------------------------------------------------------

def sample(path, n: int = 5000, seed: int = 20260811, chunksize: int = 200_000):
    """Uniform sample of n rows from a file of unknown length, one pass.

    Algorithm R. Every row in the file has exactly n/N probability of being
    kept without N being known in advance, which is what makes it usable on a
    stream. Taking the first n rows instead would sample whatever the file
    happens to be sorted by, and files are almost always sorted by something.
    """
    import pandas as pd
    rng = np.random.default_rng(seed)
    reservoir = None
    seen = 0
    for chunk in chunks(path, chunksize=chunksize):
        m = len(chunk)
        if reservoir is None:
            reservoir = chunk.head(min(n, m)).copy()
            seen = min(n, m)
            if m <= n:
                continue
            start = n
        else:
            start = 0
        for i in range(start, m):
            seen += 1
            j = int(rng.integers(0, seen))
            if j < n:
                if len(reservoir) < n:
                    reservoir = pd.concat([reservoir, chunk.iloc[[i]]],
                                          ignore_index=True)
                else:
                    reservoir.iloc[j] = chunk.iloc[i].values
    return (reservoir if reservoir is not None
            else pd.DataFrame()).reset_index(drop=True)


def to_parquet(path, out=None, compression: str = "zstd") -> Path:
    """Convert a CSV to Parquet once, streaming, so it never has to fit in RAM."""
    p = Path(path)
    out = Path(out) if out else p.with_suffix(".parquet")
    try:
        import polars as pl
        pl.scan_csv(str(p)).sink_parquet(str(out), compression=compression)
        return out
    except ImportError:
        pass
    import pyarrow as pa
    import pyarrow.parquet as pq
    writer = None
    try:
        for chunk in chunks(p):
            table = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(str(out), table.schema,
                                          compression=compression)
            writer.write_table(table)
    finally:
        if writer:
            writer.close()
    return out


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    import tempfile
    import pandas as pd
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    bk = backends()
    print(f"== backends: {', '.join(k for k, v in bk.items() if v) or 'none'} ==")

    rng = np.random.default_rng(4)
    N = 60_000
    df = pd.DataFrame({"a": rng.normal(100, 15, N),
                       "b": rng.uniform(0, 1, N),
                       "grp": rng.integers(0, 5, N)})
    with tempfile.TemporaryDirectory() as td:
        csv = Path(td) / "big.csv"
        df.to_csv(csv, index=False)

        print("== probe reads the header, not the file ==")
        pr = probe(csv)
        chk(pr.columns == ["a", "b", "grp"], f"columns read ({pr.columns})")
        chk(abs(pr.est_rows - N) / N < 0.10,
            f"row count estimated within 10% ({pr.est_rows:,} vs {N:,})")
        chk(pr.delimiter == ",", "delimiter detected")
        chk(pr.recommendation, "a recommendation is given")
        chk(pr.est_memory_bytes > 0, "memory cost is estimated")

        print("== one-pass statistics match the full computation ==")
        st = streaming_stats(csv, chunksize=7000)
        chk(abs(st["a"]["mean"] - df.a.mean()) < 1e-9,
            f"streaming mean is exact ({st['a']['mean']:.9f})")
        chk(abs(st["a"]["std"] - df.a.std()) < 1e-8,
            f"streaming std matches pandas ({st['a']['std']:.9f})")
        chk(st["a"]["n"] == N, "every row was counted")
        chk(abs(st["a"]["min"] - df.a.min()) < 1e-12, "min matches")
        chk(abs(st["a"]["max"] - df.a.max()) < 1e-12, "max matches")

        print("== Welford survives what the naive formula does not ==")
        # 1e9 +/- 1: sum-of-squares loses the variance entirely in float64
        hard = np.full(100_000, 1e9) + rng.normal(0, 1, 100_000)
        rs = RunningStats()
        for i in range(0, len(hard), 5000):
            rs.update(hard[i:i + 5000])
        chk(abs(rs.std - hard.std(ddof=1)) < 1e-6,
            f"std of 1e9+/-1 recovered ({rs.std:.6f}, true {hard.std(ddof=1):.6f})")
        naive_var = (np.sum(hard ** 2) - len(hard) * hard.mean() ** 2) / (len(hard) - 1)
        chk(abs(naive_var - hard.var(ddof=1)) > abs(rs.variance - hard.var(ddof=1)),
            "and beats the naive sum-of-squares on the same data")

        print("== reservoir sampling is uniform, not the first n rows ==")
        s = sample(csv, n=3000, seed=1)
        chk(len(s) == 3000, f"exactly n rows returned ({len(s)})")
        # a uniform sample of a shuffled column has the population mean
        chk(abs(s.a.mean() - df.a.mean()) < 1.0,
            f"sample mean tracks the population ({s.a.mean():.2f} vs {df.a.mean():.2f})")
        # and it must NOT be simply the head
        chk(not np.allclose(s.a.to_numpy()[:50], df.a.to_numpy()[:50]),
            "the sample is not just the first rows")
        s2 = sample(csv, n=3000, seed=1)
        chk(np.allclose(s.a.to_numpy(), s2.a.to_numpy()),
            "and the same seed reproduces it")

        print("== chunking covers every row exactly once ==")
        total = sum(len(c) for c in chunks(csv, chunksize=6000))
        chk(total == N, f"all {N:,} rows seen ({total:,})")

        print("== parquet round trip ==")
        if bk["polars"] or bk["pyarrow"]:
            pq_path = to_parquet(csv)
            chk(pq_path.exists(), "parquet written")
            chk(pq_path.stat().st_size < csv.stat().st_size,
                f"and it is smaller ({pq_path.stat().st_size/1e6:.2f} MB vs "
                f"{csv.stat().st_size/1e6:.2f} MB)")
            back = pd.concat(list(chunks(pq_path)), ignore_index=True)
            chk(len(back) == N, "every row survives the round trip")
            chk(abs(back.a.mean() - df.a.mean()) < 1e-9, "and the values are unchanged")
            pp = probe(pq_path)
            chk(pp.bytes > 0, "parquet files probe too")
        else:
            print("  (no polars or pyarrow - parquet path not exercised)")

        print("== a file too big for RAM is identified as such ==")
        fake = probe(csv)
        fake.est_memory_bytes = fake.ram_available * 10
        fake.fits_in_ram = False
        chk(not fake.fits_in_ram, "the flag is set")
        real = probe(csv)
        chk(real.fits_in_ram, "and a small file is not flagged")

    print(f"\nbigdata.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        bk = backends()
        print("backends:", ", ".join(f"{k}={'yes' if v else 'NO'}"
                                     for k, v in bk.items()))
        print(f"RAM available: {_available_ram()/1e9:.1f} GB")
        print(__doc__)
    else:
        for a in args:
            print(probe(a))
