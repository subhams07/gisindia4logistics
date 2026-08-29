# ADR 005: Response Metadata and Data Version Manifest

## Status
Accepted

## Context
Commercial supply chain planning, academic research, and policy analysis require auditability. Users must know the exact data vintages (Census, road network, port throughput), analytical model versions, and legal limitations used to generate any given route, modal split, or district ranking.

## Decision
1. **Centralized Data Version Manifest (`data/version_manifest.json`)**:
   A lightweight static JSON file documenting:
   - `data_version`: Current repository data release (e.g. `"2026.08"`).
   - Datasets: Source, vintage, and relative path for each core layer.
   - Loaded once at startup without expensive per-request file checksum hashing.
2. **Standard Response Metadata (`ResponseMetadata`)**:
   Every new Phase 1 response payload includes a `metadata` block containing:
   - `api_version` (e.g. `"1.0.0"`).
   - `package_version` (e.g. `"1.0.0"`).
   - `model_version` (e.g. `"phase1-decision-workbench"`).
   - `data_version` (e.g. `"2026.08"`).
   - `generated_at_utc` (ISO 8601 UTC timestamp).
   - Specific vintages (`road_network_vintage`, `port_capacity_vintage`, `population_vintage`).
   - Links to `assumptions_url`, `sources_url`, and standard legal disclaimers.
3. **HTTP Response Headers**:
   FastAPI middleware attaches version headers to all HTTP responses without breaking existing JSON response envelopes:
   - `X-GIS4L-API-Version: 1.0.0`
   - `X-GIS4L-Data-Version: 2026.08`
   - `X-GIS4L-Model-Version: phase1-decision-workbench`

## Consequences
- Full scientific reproducibility across SDK, CLI, REST API, and MCP.
- Existing legacy endpoints retain backward compatibility without forced envelope restructuring.
- Downstream reports automatically embed correct provenance and disclaimers.
