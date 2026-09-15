# FRA Deep Audit Addendum — Measurement Semantics and Stability Assumptions

## 1. `Complete Loop TS` means loop gain / return ratio, not closed-loop output transfer

The current de-embedding equation

`G_eq(jw) = H_scan(jw) / C_old(jw)`

is valid only when `H_scan` is the measured **loop gain / return-ratio transfer** around the operating closed loop, i.e. the quantity normally used by SFRA/Bode analyzers to report crossover frequency, phase margin and gain margin.

It is **not** valid if the uploaded file is the ordinary closed-loop reference-to-output transfer

`T(jw) = L(jw) / (1 + L(jw))`.

For a unity-feedback system, such a `T` record would first require

`L = T / (1 - T)`

before controller de-embedding. For non-unity feedback/injection arrangements, the reconstruction is injection-point dependent.

Therefore the GUI/documentation should use wording such as:

`Complete Loop Gain TS (measured return ratio; includes current controller)`

and should explicitly warn users not to upload a normal closed-loop reference/output transfer into the de-embedding path.

The supplied Bode100 record is consistent with a loop-gain/return-ratio measurement because it contains the usual 0-dB crossover and margin-style phase behavior, but the file format alone cannot prove injection semantics.

## 2. PM/GM/Ms/Mt do not by themselves prove absolute closed-loop stability for an unknown unstable plant

Raw frequency response can provide strong loop-shape evidence, but a general Nyquist stability statement also depends on the number of open-loop right-half-plane poles `P`.

The current V1/V1.5 margin engine does not infer `P` from FRA points. Therefore `PASS` means:

`measured-frequency robustness constraints passed under the normal power-converter assumption that the extracted open-loop plant does not contain unaccounted RHP poles`.

This is adequate for the common stable power-stage / stable inner-loop use case, but it must not be marketed as a topology-independent proof of closed-loop stability.

A future advanced path may add:

- explicit user input / model knowledge of open-loop RHP pole count,
- full Nyquist winding-number analysis,
- fitted-model pole checks where model confidence is sufficient,
- robust multi-operating-point analysis in V2.5.

## 3. Sampled Ms/Mt are lower-confidence between sparse FRA points

`Ms` and `Mt` are currently evaluated on measured frequency points. If a resonance is narrower than the scan spacing, the true peak can lie between samples. The software must therefore avoid treating a low sampled `Ms` as a mathematical upper bound.

Recommended behavior for future GUI hardening:

- display `Ms(sampled)` / `Mt(sampled)`,
- report local point density around crossover and resonances,
- mark sparse-data Auto results as REVIEW,
- never fabricate intermediate points without labeling the interpolation assumption.

## 4. Stable-only rational fitting is a model-class assumption

The V2 nonlinear fitter intentionally constrains fitted poles to the stable half-plane. That improves robustness for the intended stable power-converter plant use case, but it means the fitter cannot identify a genuinely unstable open-loop plant. A poor residual in such a case must remain LOW confidence rather than being forced into a misleading stable model.

This is another reason raw FRA + known system structure remains the authority and the fitted model remains secondary.

## 5. Status update after the controller-catalogue / plant-link work

Section 6.3 stated that Auto Power 2P2Z/3P3Z results "are not silently written back into those sliders" and that exact H(z) coefficients remain authoritative *"until a unified exact-H(z) host mode is added"*.

That host mode now exists. The FRA Loop Designer gained a `Custom H(z)` input mode, and Auto Design transfers a non-slider-reconstructable result as exact `b`/`a` coefficients instead of refusing to apply it. The legacy generic 2P2Z/3P3Z slider semantics are still unchanged, so the original warning remains correct for manual entry.

Section 7.3's step gate is now also enforced by the identified-plant link path: a fitted plant may only produce a closed-loop step when the analysed band satisfies the same `Fc/Fmin >= 10` / `Fmax/Fc >= 5` coverage requirement, plus fit confidence, model stability and closed-loop stability. Both step paths share one threshold definition.
