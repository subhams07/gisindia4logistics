# ADR 002: Deterministic Location Identity and Resolution

## Status
Accepted

## Context
Location inputs across India's supply chains can be ambiguous. Multiple districts share identical names across different states (e.g. *Bilaspur* in Chhattisgarh vs. Himachal Pradesh; *Aurangabad* in Maharashtra vs. Bihar; *Hamirpur* in UP vs. HP). Furthermore, logistics hubs are commonly referenced by acronyms (e.g. `JNPT`, `NSPT`, `Kandla`, `Dadri ICD`). Unrestricted fuzzy matching risks silently misrouting cargo across the subcontinent.

## Decision
We enforce strict, deterministic location resolution across all interfaces using a discriminated union:
```python
LocationReference = Annotated[
    CoordinateLocation | DistrictLocation | HubLocation,
    Field(discriminator="type")
]
```

### Resolution Rules
1. **Coordinates (`type: "coordinate"`)**:
   - Validated bounds: $-90 \le \text{latitude} \le 90$, $-180 \le \text{longitude} \le 180$.
2. **Districts (`type: "district"`)**:
   - Step 1: Exact integer `district_code` match (LGD).
   - Step 2: Exact case-insensitive `state` + `district` name match.
   - Step 3: Exact `district` name only if globally unique across all 781 Indian districts.
   - Step 4: If duplicate exists and no state is specified, raise `AmbiguousLocationError` listing matching states.
3. **Hubs (`type: "hub"`)**:
   - Step 1: Exact code match.
   - Step 2: Exact case-insensitive hub name and type.
   - Step 3: Explicit alias resolution via `data/aliases/location_aliases.yaml`.
   - Step 4: If unresolvable, raise `LocationNotFoundError`.

### Explicit Alias Registry
Aliases are maintained in a static YAML file (`data/aliases/location_aliases.yaml`), mapping common acronyms and trade names directly to canonical hub records.

## Consequences
- Impossible to silently route to the wrong district.
- Clear, actionable error messages returned to APIs, SDK users, and AI agents when disambiguation is required.
- Zero non-deterministic heuristic fuzzy search in core routing workflows.
