# Legacy Script Quarantine

This directory is reserved for clearly obsolete prototype scripts after they are
proven unreferenced by docs, tests, imports, and local workflows.

Nothing was moved here during the daily live stabilization pass. The current
safe cleanup policy is:

- keep daily tools in `telemetry-viewer\`
- keep legacy/debug/audit tools in place while they are referenced
- do not move tests, target libraries, profiles, plugin code, or batch/audit
  tools
- mark uncertain scripts in `docs\cleanup_report.md`

The daily workflow should ignore this directory.
