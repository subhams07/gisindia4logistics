# Changelog

All notable code, API, data-schema, and documentation changes will be recorded here. The project follows [Semantic Versioning](https://semver.org/) for code interfaces; dataset vintages are versioned separately.

## [Unreleased]

### Added

- GitHub community, security, support, citation, and review templates.
- Regression tests for API semantics, validation, geodesic distance, MCP lifecycle, and map metric safety.
- MCP initialization and ping support.
- Configurable CORS origins and generated-output directory.
- Distance columns in the district-to-port matrix.

### Changed

- Nearest-facility calculations use WGS84 geodesic distance instead of degree-distance approximation.
- Requested target ports now select their own matrix-backed distance and drive time.
- Highway route distance is summed from path edges rather than inferred from a fixed average speed.
- Map-generation inputs and embedded values are validated and escaped.
- The 10 km rail-access score is exposed under a matching field name.

### Security

- MCP errors no longer return Python tracebacks.
- Generated MCP maps are written outside the committed data tree.
- Wildcard credentialed CORS is no longer the default.

> Earlier repository history predates this changelog. A tagged baseline release should document those changes before publication.
