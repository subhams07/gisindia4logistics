# ---- W0 population context preservation (spec C) -------------------------------------------------------
import hashlib
import json
import math
import os
from decimal import Decimal

import numpy as np
import pandas as pd

IDENTITY = ["state", "district", "district_code"]


class W0Stop(Exception):
    def __init__(self, code, detail=""):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _isnull(v):
    return v is None or v is pd.NA or (isinstance(v, (float, np.floating)) and math.isnan(v))


def canon_value(v):
    """Lossless, JSON-safe literal form of one identity scalar (no normalisation, no coercion)."""
    if _isnull(v):
        return ["null"]
    if isinstance(v, (bool, np.bool_)):
        return ["b", bool(v)]
    if isinstance(v, (int, np.integer)):
        return ["i", str(int(v))]
    if isinstance(v, (float, np.floating)):
        return ["f", repr(float(v))]
    if isinstance(v, str):
        return ["s", v]
    raise W0Stop("identity_value_type", type(v).__name__)


def lit_eq(a, b):
    """Literal equality of two scalars: nulls match nulls, strings exactly, numbers exactly (Decimal)."""
    na, nb = _isnull(a), _isnull(b)
    if na or nb:
        return na and nb
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, (bool, np.bool_)) or isinstance(b, (bool, np.bool_)):
        return False
    return Decimal(repr(float(a)) if isinstance(a, (float, np.floating)) else int(a)) == \
        Decimal(repr(float(b)) if isinstance(b, (float, np.floating)) else int(b))


def rows_of(frame):
    cols = [frame[c].tolist() for c in IDENTITY]
    return [[canon_value(v) for v in rec] for rec in zip(*cols)]


class FrozenContext:
    """Deep, identity-columns-only copy of the FULL primary frame (taken before any float64 cast)."""

    def __init__(self, frame, source_path=None, source_sha256=None):
        missing = [c for c in IDENTITY if c not in frame.columns]
        if missing:
            raise W0Stop("context_missing_columns", ",".join(missing))
        self.n = len(frame)
        self.rows = rows_of(frame)
        self.index = [canon_value(v) for v in frame.index.tolist()]
        self.dtypes = {c: str(frame[c].dtype) for c in IDENTITY}
        self.frame = pd.DataFrame({c: frame[c].tolist() for c in IDENTITY}, index=frame.index.copy())
        self.source_path = None if source_path is None else str(source_path)
        self.source_sha256 = source_sha256
        self.sha256 = hashlib.sha256(json.dumps(
            {"rows": self.rows, "index": self.index, "dtypes": self.dtypes}, sort_keys=True,
            separators=(",", ":")).encode("ascii")).hexdigest()

    def check_prefix(self, frame, positions):
        """The incoming (sampled) frame must be exactly the frozen rows at `positions`: literal identities,
        order and index labels."""
        if len(frame) != len(positions):
            raise W0Stop("incoming_length_mismatch", "%d != %d" % (len(frame), len(positions)))
        if rows_of(frame) != [self.rows[p] for p in positions]:
            raise W0Stop("incoming_identity_mismatch", "identities or order differ from the frozen context")
        if [canon_value(v) for v in frame.index.tolist()] != [self.index[p] for p in positions]:
            raise W0Stop("incoming_index_mismatch", "index labels differ from the frozen context")

    def to_json(self):
        return {"kind": "n2h_full_identity_context", "n": self.n, "rows": self.rows, "index": self.index,
                "dtypes": self.dtypes, "sha256": self.sha256, "source_path": self.source_path,
                "source_sha256": self.source_sha256}


def sha256_path(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class W0Witness:
    """Real-delegating witnesses at the two by-value bindings the matrix producer reaches:
    `geopandas.read_file` (only the canonical district source is intercepted, by resolved-path equality) and
    `nh_travel_matrix.district_population`. Instrumentation is disclosed and restored."""

    def __init__(self, matrix_module, producer_io_module, canonical_district_path, sample_rows,
                 population_rows_expected=None, real_read_file=None):
        self.m = matrix_module
        self.pio = producer_io_module
        self.canonical = os.path.realpath(canonical_district_path)
        self.sample_rows = sample_rows
        self.population_rows_expected = population_rows_expected
        self._real_read = real_read_file
        self.ctx = None
        self.events = []
        self.installed = False

    def install(self):
        if self.installed:
            raise W0Stop("already_installed")
        real_pop = self.pio.district_population
        if self.m.district_population is not real_pop:
            raise W0Stop("matrix_binding_not_real_helper", "nh_travel_matrix.district_population is not producer_io's")
        self._saved_read = self.m.gpd.read_file
        self._saved_pop_binding = self.m.district_population
        self._real_pop = real_pop
        if self._real_read is None:
            self._real_read = self._saved_read
        self.m.gpd.read_file = self._read
        self.m.district_population = self._population
        self.installed = True
        self.events.append({"event": "installed", "bindings": ["geopandas.read_file", "nh_travel_matrix.district_population"]})
        return self

    def restore(self):
        if not self.installed:
            return
        self.m.gpd.read_file = self._saved_read
        self.m.district_population = self._saved_pop_binding
        self.installed = False
        self.events.append({"event": "restored"})
        if self.m.gpd.read_file is not self._saved_read or self.m.district_population is not self._saved_pop_binding:
            raise W0Stop("restore_failed")

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.restore()
        return False

    def _is_canonical(self, path):
        try:
            return os.path.realpath(str(path)) == self.canonical
        except (OSError, TypeError, ValueError):
            return False

    def _read(self, path, *a, **k):
        if not self._is_canonical(path):
            return self._real_read(path, *a, **k)  # every other geopandas read: untouched real delegation
        if self.ctx is not None:
            raise W0Stop("canonical_district_read_twice")
        full = self._real_read(path, *a, **k)
        self.ctx = FrozenContext(full, source_path=self.canonical, source_sha256=sha256_path(self.canonical))
        n = self.ctx.n if self.sample_rows is None else int(self.sample_rows)
        if not 0 < n <= self.ctx.n:
            raise W0Stop("sample_rows_out_of_range", "%d of %d" % (n, self.ctx.n))
        self.positions = list(range(n))
        prefix = full.iloc[:n].copy()
        self.events.append({"event": "canonical_read", "full_rows": self.ctx.n, "returned_rows": n,
                            "context_sha256": self.ctx.sha256})
        del full  # no full geometry is retained by the witness
        return prefix

    def _population(self, districts, population):
        if self.ctx is None:
            raise W0Stop("population_before_context")
        self.ctx.check_prefix(districts, self.positions)
        if self.population_rows_expected is not None and len(population) != self.population_rows_expected:
            raise W0Stop("population_trimmed", "%d != %d" % (len(population), self.population_rows_expected))
        full_series = self._real_pop(self.ctx.frame, population)  # real helper, full identities, untrimmed population
        sel = full_series.iloc[self.positions].copy()
        sel.index = districts.index  # restore the sampled index
        if len(sel) != len(districts) or sel.name != "pop_2011":
            raise W0Stop("population_result_shape")
        self.events.append({"event": "population", "sample_rows": len(districts), "population_rows": len(population),
                            "full_rows": self.ctx.n})
        return sel


def verify_readback(ctx, positions, csv_path=None, parquet_path=None):
    """Real CSV/Parquet readback must preserve the frozen literal identities. A lossy round trip (for example
    an integer code above 2**53 cast to float64) is a STOP; production code is never edited to bypass it."""
    expect = [ctx.rows[p] for p in positions]
    report = {}
    if csv_path is not None:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        got = list(zip(df["state"], df["district"], df["district_code"]))
        report["csv"] = _compare(expect, got, textual=True)
    if parquet_path is not None:
        df = pd.read_parquet(parquet_path)
        got = list(zip(df["state"].tolist(), df["district"].tolist(), df["district_code"].tolist()))
        report["parquet"] = _compare(expect, got, textual=False)
    return report


def _cell_ok(canon, value, textual):
    tag = canon[0]
    if tag == "null":
        return (value == "") if textual else _isnull(value)
    if tag == "s":
        return isinstance(value, str) and value == canon[1]
    if textual:
        if value == "":
            return False
        try:
            return Decimal(value) == Decimal(canon[1])
        except ArithmeticError:
            return False
    if _isnull(value):
        return False
    return Decimal(repr(float(value))) == Decimal(canon[1])


def _compare(expect, got, textual):
    if len(expect) != len(got):
        raise W0Stop("readback_row_count", "%d != %d" % (len(expect), len(got)))
    for i, (e, g) in enumerate(zip(expect, got)):
        for j, name in enumerate(IDENTITY):
            ok = _cell_ok(e[j], g[j], textual)
            if not ok and name == "district_code" and e[j][0] in ("i", "f"):
                raise W0Stop("lossy_code_roundtrip", "row %d code %s read back as %r" % (i, e[j][1], g[j]))
            if not ok:
                raise W0Stop("readback_identity_mismatch", "row %d column %s: %r vs %r" % (i, name, e[j], g[j]))
    return {"rows": len(expect), "ok": True}


class FullContextPopulation:
    """H3 gravity: adapt the `producer_io.district_population` MODULE attribute that port_hinterland_model
    reads. The real helper is called on the complete frozen identity context and the untrimmed population;
    sample rows are then selected through the sample-to-full-context membership map. Any mismatch STOPs."""

    def __init__(self, producer_io_module, context_json, sample_positions, population_rows_expected=None):
        self.pio = producer_io_module
        self.ctx_rows = context_json["rows"]
        self.ctx_index = context_json["index"]
        self.positions = list(sample_positions)
        self.population_rows_expected = population_rows_expected
        self.frame = None
        self.events = []
        self.installed = False
        self.context_json = context_json

    def _frame(self):
        rows = []
        for r in self.ctx_rows:
            rows.append([None if c[0] in ("null",) else (c[1] if c[0] == "s" else (int(c[1]) if c[0] == "i" else float(c[1])))
                         for c in r])
        idx = [None if c[0] == "null" else (c[1] if c[0] == "s" else (int(c[1]) if c[0] == "i" else float(c[1])))
               for c in self.ctx_index]
        return pd.DataFrame(rows, columns=IDENTITY, index=idx)

    def install(self):
        real = self.pio.district_population
        code = getattr(real, "__code__", None)
        if (getattr(real, "__module__", None) != self.pio.__name__ or getattr(real, "__qualname__", None) != "district_population"
                or code is None or os.path.realpath(code.co_filename) != os.path.realpath(self.pio.__file__)
                or getattr(real, "__closure__", None) or hasattr(real, "__wrapped__")):
            raise W0Stop("module_attribute_not_real_helper", "producer_io.district_population is not the real function")
        self._real = real
        self.frame = self._frame()
        self.pio.district_population = self._wrapped
        self.installed = True
        return self

    def restore(self):
        if self.installed:
            if self.pio.district_population != self._wrapped:
                self.installed = False
                self.pio.district_population = self._real
                raise W0Stop("module_attribute_replaced_while_installed", "restored the real helper")
            self.pio.district_population = self._real
            self.installed = False
            if self.pio.district_population is not self._real:
                raise W0Stop("restore_failed")

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.restore()
        return False

    def membership(self, sample):
        """Sample row i must be the frozen identity at positions[i] (literal, in order)."""
        if len(sample) != len(self.positions):
            raise W0Stop("sample_length_mismatch", "%d != %d" % (len(sample), len(self.positions)))
        for i, (rec, pos) in enumerate(zip(zip(*[sample[c].tolist() for c in IDENTITY]), self.positions)):
            if not 0 <= pos < len(self.ctx_rows):
                raise W0Stop("sample_position_outside_context", str(pos))
            frozen = self.frame.iloc[pos]
            for c, v in zip(IDENTITY, rec):
                if not lit_eq(frozen[c], v):
                    raise W0Stop("sample_not_member_of_context", "row %d column %s" % (i, c))
        return self.positions

    def _wrapped(self, df_matrix, population):
        self.membership(df_matrix)
        if self.population_rows_expected is not None and len(population) != self.population_rows_expected:
            raise W0Stop("population_trimmed", "%d != %d" % (len(population), self.population_rows_expected))
        full = self._real(self.frame, population)
        sel = full.iloc[self.positions].copy()
        sel.index = df_matrix.index
        self.events.append({"event": "gravity_population", "sample_rows": len(df_matrix),
                            "full_rows": len(self.frame), "population_rows": len(population)})
        return sel


def module_origin_report(modules, repo_root):
    """Imported modules must resolve inside the repository (no stale install)."""
    out = {}
    for m in modules:
        f = os.path.realpath(getattr(m, "__file__", "") or "")
        out[m.__name__] = {"file": f, "inside_repo": f.startswith(os.path.realpath(repo_root) + "/")}
    return out
