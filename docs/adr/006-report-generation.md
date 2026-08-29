# ADR 006: Sandboxed Multi-Format Report Generation

## Status
Accepted

## Context
Logistics analysts, infrastructure consultants, and executives require shareable, board-ready exports (HTML, PDF, Excel) for corridor plans and district site comparisons. Generating files on the server introduces potential risks: heavy mandatory dependencies, arbitrary HTML/JavaScript execution in headless browsers, race conditions on shared files, and directory traversal vulnerabilities.

## Decision
1. **Optional Dependency Management**:
   - PDF (`reportlab>=4.0.0`) and Excel (`openpyxl>=3.1.0`) are declared as optional dependencies under `[project.optional-dependencies] reports`.
   - Core SDK and base API server run without them. If a user triggers PDF/Excel export without the package installed, a clean `ReportGenerationError` is raised with installation instructions (`pip install gisindia4logistics[reports]`).
2. **Deterministic, Cross-Platform Engine**:
   - **PDF**: Generated via pure Python `reportlab` Platypus Flowables, avoiding heavyweight headless browser installations or OS-dependent print drivers.
   - **Excel**: Multi-tab formatted workbooks generated via `openpyxl`.
   - **HTML**: Standalone self-contained HTML reports with embedded Leaflet maps and CSS.
3. **Sandboxed File Security**:
   - All generated report files are saved strictly under `outputs/reports/`.
   - Report files are named using random UUIDs (`{report_id}.pdf`, `{report_id}.xlsx`, `{report_id}.html`).
   - Download endpoints (`GET /api/v1/reports/{report_id}/{format}`) strictly validate that the resolved path resides within `outputs/reports/`, eliminating directory traversal (`../`).
   - User inputs are escaped to prevent XSS.

## Consequences
- Clean, standalone, publication-ready reports for executive briefings.
- Zero server security exposure to path traversal or arbitrary execution.
- Lightweight core installation footprint for standard SDK users.
