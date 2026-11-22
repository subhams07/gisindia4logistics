# Routing remediation review evidence

Status: bounded internal-service remediation accepted after independent Opus closure review `9a099af2-48c1-4b0a-9d37-ac34e879b9ac` and coordinator tests; not operational routing acceptance. Checkout baseline `00b21b89ff0d1301e958e689876fb941bf7899ef`, with uncommitted stabilization edits.

## Verified mechanics

The coordinator independently ran the routing/cache/edge-case suite: **47 passed in 12.81s**, plus focused Ruff `E4,E7,E9,F`. Those results precede the subsequent feeder-only correction and are not final acceptance evidence.

After the feeder-only corrections and expanded metadata/boundary regressions, the combined admin/routing/cache run returned **162 passed in 14.74s**. The consolidated repository suite returned **586 passed in 81.36s**, with focused Ruff and diff checks passing. Stage-3 entry gates below remain mandatory before canonical public exposure.

Validated geometry suppression, zero-movement null geometry, projected-metre simplification, threshold-first compatible snapping, sparse marked bridges and atomic cache replacement. Real geometry tests bound projected Hausdorff error by 52 m, including five-decimal WGS84 rounding; they do not prove physical connectivity.

## Real endpoints, not city-name assumptions

| Probe | Origin latitude, longitude | Destination latitude, longitude | Model distance | Network distance | Origin / destination feeders | Direct geodesic distance | Route/direct ratio |
|---|---|---|---|---|---|---|---|
| Pune district representative point -> JNPT | 18.62541, 73.90267 | 18.9497, 72.951 | 131.19 km | 122.39 km | 5.24 / 3.56 km | 106.55740923444947 km | 1.23116732043807 |
| NDLS -> Jaipur district representative point | 28.642314, 77.22000399999999 | 26.98025, 75.69443 | 379.47 km | 370.68 km | 4.20 / 4.59 km | 237.73166525206264 km | 1.5962114243285797 |

Direct distances were calculated with `pyproj.Geod(ellps="WGS84").inv`. Both selected routes cross five marked synthetic bridges. Pune's total marked bridge distance is 0.86 km, maximum 342.1 m; Jaipur's is 0.34 km, maximum 187.9 m (rounded public diagnostics).

## Delhi detour diagnosis

The current snap objective selects origin node 46581 at longitude/latitude `77.256331594285, 28.663675310227124` and destination node 47822 at `75.73315473036129, 26.956244946050255`. It minimizes the sum of compatible feeder distances before computing a graph path, not total journey time or distance.

A bounded diagnostic held the destination node fixed, evaluated origin nodes within 25 km using one reverse Dijkstra traversal, and included feeder time at 35 km/h with the 2-axle truck factor 0.85. Node 20185 gives:

- origin snap longitude/latitude: `77.13972501087798, 28.673600486171015`;
- origin feeder: 8.440929089766422 km;
- network distance: 276.2960734369695 km;
- total model distance: 289.3270025267359 km;
- total model time: 5.111428460653452 h;
- five marked synthetic bridges.

This reproduces a materially shorter modelled alternative without changing the source graph. The nearest-compatible-snap objective can explain the detour; the district representative point alone cannot. The alternative is not a verified real-world route, nor a global optimum across both endpoint candidate sets.

The coordinator inspected projected path plots with origin/destination markers and marked bridge locations. The selected Delhi path first makes a substantial northward excursion, then goes west before heading south; the alternate snap path bypasses that initial excursion. No basemap or grade-separation evidence was used, so individual bridge legitimacy remains unverified.

**Known limitation:** the service must not be advertised as dependable door-to-door optimal routing. The loose 240–420 km regression band is a numerical stability check, not a reviewed road-distance benchmark. Do not tighten it to an invented expected value or widen it to hide drift. A calibrated endpoint-selection policy is required before stronger optimality claims.

## Stored graph semantics and stage-3 entry gates

- Full graph validation is currently performed per request. On 316,930 nodes and 932,722 directed edges, three measurements gave median validation 0.4714107000036165 s and median route calculation 0.5840365999611095 s. First route calculation was 1.551977299968712 s. This is a local latency probe, not concurrency/cold-install evidence.
- Mandatory before canonical public integration: move full validation to a stable validated store lifecycle with cheap request checks. Do not memoize mutable graph objects without invalidation/freeze semantics.
- Mandatory before canonical public integration: distinguish corrupt graph data (503) from impossible supported land routes (422). Current internal `RouteNotAvailableError` conflates those cases; adapters must not inherit that mapping blindly.
- Distinct locations sharing a snap node require feeder-only/no-network-traversal disclosure and low confidence. Exactly equal locations remain zero movement, with proximity rather than journey diagnostics.
- The 15 m vertex grid can merge separate roads without a marked bridge. The graph is undirected; one-way rules are ignored. Bridges between all way endpoints within 350 m can invent grade-separated, parallel-carriageway or switchback connectivity. Marked bridge metrics do not describe all modelled connectivity assumptions.
- Internal path vertices are grid-snapped, not exact upstream source coordinates. Later toll matching must exclude feeders and marked bridges and must not treat inferred NH labels as trusted plaza identity.

## Evidence artifacts

The read-only source-data probe generated `gis-routing-corridor-review.json` and `gis-routing-corridor-review.png` under Hermes scratch; the JSON contains full projected paths, node IDs and individual bridge endpoint coordinates. These are diagnostic artifacts, not committed source data or official routing references. No source datasets were regenerated.
