# FRA measured-scan PI tuning map (V1)

Open **FRA Loop Designer → 实测迭代调参 Map**. This separate workspace does not alter the existing FRA designer or apply parameters to hardware.

## Workflow

1. Import a baseline CSV. Explicitly choose the frequency/magnitude/phase columns (1-based in the UI), header-row count, separator and units. Supported units: Hz/kHz/rad/s, dB/linear amplitude, degrees/radians. Blank rows are ignored; all malformed numeric rows, NaN/Inf, nonpositive frequencies and duplicate frequencies are rejected. Unsorted rows are sorted with the original text and mapped numeric rows retained in the session.
2. Record the **actual PI used for that scan**: Kp, Ti in seconds, Ts in seconds, Tustin or backward Euler, controller input/output units and scaling. Ki = Kp/Ti. V1 does not infer a controller from the CSV and does not support non-PI parameter optimization.
3. Bind Vin, load, mode and a hardware/operating-point group label. Explicitly confirm signed complete loop gain L with characteristic equation 1+L=0 and enter the injection phase offset. Do not infer an instrument's sign convention from its brand or file extension. Unknown measurement meaning can be stored and displayed but cannot produce margin recommendations.
4. Review baseline/current/history-best measured scans, overlaid Bode plots and the Kp×Ti map. Circles are measurements; crosses and the next-point star are predictions. Empty space stays unknown. The numeric best label is a relative measured-band ranking, **not a stability certificate**. No dense interpolated safety surface or global optimizer is used.
5. Inspect the suggested local point, its predicted Fc/PM/GM, tradeoffs and low-confidence warning. The local grid uses 0.8×, 1× and 1.25× factors around the current PI. These are search increments, **not safe hardware limits**. The user must establish applicable bounds/protections, apply changes themselves and remeasure. No automatic connection or controller write exists.
6. Import the next scan with its actual parameters and matching operating point. Compare prediction against measurement within their overlapping frequency band. More than 3 dB or 15 degrees maximum discrepancy flags disagreement and suppresses further UI suggestions until a consistent repeat scan is supplied. These diagnostic thresholds are not safety limits. Check setup, sign/scaling, noise, operating point and nonlinearity.
7. Save/reopen the full JSON session. Export the current measured scan as normalized Hz/dB/raw-degree CSV. The JSON is authoritative for parameter identity, exact b/a coefficients, Ts, original raw text/units, operating point, exclusions and quality flags. CSV alone does not carry those semantics.

## Model and limits

For confirmed loop measurements only, the local hypothesis uses sampled complex data:

`P_eff(f) = L_measured(f) / C_actual(exp(j 2πf Ts))`

`L_predicted(f) = P_eff(f) · C_candidate(exp(j 2πf Ts))`

- Tustin PI: b = [Kp + Ki Ts/2, −Kp + Ki Ts/2], a = [1, −1]
- Backward-Euler PI: b = [Kp + Ki Ts, −Kp], a = [1, −1]
- No unique rational transfer-function fit is implied. Prediction never extrapolates beyond the measured band
- Exact operating-point, sample-period, discretization and controller-unit identity isolate scan groups. Different groups are not averaged or compared as improvements
- Frequencies at/above Nyquist and user-marked noisy/invalid points are excluded from predictions. Global quality flags disable predictions. To avoid inventing crossings across invalid gaps, margins/ranking are unavailable for scans containing excluded or out-of-band samples. Repair/re-export the measured band explicitly if appropriate
- Zero/ill-conditioned controller division, numerical underflow/overflow and malformed stored coefficients are rejected
- Fc and margins come from the existing FRA all-crossing analyzer. No 0 dB crossover means unknown, not optimized. Unobserved GM stays unknown; it is not reported as infinite or passing
- Candidate ranking penalizes log-frequency target error and deficits from the requested PM/GM, with an explicit penalty for unobserved GM. Every candidate can be worse than the baseline; the UI says when the next point is only exploratory
- Finite-band measurements cannot establish global stability, unmeasured resonances, saturation, large-signal response, delay/scaling not represented by the exact controller, or hardware safety

## Verification

Core tests use synthetic plant/PI loops to verify exact in-band recovery, discrete PI forms, held-out mismatch, grouping, phase wraps, no crossover, strict imports and session validation. Qt tests exercise the actual window, sparse plots, import validation/cancellation, repeated save/reopen, selecting old rounds and reopening the workspace. These are software tests, not instrument/hardware validation.
