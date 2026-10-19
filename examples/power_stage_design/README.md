# Reproducible Power Stage Designer inputs

These small historical 9.8.1-schema examples are accepted by 9.9.0 and always
recomputed on load. Saved result identities are provenance, not cached authority.
Open them in the Power Stage Designer workspace, or regenerate a report:

```bash
power-stage-design examples/power_stage_design/buck-loop.json --report buck-loop.html
```

- `buck.json`: 12V → 5V, 3A, 100kHz, 47µH, 100µF.
- `boost.json` and `flyback.json`: inspect their explicit input fields before use.
- `buck-loop.json`: Buck with a saved analog plant/compensator network.

They use ideal, declared model assumptions and are not hardware reference designs.
See [supported domains](../../docs/PSD_FEATURE_MATRIX.md).
