# PFC V3 desktop integration

The TTPL **Run analysis** action now builds one `PFCEngineeringV3Result`.
Its frozen small-signal analysis supplies Bode, the installed Fc–PM solution
map, Exact H(z)/C99 and the shared-ngspice input. Its single line-cycle
simulation supplies the existing AC/switching views and V3 diagnostics.

Open **6. Control / Sensing / Bode → V3 Convergence / Distortion / Dual Loop**
to inspect convergence, PF/THD convention and evidence status, distortion
region estimates, zero-crossing limits, loop separation, Ms/Mt and phase-budget
consistency. The AC summary also reports convergence and PF/THD status.

Editing input fields does not reinterpret the previous waveform. Run analysis
again to update all results; changing the switching zoom uses the last completed
analysis configuration. Selecting a feasible solution-map controller continues
to run the same analysis entry point.

## Compatibility

- `run_pfc_engineering_v3_core(config)` retains its dictionary schema.
- `build_line_cycle_result(config)` and existing Smart Control APIs remain valid.
- `PFCMainWindow.result` and downstream view results remain legacy three-tuples.
- `_ttpl_ready` accepts legacy three-tuples and derives V3 diagnostics without
  repeating their line simulation.
- `build_pfc_engineering_v3` may reuse explicit `analysis` and `waveforms`;
  callers must supply waveforms from the same configuration. A mismatched
  supplied analysis/config is rejected.
- PF/THD now rejects non-finite data, non-positive/non-integral harmonic limits,
  and non-increasing/nonuniform time samples because its DFT is unweighted.

## Evidence limits

V3 convergence refers to the averaged closed-loop model, not bench validation.
PF/THD remains a finite-harmonic rectangular-window estimate; accepting a
uniform time grid does not establish that an arbitrary user-supplied window
contains an integer number of line cycles. Distortion causes and analytical
zero-crossing limits retain their approximation/unknown labels. The existing
line simulator still runs its configured firmware-controller model; this change
does not introduce a new exact-H(z) switching solver or hardware certification.
