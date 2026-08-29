# Security Policy

## Supported versions

Security fixes are applied to the latest code on the default branch. Until the project publishes tagged releases, older commits are not separately supported.

## Reporting a vulnerability

Do not open a public issue containing exploit details, sensitive coordinates, credentials, or personal data.

Use GitHub's **Report a vulnerability** / private security-advisory flow for this repository. If that option is unavailable, open a minimal public issue asking the maintainer to establish a private contact channel; do not include reproduction details in that issue.

Include, where possible:

- affected commit or version;
- affected API, CLI, MCP tool, script, or dataset;
- impact and preconditions;
- minimal reproduction steps;
- suggested mitigation;
- whether the issue is already public.

The maintainers will acknowledge a complete report, assess severity, coordinate a fix, and disclose it after affected users have a reasonable opportunity to update.

## Scope

Security-sensitive areas include API/MCP input handling, generated HTML maps, filesystem writes, dependency vulnerabilities, data-source integrity, and accidental publication of restricted or sensitive geospatial attributes.
