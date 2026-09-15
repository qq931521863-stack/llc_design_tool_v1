# FRA Loop Designer — Engineering Contract

## 1. Purpose

FRA Loop Designer turns measured complex frequency-response data into an engineering controller-design workflow without pretending that a measured Bode file contains more information than it actually does.

Core path:

```text
FRA file
 -> source-specific importer
 -> raw complex response
 -> measurement semantics
 -> optional controller de-embedding
 -> Equivalent Plant
 -> controller design / tuning
 -> L(jω)
 -> Fc / PM / GM / S / T
 -> exact H(z)
 -> C99
```

Raw measured points remain the authority for frequency-domain margins. Rational model identification is optional and is used for interpretation and model-derived time response.

## 2. Supported source formats

Current importers support:

- **Bode100 CSV**
- **SIMPLIS TXT** with frequency/gain/phase columns
- **Generic** frequency/gain/phase data

Source format and measurement meaning are separate concepts. A file parser knows how columns are encoded; it does not automatically know what transfer function was measured.

The importer preserves source metadata and phase handling so source-specific conventions are visible rather than silently discarded.

## 3. Measurement semantics

The two primary meanings are:

### Plant TS

The imported response is already the plant/equivalent response to be controlled:

```text
G_eq(jω) = G_meas(jω)
L_new(jω) = C_new(jω) · G_eq(jω)
```

### Complete Loop TS

The imported response contains the controller that was active during measurement:

```text
L_meas(jω) = C_old(jω) · G_rest(jω)
G_rest(jω) = L_meas(jω) / C_old(jω)
L_new(jω)  = C_new(jω) · G_rest(jω)
```

`C_old` is therefore an engineering input. A Bode file alone cannot reconstruct an unknown firmware controller.

A numerical de-embed/re-embed check can prove that complex division/multiplication is implemented correctly; it cannot prove that the user supplied the physically correct `C_old`.

## 4. Internal representation

Magnitude/phase are converted into a complex frequency response before controller multiplication:

```text
G(jω) = 10^(Gain_dB/20) · exp(j·phase)
L(jω) = G(jω) · C(jω)
```

This avoids treating gain and phase as unrelated curves and is required for correct controller de-embedding, sensitivity calculation and model identification.

The raw imported curve is immutable. Controller edits produce new derived responses; they do not rewrite the measurement.

## 5. Data-quality checks

Before design, the measurement layer checks conditions such as:

- positive/ordered frequency;
- duplicate points;
- NaN/Inf values;
- usable frequency coverage;
- phase continuity/unwrapping;
- local discontinuities/noise;
- crossover coverage and point density.

If the instrument provides quality/coherence metadata, it should be retained when available. Smoothing must not be used to hide a resonance or create artificial margin.

## 6. Stability calculation

The analyzer searches the complete usable measured band for:

- **all 0-dB gain crossovers**;
- phase margin at each gain crossover;
- all observed `-180° + 360°k` phase crossovers;
- gain margin at each valid phase crossover;
- critical/worst PM and GM;
- sensitivity `S = 1/(1+L)`;
- complementary sensitivity `T = L/(1+L)`;
- `Ms = max|S|` and `Mt = max|T|` on the analyzed data grid.

A response with multiple gain crossovers must not be summarized using only the first crossing.

If no phase crossover is observed inside the measured band, gain margin is reported as **not observed** rather than extrapolated as if it had been measured.

## 7. Manual design paths

The FRA GUI supports two manual workflows:

- **Quick Tune** — modify the existing controller while preserving its structure.
- **New Structure** — replace it with another structure from the shared Control Tools catalogue.

Controller parameters update the compensated Bode response immediately so zero/pole/gain changes can be evaluated against the original measured data.

The shared controller catalogue includes Integrator, PI, PIF, PID, PIDF, Type-II, Type-III, Modified PI, Lead, Lag, 1P1Z, 2P2Z, 3P3Z and General/custom forms.

## 8. Auto Design

Automatic design takes a target crossover and phase margin and synthesizes a controller candidate for the selected structure.

Engineering rules:

1. Try the requested `Fc` first.
2. Evaluate the candidate on the **raw FRA points**.
3. Check all gain crossovers, worst PM, available GM, sensitivity peaks and digital-controller validity.
4. If the requested target is not achievable with that controller structure, progressively reduce crossover and retry.
5. Report requested and achieved crossover separately; never label a fallback result as if the original target was met.

For power-compensator templates such as 2P2Z/3P3Z, the final result is transferred as the exact digital H(z) rather than silently mapping it into an unrelated legacy slider parameterization.

## 9. Rational model identification

Optional model identification fits a low-order continuous rational approximation to the complex measured response.

Current design contract:

- order up to 5 poles;
- stable real poles and stable complex-conjugate pole pairs;
- real numerator coefficients;
- optional pure delay;
- normalized polynomial variable for conditioning;
- weighted fit with extra emphasis around the control region;
- fit confidence and magnitude/phase error reporting.

The fitter works on complex relative error rather than fitting magnitude and phase independently.

Raw FRA remains the stability authority even when the fit looks visually excellent.

## 10. Model-derived step response

Time response is produced only from an identified/fitted rational model, not by applying an arbitrary finite-band IFFT to measured Bode data.

The GUI withholds step results when the available evidence is inadequate, including cases such as:

- LOW fit confidence;
- identified right-half-plane poles where the fit contract does not support the requested interpretation;
- failed loop-margin checks;
- unstable closed loop;
- insufficient measurement bandwidth around crossover.

Current bandwidth gate includes:

```text
Fc / Fmin >= 10
Fmax / Fc >= 5
```

A withheld step does not invalidate measured `Fc`/`PM`; it means the frequency data are insufficient to support that time-domain prediction.

## 11. Exact H(z) and code generation

The final controller is the exact discrete transfer function:

```text
C(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)
```

It can be exported using the shared C99 `float32_t` generator. Generated-C regression, when a compiler is available, verifies the discrete implementation against the Python response.

## 12. What FRA margins do not prove

Measured PM/GM/Ms/Mt are strong engineering evidence only within their assumptions and measured frequency band. They do not by themselves prove:

- absence of unmeasured right-half-plane plant poles;
- nonlinear large-signal stability;
- saturation/limit-cycle behavior;
- protection/state-machine correctness;
- stability at other input/load/temperature operating points;
- hardware robustness outside the measured configuration.

Multi-operating-point robust/uncertainty design is a separate layer and should not be implied by a single FRA trace.
