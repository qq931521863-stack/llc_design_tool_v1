# LLC Two-Stage input examples

These two **input-only software fixtures** demonstrate the actual Stage-A FHA
screening and nominal Stage-B time-domain (TD) gate. They contain no saved
analysis, accepted candidate, waveform, or optimization history. Opening a file
requires a fresh run. The fixtures were written with `save_spec` and read back
with the production `load_spec` / `load_project` path.

They are explicit examples. They do not change the normal production defaults,
relax the validity thresholds, or establish a hardware-validated design.

## Choose an example

Both use a full-bridge primary, full-bridge synchronous rectification, 400 V
nominal bus, 53 V / 3 kW output, Q = 0.35, and a 100 kHz synthesized resonant tank.
Both explicitly set the hold-up end voltage to 320 V and bus capacitance to
0.0033 F (3300 µF). All other fields retain the production defaults at the source
revision below, except the stated Ln and primary turns.

- [full_bridge_nominal_warn.json](full_bridge_nominal_warn.json): Ln = 4.5,
  Np:Ns = 32:4. Stage A is feasible. The nominal Stage-B candidate is admitted
  with **WARN**, including about **13.045%** FHA/TD disagreement in peak
  magnetizing current. Inspect the warning; admission does not turn it into PASS.
- [full_bridge_nominal_rejected.json](full_bridge_nominal_rejected.json):
  Ln = 5.5, Np:Ns = 30:4. Stage A is feasible, but nominal Stage B intentionally
  rejects it with **FAIL**. Peak magnetizing-current disagreement is about
  **20.92046%**, above the unchanged **20%** failure gate. Periodic TD convergence
  alone does not make the candidate acceptable.

The reported values were checked against the numerical-kernel source revision
`a698dc533124e9c2d22342761cc2c56bf01a1f88`, with event-split Radau periodic solving
and continuous-time magnetizing-current extrema. Treat the fresh output from
your installed revision as the evidence; do not overwrite a new FAIL or UNKNOWN
with these expected values.

## Desktop: open, run, inspect, Apply, save, reopen, report

1. Launch the toolkit, enter the **LLC** workspace, and use **Load LLC JSON**
   (`加载 LLC JSON`) to open one of the files above. Check the topology, Ln,
   Np:Ns, hold-up voltage, and bus capacitance.
2. Open **LLC Two-Stage optimization** (`LLC Two-Stage 优化`). To reproduce the
   exact input rather than sweep neighboring Ln values, replace the
   **search-axis JSON** by copying the complete contents of the matching sidecar:
   [positive axes](full_bridge_nominal_warn.search_axes.json) or
   [rejected axes](full_bridge_nominal_rejected.search_axes.json). Paste it into
   the search-axis editor; these sidecars are not LLC project input files. Each
   was checked against the production CLI `--profile current` configuration and
   contains exactly one candidate. The positive axes are also shown below.
   Opening a plain spec normally seeds three Ln search values, so setting only
   the candidate count to 1 would test the first neighboring Ln value.

   ```json
   {
     "ln_values": [4.5],
     "q_values": [0.35],
     "fr_values_hz": [100000.0],
     "primary_turn_values": [32],
     "secondary_turn_values": [4],
     "primary_devices": ["REF_650V_SIC_45M"],
     "sr_parallel_values": [2]
   }
   ```

3. Set **Stage-A limit = 1**, **Top N = 1**, and verification scope to
   **nominal-point TD losses only** (`仅名义点 TD 损耗验证`). Keep the default
   ranking weights. Click **Run Stage A → B** (`运行 Stage A → B`) and wait for
   the actual result.
4. Select the row and inspect **FHA↔TD**, diagnostics, and per-point loss sources.
   The positive file should remain **WARN**. The rejected file should show
   **REJECTED / FAIL**; **Apply** and the selected-candidate report must remain
   unavailable for it. A different result is something to investigate.
5. For the admitted WARN candidate, review the limitations below, then explicitly
   click **Apply selected candidate** (`Apply 所选候选`). Selecting a row only
   previews it. After Apply, the design overview and losses must use the selected
   candidate's TD analysis and exact input values.
6. Save the applied project to a **new JSON file**, then reopen that file. Saved
   candidates are history only: they do not authorize Apply or an accepted
   candidate report. Run the Two-Stage request again, inspect the fresh WARN and
   source records, and explicitly Apply again.
7. Use the Two-Stage page's **Export selected candidate report**
   (`导出所选候选报告`) to save its report ZIP. Inspect
   `optimization_provenance.json`, `analysis.json`, `operating_points.csv`, and
   `LLC_calculation_book.md`. The candidate spec, waveform hash, workpoint scope,
   and loss-source labels must agree. Do not substitute a separate FHA-only run
   for this selected-candidate report.

For the negative file, the intended endpoint is the rejected candidate and its
diagnostics. There is no accepted Apply/report step to complete. Saving and
reopening its inputs or historical record does not bypass the gate.

## CLI: the same bounded current-spec request

Run from the repository root with its installed Python environment. Each output
directory must be **new and absent**, including on retries. Choose a new name
for every run so rejection evidence cannot be mixed with a prior accepted report.
`--profile current` fixes every search axis to the values in the input file.

Positive example:

```sh
python -m llc_design optimize \
  --config examples/llc_two_stage/full_bridge_nominal_warn.json \
  --profile current --two-stage --verification-scope nominal \
  --top-n 1 --max-candidates 1 \
  --output output/llc_two_stage_nominal_warn_run1
```

Expected: exit code **0**, `run_status.json` state **COMPLETE**, one admitted
candidate with `fha_td_validity: "WARN"`, and a report under
`selected_candidate/`. Check `two_stage_verification.json` and
`selected_candidate/optimization_provenance.json` for the same selected candidate
ID, exact inputs, and source records.

Negative example, run separately:

```sh
python -m llc_design optimize \
  --config examples/llc_two_stage/full_bridge_nominal_rejected.json \
  --profile current --two-stage --verification-scope nominal \
  --top-n 1 --max-candidates 1 \
  --output output/llc_two_stage_nominal_rejected_run1
```

Expected: a **nonzero exit code**, `run_status.json` state **REJECTED**,
`selected_candidate_id: null`, and `fha_td_validity: "FAIL"` in
`two_stage_verification.json`. The rejection reason is
`FHA↔TD MODEL_VALIDITY FAIL`. **No `selected_candidate/` accepted report is
produced.** Keep the rejection evidence; do not replace it with an FHA report or
weaken the gate to force acceptance.

## What the nominal result covers

- Only **1 of 7 operating points**, 400 V at full load, receives accepted TD
  waveform-based semiconductor and passive losses in the positive example.
  The other **6 points retain FHA**. Nominal-only acceptance does not establish
  critical-point or full-envelope TD validation.
- At the accepted nominal point, semiconductor, magnetic, and capacitor source
  records identify the TD waveform. **Commutation/ZVS terms remain FHA-based.**
  The report keeps these component-level distinctions and the waveform SHA-256.
- The exported operating-point frequency/current columns remain labeled
  `FHA_OPERATING_POINT`. Inspect the separate TD loss frequency, device RMS,
  component-source, and waveform-hash fields rather than interpreting all
  columns as TD measurements.
- The positive WARN retains a material model disagreement. The negative FAIL
  must stay rejected even though its periodic solve converges.
- Reference device/material data, magnetic geometry, thermal models, parasitics,
  nonlinear commutation, manufacturing tolerances, and actual hardware still
  require their own engineering validation. These are input fixtures, not
  measured reference converters, component purchase recommendations, or hardware
  safety qualification.

See the [main README](../../README.md),
[LLC physics-model scope](../../docs/LLC_PHYSICS_MODEL_V2.md), and
[validation report](../../docs/PSD_VALIDATION_REPORT.md) for the maintained
workflow and remaining limits.
