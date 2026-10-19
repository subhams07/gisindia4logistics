# Contributions

GISIndia4Logistics is a **personal hobby project**. Pull requests and outside
contributions are **not being accepted**, and unsolicited pull requests may be
closed without review. You are welcome to fork the project under its licences
(MIT for code; see `DATA_LICENSE.md` for data).

If you hold rights in a dataset included here and want it attributed
differently or removed, open an issue with the *Source or license concern*
template. Report security problems privately as described in `SECURITY.md`.

The standards and checklists below are the maintainer's own working rules for
adding data. They are kept here because `docs/legal_compliance.md` refers to
them.

## Data standards (required for new datasets)

1. **CRS**: EPSG:4326 (WGS 84). Projected outputs may additionally be provided
   in EPSG:7755 or UTM zones, but 4326 is the canonical format.
2. **Join keys**: use LGD (Local Government Directory) state/district codes
   where applicable; attribute names snake_case.
3. **Metadata**: every dataset needs a `catalog.yaml` entry with: name,
   category, source_url, license, vintage, resolution, and how it is obtained.
4. **Size**: no hard limit; files >20 MB should use a Git LFS-routed format
   (`*.gpkg`, `*.parquet`, `*.zip`) per `.gitattributes`.
5. **License**: only commit data that is openly licensed or redistributable.
   Ambiguous-license sources are documented in `docs/sources.md` with a fetch
   script, not committed.

## Adding a dataset — checklist

- [ ] Source URL and license verified and recorded in `catalog.yaml` + `docs/sources.md`
- [ ] Data standardized per `docs/data_standards.md`
- [ ] Fetch script added under `scripts/fetch/` (if not committed directly)
- [ ] README catalog table updated
- [ ] Data audit run locally and exact command/result recorded in the change
- [ ] API/SDK/CLI/MCP behavior changes include regression tests
- [ ] Generated files are reproducible from committed scripts and parameters

## Legal & policy checks (India geospatial — see docs/legal_compliance.md)

- [ ] No defence/military/security-installation features (DST negative list of
      sensitive attributes)
- [ ] No DEM/terrain/gravity data finer than 2021 thresholds (~1 m horizontal /
      3 m vertical DEM, 1 milli-gal gravity) without documented SoI clearance
- [ ] No ground-truthing inside notified Negative List of Areas
- [ ] Boundary data labelled indicative; maps carry the boundary disclaimer

## Reporting data errors

Open an issue with: dataset name, affected feature(s), expected vs actual value,
and a source supporting the correction.

Use the repository's data-quality issue template. Report vulnerabilities
privately as described in `SECURITY.md`, and follow `CODE_OF_CONDUCT.md` in all
project interactions.
