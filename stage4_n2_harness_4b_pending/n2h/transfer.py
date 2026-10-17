# ---- H1(192) -> H3 transfer, two domains (spec E). Stdlib only: usable by the controller and inside a payload. ----
import csv
import hashlib
import json
import os
import stat
from decimal import Decimal, InvalidOperation

SAMPLE_FILES = {"summary": "analysis/nh_district_travel_time_summary.csv",
                "matrix": "analysis/nh_district_port_matrix.csv"}
POPULATION_REL = "demographic/district_population_estimates.csv"
CONTEXT_REL = "context/full_identity_context.json"
IDENT = ("state", "district", "district_code")


class TransferStop(Exception):
    def __init__(self, code, detail=""):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _open_plain(path):
    """Read-only descriptor of a plain regular file; a symlink leaf is refused (payload-writable directories)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:  # includes ELOOP for a symlink leaf
        raise TransferStop("file_unreadable_or_symlink", "%s: %s" % (path, e)) from e
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise TransferStop("file_not_regular", path)
    return os.fdopen(fd, "rb")


def _sha(path):
    h = hashlib.sha256()
    with _open_plain(path) as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _within(path, root):
    return (path.rstrip("/") + "/").startswith(root.rstrip("/") + "/")


def _regular(path, root, what):
    """Resolved containment, no symlink leaf, regular file."""
    real = os.path.realpath(path)
    if real != os.path.normpath(path):
        raise TransferStop("path_not_canonical", "%s: %s" % (what, path))
    if not _within(real, os.path.realpath(root)):
        raise TransferStop("path_outside_root", "%s: %s" % (what, path))
    try:
        st = os.lstat(real)
    except OSError as e:
        raise TransferStop("file_missing", "%s: %s" % (what, e)) from e
    if not stat.S_ISREG(st.st_mode):
        raise TransferStop("file_not_regular", what)
    return real, st.st_size


def load_json(path):
    with open(path, encoding="ascii") as f:
        return json.load(f)


def read_table(path):
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if header is None:
            raise TransferStop("table_empty", path)
        rows = [dict(zip(header, r)) for r in rd]
    return header, rows


def cell_matches(canon, text):
    """Literal comparison of a frozen canonical cell against CSV text."""
    tag = canon[0]
    if tag == "null":
        return text == ""
    if tag == "s":
        return text == canon[1]
    if text == "":
        return False
    try:
        return Decimal(text) == Decimal(canon[1])
    except InvalidOperation:
        return False


def _identity_rows(rows):
    return [(r.get("state"), r.get("district"), r.get("district_code")) for r in rows]


def _check_identities(rows, ctx_rows, positions, what):
    if len(rows) != len(positions):
        raise TransferStop("sample_row_count_mismatch", "%s %d != %d" % (what, len(rows), len(positions)))
    for i, (r, pos) in enumerate(zip(rows, positions)):
        frozen = ctx_rows[pos]
        for j, col in enumerate(IDENT):
            if col not in r or not cell_matches(frozen[j], r[col]):
                raise TransferStop("sample_identity_drift", "%s row %d column %s" % (what, i, col))


def build_transfer(*, h1_dir, transfer_root, sample_rows, district_source, population_source, forbidden_roots=()):
    """Copy only the explicitly needed files into a disjoint scratch transfer root and validate BOTH domains.

    Sample domain: H1(n) summary/matrix identities equal the ordered first-n frozen positions.
    Full-context domain: the complete frozen identity context and the complete population source, bound to the
    hashes of the ORIGINAL sources (hashed here, by the parent), never trimmed. The two domains are NOT required
    to equal each other. Everything is checked before any model is entered; nothing falls back to B0.
    """
    h1_real, troot = os.path.realpath(h1_dir), os.path.realpath(transfer_root)
    if _within(h1_real, troot) or _within(troot, h1_real):
        raise TransferStop("transfer_root_not_disjoint", "H1 dir and transfer root overlap")
    for f in forbidden_roots:
        if _within(troot, os.path.realpath(f)) or _within(os.path.realpath(f), troot):
            raise TransferStop("transfer_root_forbidden", f)
    if os.path.exists(troot) and os.listdir(troot):
        raise TransferStop("transfer_root_not_empty", troot)
    os.makedirs(troot, exist_ok=True)
    pop_real, _ = _regular(population_source, os.path.dirname(os.path.realpath(population_source)), "population_source")
    dist_real, _ = _regular(district_source, os.path.dirname(os.path.realpath(district_source)), "district_source")
    srcs = {
        "summary": _regular(os.path.join(h1_real, "analysis", "nh_district_travel_time_summary.csv"), h1_real, "summary"),
        "matrix": _regular(os.path.join(h1_real, "analysis", "nh_district_port_matrix.csv"), h1_real, "matrix"),
        "context": _regular(os.path.join(h1_real, "context", "full_identity_context.json"), h1_real, "context"),
        "population": (pop_real, os.lstat(pop_real).st_size),
    }
    dest_rel = {"summary": SAMPLE_FILES["summary"], "matrix": SAMPLE_FILES["matrix"], "context": CONTEXT_REL,
                "population": POPULATION_REL}
    record = {"kind": "n2h_transfer_record", "sample_rows": sample_rows, "files": {}}
    for key, (src, size) in srcs.items():
        dst = os.path.join(troot, dest_rel[key])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        before = _sha(src)
        with _open_plain(src) as fi, open(dst, "xb") as fo:
            fo.writelines(iter(lambda: fi.read(1 << 20), b""))
            fo.flush()
            os.fsync(fo.fileno())
        os.chmod(dst, 0o444)
        after = _sha(dst)  # readback of the copy
        if before != after or _sha(src) != before:
            raise TransferStop("copy_hash_mismatch", key)
        record["files"][key] = {"source": src, "dest": dst, "bytes": size, "sha256": after,
                                "regular": True, "copy_complete": True, "readback_ok": True}
    validate_transfer(record, troot, district_source=dist_real, population_source=pop_real, sample_rows=sample_rows)
    ctx = load_json(record["files"]["context"]["dest"])
    record["full_context"] = {"rows": ctx["n"], "sha256": ctx["sha256"], "source_sha256": ctx["source_sha256"]}
    record["population"] = {"rows": len(read_table(record["files"]["population"]["dest"])[1]),
                            "source_sha256": record["files"]["population"]["sha256"]}
    record["sample_positions"] = list(range(sample_rows))
    rec_path = os.path.join(troot, "transfer_record.json")
    with open(rec_path, "x", encoding="ascii") as f:
        json.dump(record, f, sort_keys=True)
    os.chmod(rec_path, 0o444)
    return record


def validate_transfer(record, transfer_root, *, district_source, population_source, sample_rows, require_positions=False):
    """All checks that must precede entering either model. Re-runnable (the model entry repeats it)."""
    troot = os.path.realpath(transfer_root)
    files = record["files"]
    for key, meta in files.items():
        real, size = _regular(meta["dest"], troot, key)
        if size != meta["bytes"] or _sha(real) != meta["sha256"]:
            raise TransferStop("transfer_file_tampered", key)
    if _sha(population_source) != files["population"]["sha256"]:
        raise TransferStop("population_not_original", "population copy differs from the original source hash")
    ctx = load_json(files["context"]["dest"])
    if ctx.get("kind") != "n2h_full_identity_context" or len(ctx["rows"]) != ctx["n"]:
        raise TransferStop("context_malformed")
    if ctx["source_sha256"] != _sha(district_source):
        raise TransferStop("context_not_original", "context is not bound to the original district source hash")
    _check_context_against_source(ctx, district_source)
    if not (0 < sample_rows <= ctx["n"]):
        raise TransferStop("sample_rows_out_of_range")
    # full-context domain: complete and untrimmed (the sample is a strict or equal prefix; domains need not be equal)
    pop_rows = read_table(files["population"]["dest"])[1]
    expected_pop_rows = record.get("population", {}).get("rows")
    if expected_pop_rows is not None and len(pop_rows) != expected_pop_rows:
        raise TransferStop("population_trimmed", "%d != %d" % (len(pop_rows), expected_pop_rows))
    exp_ctx = record.get("full_context")
    if exp_ctx and (exp_ctx["rows"] != ctx["n"] or exp_ctx["sha256"] != ctx["sha256"]):
        raise TransferStop("context_trimmed_or_changed")
    positions = list(range(sample_rows))
    if require_positions and record.get("sample_positions") != positions:
        raise TransferStop("sample_positions_not_the_ordered_prefix", repr(record.get("sample_positions"))[:80])
    if "sample_positions" in record and record["sample_positions"] != positions:
        raise TransferStop("sample_positions_not_the_ordered_prefix", repr(record["sample_positions"])[:80])
    # sample domain
    for key in ("summary", "matrix"):
        header, rows = read_table(files[key]["dest"])
        if not set(IDENT) <= set(header):
            raise TransferStop("identity_columns_missing", key)
        _check_identities(rows, ctx["rows"], positions, key)
    return {"positions": positions, "context_rows": ctx["n"], "population_rows": len(pop_rows)}


def _json_cell_matches(canon, value):
    tag = canon[0]
    if tag == "null":
        return value is None
    if tag == "s":
        return isinstance(value, str) and value == canon[1]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return Decimal(repr(value) if isinstance(value, float) else str(value)) == Decimal(canon[1])


def _check_context_against_source(ctx, district_source):
    """The full identity context must be the COMPLETE original district identities (not a child-declared hash
    alone): same feature count and the same literal identities, in source order."""
    with open(district_source, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    if len(feats) != ctx["n"]:
        raise TransferStop("context_trimmed", "%d features in the original source, %d in the context" % (len(feats), ctx["n"]))
    for i, (ft, row) in enumerate(zip(feats, ctx["rows"])):
        props = ft.get("properties", {})
        for j, col in enumerate(IDENT):
            if not _json_cell_matches(row[j], props.get(col)):
                raise TransferStop("context_not_original", "row %d column %s" % (i, col))


def check_outputs_identity(record, out_csv, what):
    """After a model ran: its output rows must carry exactly the sample identities, in order."""
    ctx = load_json(record["files"]["context"]["dest"])
    rows = read_table(out_csv)[1]
    _check_identities(rows, ctx["rows"], record["sample_positions"], what)
    return len(rows)


def h3_call_plan(record, out_dir):
    """The exact real H3 arguments (paths only; the callables are the producers' own)."""
    root = os.path.dirname(os.path.dirname(record["files"]["summary"]["dest"]))
    return {
        "compute_modal_costs": {"output_csv": os.path.join(out_dir, "district_freight_modal_split.csv"),
                                "data_dir": root},
        "run_port_gravity_model": {"alpha": 1.2, "beta": 1.5, "matrix_csv": record["files"]["matrix"]["dest"],
                                   "population_csv": record["files"]["population"]["dest"],
                                   "output_csv": os.path.join(out_dir, "district_port_hinterland_catchment.csv"),
                                   "data_dir": root},
    }
