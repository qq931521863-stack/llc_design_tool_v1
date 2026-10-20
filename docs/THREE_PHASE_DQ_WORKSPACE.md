# Two-level three-phase dq control-workspace checkpoint

This is a software-model development checkpoint. Formal publication, native
package acceptance and hardware qualification remain separate. The implementation
is specific to the two-level rectifier; it does not use Vienna modulation, the
Vienna midpoint approximation or an LLC power-stage model.

## Model and controller ownership

- Current is positive from the grid to the DC bus. Park transforms are
  amplitude-invariant and cosine-aligned. The existing nonlinear ABC plant,
  SRF/DDSRF/DDSOGI PLL choices, precharged-bus startup, six-switch SVPWM,
  current-vector limiting, Id slew and physical capacitor-energy integration
  remain available.
- The old runtime used forward Euler in the current PI and backward Euler in
  the voltage PI, while the old Bode/C99 used Tustin for both. Native defaults
  now preserve the actual old runtime: current b = [3.77, -3.74485], voltage
  b = [0.51, -0.5], with a = [1, -1]. `controller_for()` supplies the same exact
  normalized coefficients to runtime, lifted model, Bode, sessions and C99.
- `ExactController` accepts causal ascending-z^-1 FIR/IIR polynomials up to order
  16. It preserves leading numerator zeros, rejects nonfinite values and
  mismatched sample rates, and carries its source string. No PI refit occurs.
- Current d/q axes have separate canonical DF2T states. A tracking correction
  enters the first state after each update. Voltage uses the same recurrence;
  its state freezes while PLL lock is absent and its first-state tracking
  correction reflects the Id-command clamp. This is an explicit nonlinear
  policy, not an antiwindup stability theorem for arbitrary imported H(z).
- Native voltage state is initialized to the old P/(1.5 Vd) value. Imported
  controllers start from zero. Import is not a bumpless-transfer guarantee.
  C99 exposes Reset/Peek/Advance, including initial state, external correction
  and enabled/freeze arguments; the application owns saturation and scheduling.

## Exact local state model and validity

Current plant: Gi(s) = 1/(L s + R), with controller output in volts and error in
amperes. The local plant is exactly ZOH-discretized at the current rate. PWM
command delay is a genuine integer FIFO, not an extra undocumented hold factor.

The energy model retains the inductor-energy term:

Gvi(s) = [1.5(Vd - 2 R Id) - 1.5 L Id s] / [C Vdc s + dPload/dV].

The fast state contains current, bus voltage, every current-controller DF2T
state, every current-sensor state and the PWM queue. An augmented matrix power
lifts this complete state and a held Id reference to the voltage-loop rate.
Voltage sensing, the exact outer controller and its own command FIFO are then
closed around the lifted plant. All reported poles are eigenvalues of the full
corresponding state matrix, including internal states. High-order characteristic
polynomial roots are not used as the authoritative stability decision.

Frequency responses are evaluated directly from those state matrices to avoid
low-frequency cancellation of lifted polynomial coefficients. The requested
range is retained; each loop has its own grid strictly below its own Nyquist
boundary. Exports retain both grids, all gain/phase crossovers, every signed GM,
Ms/Mt, actual poles and exact coefficients. Missing crossings remain unknown.
T means L/(1+L); S means 1/(1+L); forward and sense are exported separately.

The declared operating point is the regulated sensing target. Nonunity bus
sense gain changes the physical voltage target; resistance-load power and slope
are recomputed there. Current-reference and modulation headroom are checked. Nonlinear convergence
uses that physical voltage target, and tracking errors compare the exact
reference against sampled feedback in the same calibration domain. Plots
distinguish physical dq current from sensed feedback.
Imported finite-DC-gain controllers need matching steady-state bias to realize
that point: their nonlinear settling is not inferred from local eigenvalues.

The local model requires Iq=0 and a strict interior forward-current operating
point. It assumes ideal grid feedforward and dq decoupling. It does not prove
PLL coupling, negative sequence, ABC sensor/transform coupling, clipping,
vector/slew limiting or switching dynamics. Nonzero-Iq nonlinear simulation
remains supported, but the scalar local model rejects it explicitly.

## Sensing and timing scope

Each sense path explicitly implements engineering-unit gain, an optional
matched-z discrete low-pass and integer sample latency, at its owning loop rate.
The same filter/FIFO executes in the nonlinear simulation and enters the local
state model. These are discrete post-sampling filters, not an analog anti-alias
circuit model. There is no claim to model ADC aperture, fractional conversion
latency, quantization or clipping. Current sensing is in sampled dq coordinates,
not a secretly synthesized three-channel analog circuit.

ADC volts-per-engineering-unit, bias and reference are headroom diagnostics.
The calibrated measurement gain is separately explicit; changing a headroom
field is not presented as changing the modeled calibrated filter. Unspecified
physical scaling remains UNKNOWN. Configured overrange remains OVERRANGE,
is retained verbatim on save/reopen, and prevents candidate Apply at nominal or
any retained corner. Diagnostic bounds include the sense-gain-adjusted physical
current reference limit and both precharge and regulated voltage targets. A nominal
IN_RANGE diagnostic is not transient or board acceptance.

## Actual desktop workflow

The existing `ThreePhaseDQView` entry now exposes:

1. Nonlinear dq/PLL run, cooperative Cancel and a separate local-loop Run.
2. Exact H(z) JSON import for current or voltage, and explicit restore-native PI.
3. Selectable G/C/sense/command/L/T/S curves, independent frequency grids,
   arbitrary-frequency cursor and factor-by-factor gain/phase budget.
4. A 3×3 Fc×PM candidate table using the shared bounded Tustin synthesis engine.
   Each candidate is re-evaluated with actual topology state matrices. Apply
   requires both current and outer local state models to be stable, observed
   selected-loop signed GM >= 6 dB, Ms <= 2, target
   agreement and retained-corner checks. Switching attenuation outside Nyquist
   remains UNKNOWN. This is explicitly local-model applicability, not overall
   engineering/hardware approval.
5. Retained full operating points and fixed-controller corner scans. Both exact
   controllers remain unchanged at every corner; a different corner sampling
   rate is rejected rather than silently rebased or retuned.
6. Strict complete session save/open. Source inputs, exact H(z), sensing, timing,
   retained points and design targets survive. Legacy scalar v1 parameter files
   remain readable. Unknown types/fields, nonfinite JSON, excessive sizes/orders,
   source mismatch and out-of-desktop-range values are rejected before widget
   mutation. Writes use a temporary file and atomic replacement. Open requires
   fresh analysis before enabling current exports or Apply.
7. Current-source report, per-loop CSV, standalone Python and MATLAB research
   scripts, exact session and binary64/float32 canonical C99 files. Delivery
   compiles/runs C99 and executes the actual standalone Python script first.
   Float32 coefficient rounding also receives a separate local-state reanalysis
   with response deviation and pole evidence; this is not a linear model of
   finite-precision state arithmetic. MATLAB is explicitly NOT_RUN when not
   executed. A manifest binds every file
   to the source identity and its file SHA256.

Any relevant edit, same-rate exact-controller import or new session invalidates
both loops, current exports and candidates. Worker results carry revision
ownership and cannot overwrite a changed source. Candidate generation checks
cooperative cancellation between grid points; simulation checks each sample.
The owning PFC host can use `has_active_work` (bool property) and
`cancel_background_work()` (no arguments) when closing.

At 1280×720 the dq view's minimum hint is 744×533. The control diagram is a
separate tab, and only the existing PLL comparison parameter sidebar scrolls; its plot tabs
remain outside that scroll area. This prevents its old 612-pixel form from
forcing the whole workspace above the screen height and avoids the native Qt
Matplotlib-in-scroll-area paint crash reproduced during qualification.
Linux offscreen captures were inspected; this is not native scaling acceptance.

## Focused verification

The focused modules are:

- `pfc_design/tests/test_three_phase_dq.py`
- `pfc_design/tests/test_three_phase_performance.py`
- `pfc_design/tests/test_dq_exact_workspace.py`
- `pfc_design/tests/test_dq_workspace_gui.py`

Checks cover original-runtime preservation; independent Euler recurrences and
SciPy FIR/IIR/DF2T execution; actual imported-controller nonlinear traces;
independent fast-state update versus the lifted matrix; sensor/delay ownership;
actual eigenvalues; negative margins and unknown crossings; exact Apply;
unchanged-controller corners and mismatched rates; strict/atomic sessions;
fresh-window and fresh-process actual Qt button replay; late results, edits,
cancellation and close; standalone exported Python; and compiled C99 impulse,
step, deterministic random, clipping/recovery tracking and state-freeze cases.

At the initial checkpoint, the native default nonlinear result matches all
original pre-change 7001265 signal arrays to maximum absolute difference
4.9113e-11 (the maximum is a power channel); both runs are settled. The C99 host
verification exercises 448 samples per controller for each scalar type and
matches the corresponding Python scalar recurrence exactly in the default case.
Independent H(z) tests also compare against SciPy, rather than relying solely on
two copies of the same implementation. These are focused software checks; they
are not full-repository or target-firmware acceptance.
