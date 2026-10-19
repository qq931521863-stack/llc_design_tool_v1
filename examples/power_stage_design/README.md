# Reproducible Power Stage Designer inputs

The original M1 examples retain the historical 9.8.1 schema; the optional-model
examples were saved with the current source. All are accepted by 9.9.0 and always
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

Optional, explicitly parameterized examples:
- `two-switch-floating-nodes.json`: simultaneous gates with finite Coss/Ron,
  piecewise-resistive diodes and winding resistance; floating-node DCM solved.
- `qr-delayed-valley.json`: selected second valley with finite-Ron discharge;
  frequency is a ceiling, not an imposed switching period.
- `psfb-finite-coss.json`: 400→48 V / 5 A, positive CCM, explicit Lm/Llk/dead time
  and constant parasitic capacitances. This example intentionally does **not**
  achieve ZVS on both bridge legs.

The listed component constants are illustrative test inputs, not vendor data or
recommended hardware. Inspect their source fields and model assumptions. Restore
recomputes the numerical model and may take tens of seconds; reports include all
branch evidence and can be several megabytes.
