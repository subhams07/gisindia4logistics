"""fetch_lgd_subdistricts.py — Snapshot the official LGD sub-district directory & Census 2011 crosswalk.

Queries the Local Government Directory web service (Ministry of Panchayati
Raj, lgdirectory.gov.in) for the sub-districts (tehsils / talukas / mandals /
circles / blocks) of every official LGD district listed in
`data/administrative/lgd_districts.csv`, and writes one row per unique LGD
sub-district with its official 5-digit Census 2011 code where available.

Output:
  data/administrative/lgd_subdistricts.csv

Usage: python scripts/fetch/fetch_lgd_subdistricts.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import geopandas as gpd
import pandas as pd
import requests

from scripts.clean.standardize import (
    CANONICAL_STATE_NAMES,
    DATA_DIR,
    LGD_STATE_CODES,
    http_session,
)

LGD_SUBDISTRICTS_URL = "https://lgdirectory.gov.in/webservices/lgdws/subdistrictList"
DISTRICTS_CSV = DATA_DIR / "administrative" / "lgd_districts.csv"
SUBDISTRICTS_GPKG = DATA_DIR / "administrative" / "india_subdistricts_lgd.gpkg"
CACHE_JSON = DATA_DIR / "raw" / "lgd_subdistricts_cache.json"
OUT_CSV = DATA_DIR / "administrative" / "lgd_subdistricts.csv"

STATE_CODE_TO_NAME: dict[int, str] = {
    code: CANONICAL_STATE_NAMES[key] for key, code in LGD_STATE_CODES.items()
}

OUTPUT_COLUMNS = [
    "state_code",
    "state",
    "district_code",
    "district",
    "sub_district_code",
    "sub_district_name",
    "census2011_code",
]

_THREAD_LOCAL = threading.local()


class PoliteRateLimiter:
    """Thread-safe shared pacer with coordinated global cooldown on HTTP 429."""

    def __init__(self, requests_per_sec: float = 2.0) -> None:
        self._lock = threading.Lock()
        self._min_interval = 1.0 / max(requests_per_sec, 0.1)
        self._next_allowed = 0.0
        self._cooldown_until = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                target = max(self._next_allowed, self._cooldown_until)
                if now >= target:
                    self._next_allowed = now + self._min_interval
                    return
                delay = target - now
            time.sleep(delay)

    def trigger_cooldown(self, seconds: float) -> None:
        with self._lock:
            now = time.monotonic()
            until = now + seconds
            if until > self._cooldown_until:
                self._cooldown_until = until
                self._next_allowed = max(self._next_allowed, until)
                # Gently slow steady-state pace after hitting a rate limit
                self._min_interval = min(self._min_interval * 1.25, 1.0)


def _get_session() -> requests.Session:
    """Return a per-thread requests.Session with project User-Agent."""
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = http_session()
        _THREAD_LOCAL.session = session
    return session


def clean_text(value: object) -> str:
    """Trim whitespace and collapse internal CR/LF/whitespace runs."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_census2011_code(value: object) -> str:
    """Normalize LGD census2011Code to 5-digit zero-padded text, or '' if null/zero."""
    raw = clean_text(value)
    if not raw:
        return ""
    if raw.isdigit():
        if int(raw) == 0:
            return ""
        return raw.zfill(5)
    return raw


def _parse_retry_after(resp: requests.Response, default_s: float) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(float(header), 5.0)
        except ValueError:
            pass
    return default_s


def _rows_from_payload(
    state_code: int,
    state_name: str,
    district_code: int,
    district_name: str,
    payload: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in payload:
        sub_code = int(item["subdistrictCode"])
        sub_name = clean_text(item.get("subdistrictNameEnglish"))
        c2011 = normalize_census2011_code(item.get("census2011Code"))
        rows.append(
            {
                "state_code": state_code,
                "state": state_name,
                "district_code": district_code,
                "district": district_name,
                "sub_district_code": sub_code,
                "sub_district_name": sub_name,
                "census2011_code": c2011,
            }
        )
    return rows


def fetch_district_payload(
    district_code: int,
    district_name: str,
    state_name: str,
    limiter: PoliteRateLimiter,
    max_retries: int = 8,
    timeout: int = 45,
) -> list[dict[str, object]]:
    """Fetch raw LGD sub-district JSON list for one district with 429-aware backoff."""
    session = _get_session()
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        limiter.wait()
        try:
            resp = session.post(
                LGD_SUBDISTRICTS_URL,
                data={"districtCode": district_code},
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait_s = _parse_retry_after(resp, default_s=65.0)
                print(
                    f"HTTP 429 on districtCode={district_code} (attempt {attempt}/{max_retries}); "
                    f"cooling down {wait_s:.0f}s...",
                    flush=True,
                )
                limiter.trigger_cooldown(wait_s)
                last_err = requests.HTTPError(
                    f"HTTP 429 for districtCode={district_code}", response=resp
                )
                continue
            if resp.status_code in (500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} for districtCode={district_code}",
                    response=resp,
                )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                raise ValueError(
                    f"Unexpected JSON type {type(payload).__name__} for districtCode={district_code}"
                )
            return payload
        except (requests.RequestException, ValueError, KeyError) as exc:
            last_err = exc
            if attempt < max_retries:
                sleep_s = min(2.0 ** (attempt - 1), 20.0)
                time.sleep(sleep_s)
    raise RuntimeError(
        f"Failed to fetch sub-districts for district_code={district_code} "
        f"({district_name}, {state_name}) after {max_retries} attempts: {last_err}"
    )


def load_lgd_districts(path: Path = DISTRICTS_CSV) -> pd.DataFrame:
    """Load official LGD districts and attach canonical state names."""
    df = pd.read_csv(path, dtype={"state_code": int, "district_code": int})
    missing_states = set(df["state_code"]) - set(STATE_CODE_TO_NAME)
    if missing_states:
        raise ValueError(f"Unknown LGD state_code(s) in {path}: {sorted(missing_states)}")
    df["state"] = df["state_code"].map(STATE_CODE_TO_NAME)
    df["district"] = df["district_name"].map(clean_text)
    return df[["state_code", "state", "district_code", "district"]].drop_duplicates(
        subset=["district_code"]
    )


def _load_cache(path: Path) -> dict[str, list[dict[str, object]]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, cache: dict[str, list[dict[str, object]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fetch_all_subdistricts(
    districts_df: pd.DataFrame,
    workers: int = 4,
    rate: float = 2.0,
    cache_path: Path = CACHE_JSON,
    refresh: bool = False,
) -> tuple[pd.DataFrame, list[tuple[int, str]]]:
    """Fetch sub-districts for all districts using a polite rate-limited worker pool."""
    tasks = list(
        districts_df[["state_code", "state", "district_code", "district"]].itertuples(
            index=False, name=None
        )
    )
    cache = {} if refresh else _load_cache(cache_path)
    pending = [t for t in tasks if str(t[2]) not in cache]
    if cache and pending:
        print(
            f"Loaded {len(tasks) - len(pending)}/{len(tasks)} districts from cache; "
            f"fetching {len(pending)} remaining...",
            flush=True,
        )
    elif cache and not pending:
        print(f"All {len(tasks)} districts loaded from cache ({cache_path}).", flush=True)

    limiter = PoliteRateLimiter(requests_per_sec=rate)
    completed = len(tasks) - len(pending)
    total = len(tasks)

    if pending:
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(fetch_district_payload, dc, dn, sn, limiter): (dc, dn)
                    for sc, sn, dc, dn in pending
                }
                since_save = 0
                for fut in as_completed(future_map):
                    dc, dn = future_map[fut]
                    payload = fut.result()
                    cache[str(dc)] = payload
                    completed += 1
                    since_save += 1
                    if since_save >= 50 or completed == total:
                        _save_cache(cache_path, cache)
                        since_save = 0
                    if completed % 100 == 0 or completed == total:
                        raw_total = sum(len(v) for v in cache.values())
                        print(
                            f"Fetched {completed}/{total} districts "
                            f"({raw_total} raw sub-district rows)...",
                            flush=True,
                        )
        finally:
            if cache:
                _save_cache(cache_path, cache)

    all_rows: list[dict[str, object]] = []
    empty_districts: list[tuple[int, str]] = []
    for sc, sn, dc, dn in tasks:
        payload = cache[str(dc)]
        if not payload:
            empty_districts.append((dc, dn))
        else:
            all_rows.extend(_rows_from_payload(sc, sn, dc, dn, payload))

    raw_df = pd.DataFrame(all_rows, columns=OUTPUT_COLUMNS)
    raw_df = raw_df.sort_values(
        ["state_code", "district_code", "sub_district_code"], kind="mergesort"
    )
    dup_count = int(raw_df.duplicated(subset=["sub_district_code"]).sum())
    if dup_count:
        print(f"Note: deduplicated {dup_count} duplicate sub_district_code row(s).", flush=True)
    out_df = raw_df.drop_duplicates(subset=["sub_district_code"], keep="first").reset_index(
        drop=True
    )
    return out_df, empty_districts


def validate_output(df: pd.DataFrame) -> None:
    """Validate schema, uniqueness, state coverage, and Census 2011 code formatting."""
    if list(df.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"Column mismatch: expected {OUTPUT_COLUMNS}, got {list(df.columns)}")
    if len(df) < 6000:
        raise ValueError(f"Suspiciously few sub-districts fetched: {len(df)}")
    if df["sub_district_code"].isna().any():
        raise ValueError("Null sub_district_code found")
    if df["sub_district_code"].duplicated().any():
        raise ValueError("Duplicate sub_district_code found after deduplication")
    if df["state_code"].nunique() != 36:
        raise ValueError(f"Expected 36 states/UTs, got {df['state_code'].nunique()}")
    non_empty_c2011 = df.loc[df["census2011_code"] != "", "census2011_code"]
    bad_c2011 = non_empty_c2011[~non_empty_c2011.str.fullmatch(r"\d{5}")]
    if not bad_c2011.empty:
        raise ValueError(
            f"Found {len(bad_c2011)} malformed census2011_code values, e.g. "
            f"{bad_c2011.head(5).tolist()}"
        )


def print_coverage_stats(
    df: pd.DataFrame,
    districts_count: int,
    empty_districts: list[tuple[int, str]],
    gpkg_path: Path = SUBDISTRICTS_GPKG,
) -> None:
    """Print summary and join coverage against india_subdistricts_lgd.gpkg."""
    total_rows = len(df)
    n_states = int(df["state_code"].nunique())
    n_districts = int(df["district_code"].nunique())
    c2011_non_empty = int((df["census2011_code"] != "").sum())
    c2011_unique = int(df.loc[df["census2011_code"] != "", "census2011_code"].nunique())
    c2011_pct = (100.0 * c2011_non_empty / total_rows) if total_rows else 0.0

    print("\n=== LGD Sub-District Directory Summary ===")
    print(
        f"Districts queried:          {districts_count} "
        f"({n_districts} with sub-districts, {len(empty_districts)} empty)"
    )
    if empty_districts:
        print(f"Empty districts:            {empty_districts}")
    print(f"States/UTs covered:         {n_states}")
    print(f"Unique LGD sub-districts:   {total_rows:,}")
    print(
        f"Non-empty census2011_code:  {c2011_non_empty:,} / {total_rows:,} "
        f"({c2011_pct:.2f}%; {c2011_unique:,} unique codes)"
    )

    if gpkg_path.exists():
        gpkg = gpd.read_file(gpkg_path, ignore_geometry=True)
        total_features = len(gpkg)
        coded_mask = gpkg["sub_district_code"].notna()
        coded_features = int(coded_mask.sum())
        raw_codes = gpkg.loc[coded_mask, "sub_district_code"].astype("string").str.strip()
        # Normalize pure numeric strings (including pre-PR#30 float strings like "285.0")
        # while preserving alphanumeric ABDB carveouts (e.g. "00285N001", "07314NOO1").
        gpkg_codes = raw_codes.map(
            lambda s: str(int(s.split(".", 1)[0])) if re.fullmatch(r"[0-9]+(?:\.0+)?", s) else s
        )
        numeric_codes = gpkg_codes[gpkg_codes.str.fullmatch(r"[0-9]+")]
        lgd_codes = set(df["sub_district_code"].astype("int64").astype(str))
        matched_features = int(gpkg_codes.isin(lgd_codes).sum())
        unique_gpkg_codes = set(gpkg_codes.unique())
        matched_unique_codes = len(unique_gpkg_codes & lgd_codes)

        c2011_str_set = {
            str(int(c)) for c in df.loc[df["census2011_code"] != "", "census2011_code"]
        }
        unmatched_codes = gpkg_codes[~gpkg_codes.isin(lgd_codes)]
        matched_via_c2011 = int(unmatched_codes.isin(c2011_str_set).sum())

        print(f"\n=== Join Coverage vs {gpkg_path.name} ({total_features:,} features) ===")
        print(
            f"Joined by sub_district_code (all features):   "
            f"{matched_features:,} / {total_features:,} "
            f"({100.0 * matched_features / total_features:.2f}%)"
        )
        print(
            f"Joined by sub_district_code (coded features): "
            f"{matched_features:,} / {coded_features:,} "
            f"({100.0 * matched_features / coded_features:.2f}%)"
        )
        if len(numeric_codes):
            print(
                f"Joined by sub_district_code (pure numeric):   "
                f"{matched_features:,} / {len(numeric_codes):,} "
                f"({100.0 * matched_features / len(numeric_codes):.2f}%)"
            )
        print(
            f"Unique gpkg sub_district_code matched:        "
            f"{matched_unique_codes:,} / {len(unique_gpkg_codes):,} "
            f"({100.0 * matched_unique_codes / len(unique_gpkg_codes):.2f}%)"
        )
        if matched_via_c2011:
            print(
                f"Additional unmatched features matching census2011_code: {matched_via_c2011:,}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent worker threads for LGD API requests (default: 4)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=2.0,
        help="Max requests per second across all workers (default: 2.0)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached district responses and re-fetch all districts",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUT_CSV,
        help=f"Output CSV path (default: {OUT_CSV})",
    )
    args = parser.parse_args()

    districts_df = load_lgd_districts(DISTRICTS_CSV)
    print(
        f"Querying LGD subdistrictList for {len(districts_df)} official LGD districts "
        f"({args.workers} workers, {args.rate:.1f} req/s)...",
        flush=True,
    )
    t0 = time.monotonic()
    df, empty_districts = fetch_all_subdistricts(
        districts_df,
        workers=args.workers,
        rate=args.rate,
        refresh=args.refresh,
    )
    elapsed = time.monotonic() - t0
    print(f"Completed in {elapsed:.1f}s.", flush=True)

    validate_output(df)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, lineterminator="\n")
    print(f"Wrote {len(df):,} rows -> {args.output}")

    print_coverage_stats(df, len(districts_df), empty_districts)


if __name__ == "__main__":
    main()
