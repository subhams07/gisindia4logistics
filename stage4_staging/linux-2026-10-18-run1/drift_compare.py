"""Read-only drift comparison: staged run vs B0 (data/analysis). Prints a markdown report to stdout."""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

NEW = Path(sys.argv[1])
OLD = Path(sys.argv[2])
KEY = ["state", "district", "district_code"]
TABLES = ["nh_district_travel_time_summary.csv", "nh_district_port_matrix.csv",
          "district_freight_modal_split.csv", "district_port_hinterland_catchment.csv"]
ISOS = ["major_ports_isochrones.geojson", "mmlp_isochrones.geojson"]


def keyed(df):
    def norm(v):
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            return "NA"
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else str(v)
        except (TypeError, ValueError):
            return str(v)
    k = pd.Series(["|".join(norm(v) for v in row) for row in df[KEY].itertuples(index=False)], index=df.index)
    if k.duplicated().any():
        raise SystemExit(f"duplicate keys: {k[k.duplicated()].tolist()[:5]}")
    return df.set_index(k)


def table_report(name):
    a, b = keyed(pd.read_csv(NEW / name)), keyed(pd.read_csv(OLD / name))
    out = [f"### `{name}`", ""]
    only_new, only_old = a.index.difference(b.index), b.index.difference(a.index)
    out.append(f"Rows: new {len(a)}, B0 {len(b)}, matched {len(a.index.intersection(b.index))}, "
               f"only new {len(only_new)}, only B0 {len(only_old)}.")
    for label, ids in (("only in new", only_new), ("only in B0", only_old)):
        if len(ids):
            out.append(f"- Keys {label}: {', '.join(list(ids)[:10])}")
    common = a.index.intersection(b.index)
    a, b = a.loc[common], b.loc[common]
    out += ["", "| column | changed rows | NaN new→B0 mismatches | median abs Δ | max abs Δ | median rel Δ |", "|---|---|---|---|---|---|"]
    cat_lines = []
    for col in [c for c in a.columns if c not in KEY]:
        x, y = a[col], b[col]
        is_num = (lambda s: pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s))
        if is_num(x) and is_num(y):
            xn, yn = x.isna(), y.isna()
            nan_mis = int((xn != yn).sum())
            both = ~xn & ~yn
            d = (x[both] - y[both]).abs()
            changed = int((d > 1e-9).sum()) + nan_mis
            if changed == 0:
                continue
            dd = d[d > 1e-9]
            rel = (dd / y[both][d > 1e-9].abs().replace(0, np.nan)).dropna()
            out.append(f"| {col} | {changed} | {nan_mis} | {dd.median() if len(dd) else 0:.4g} | "
                       f"{dd.max() if len(dd) else 0:.4g} | {rel.median() if len(rel) else 0:.2%} |")
        else:
            xs, ys = x.fillna("<NA>").astype(str), y.fillna("<NA>").astype(str)
            diff = xs != ys
            if diff.any():
                trans = (ys[diff] + " → " + xs[diff]).value_counts().head(5)
                cat_lines.append(f"- `{col}`: {int(diff.sum())} rows changed; top transitions: "
                                 + "; ".join(f"{t} ({n})" for t, n in trans.items()))
    if len(out) and out[-1].startswith("|---"):
        out.append("| (no numeric changes) | | | | | |")
    out += [""] + (cat_lines or ["- No categorical/text changes."]) + [""]
    return out


def iso_report(name):
    a, b = gpd.read_file(NEW / name), gpd.read_file(OLD / name)
    out = [f"### `{name}`", "", f"Features: new {len(a)}, B0 {len(b)}. Columns only new: "
           f"{sorted(set(a.columns) - set(b.columns))}; only B0: {sorted(set(b.columns) - set(a.columns))}.", ""]
    hub = "hub_name" if "hub_name" in b.columns else "name"
    for g in (a, b):
        g["_k"] = g[hub].astype(str) + " @" + g["time_threshold_hours"].astype(float).map("{:g}h".format)
    ai, bi = a.set_index("_k"), b.set_index("_k")
    common = ai.index.intersection(bi.index)
    out.append(f"Matched by hub and threshold: {len(common)}; only new {len(ai.index.difference(bi.index))}; "
               f"only B0 {len(bi.index.difference(ai.index))}.")
    area_new = ai.loc[common].to_crs(7755).geometry.area / 1e6
    area_old = bi.loc[common].to_crs(7755).geometry.area / 1e6
    rel = ((area_new - area_old) / area_old.replace(0, np.nan)).dropna()
    out.append(f"Catchment area (km², EPSG:7755): total new {area_new.sum():,.0f} vs B0 {area_old.sum():,.0f} "
               f"({(area_new.sum() / area_old.sum() - 1):+.1%}); per feature median rel Δ {rel.median():+.1%}, "
               f"min {rel.min():+.1%}, max {rel.max():+.1%}.")
    big = rel[rel.abs() > 0.10].sort_values()
    if len(big):
        out.append(f"- Features with |area Δ| > 10%: {len(big)} — " + ", ".join(f"{k} {v:+.0%}" for k, v in big.head(8).items()))
    if "status" in bi.columns:
        st = (bi.loc[common, "status"].astype(str) + " → " + ai.loc[common, "status"].astype(str))
        st = st[bi.loc[common, "status"].astype(str) != ai.loc[common, "status"].astype(str)]
        out.append(f"- Status changes: {len(st)}" + (": " + "; ".join(f"{t} ({n})" for t, n in st.value_counts().items()) if len(st) else ""))
    else:
        out.append(f"- Status values in new: {a['status'].value_counts().to_dict()} (B0 has no status column)")
    return out + [""]


lines = ["## Drift: staged run vs B0", ""]
for t in TABLES:
    lines += table_report(t)
for i in ISOS:
    lines += iso_report(i)
pa = pd.read_parquet(NEW / "nh_district_travel_time_summary.parquet")
pc = pd.read_csv(NEW / "nh_district_travel_time_summary.csv")
lines.append(f"Parquet/CSV consistency (new summary): shapes {pa.shape} / {pc.shape}; same columns {list(pa.columns) == list(pc.columns)}.")
print("\n".join(lines))
