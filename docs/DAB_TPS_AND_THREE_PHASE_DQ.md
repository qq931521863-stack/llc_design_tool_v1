# DAB TPS and three-phase two-level PFC

## Scope and evidence

These are Python engineering/simulation features, available from the desktop
launcher and Python API. They do not generate device firmware, claim hardware
validation, or extend the Web/MCP routes. Existing LLC/TTPL/Vienna engines retain
their topology-specific behavior. No proprietary multimode PFC controller is
used as an algorithm or requirements baseline.

## DAB TPS

Enter **DAB TPS** from the launcher or LLC toolbar. The layout follows the LLC
parameter-dock / analysis-tab pattern. Set the sweep, run, then select an
operating point from the table or selector. The plots cover phase/power,
electrical waveforms, current/backflow, capacitor stress and commutation/PWM.

The supplied source is preserved at
[`reference/DAB_TPS_LOAD_POINTS_2018_V2.m`](reference/DAB_TPS_LOAD_POINTS_2018_V2.m).
Its four TPS/MCSO branches, default load derating and 25 cases are ported to
`dab_design/core.py`. This attribution describes the supplied file, not an
independent verification of the provenance or optimality of its equations.

Conventions:

- `n = Ns/Np`, referred secondary voltage `VB/n`, `m=n*VA/VB`.
- `Pbase = VA*VB/(8*n*fs*L)` and `p_raw = Pref/Pbase`.
- `D1`, `D2`, `D3` are absolute leg shifts normalized to a half period;
  angles are `180*D`. Secondary inner shift is `D3-D2`.
- Ideal bridge functions are `(qA1+qA2)/2` and `(qB1+qB2)/2`, with the same
  polarity and shift convention as the reference. They are not gate commands.
- Only forward power is supported. Negative power is rejected, not silently
  clamped and presented as an implemented reverse controller.
- `feasible` means `p_raw<=1` under the **ideal** power ceiling. `limited`
  independently reports `p_raw>p_limit`, including feasible-but-limited cases.
- L-only uses the reference's sampled rectangular integration and zero-mean
  current. LC uses FFT impedance `R+jwL+1/(jwCp)`. Cp does not modify the TPS law.
  The L-only inductor-voltage diagnostic consistently omits R (the reference
  subtracted R even from its ideal L-only diagnostic).
- PWM counts retain MATLAB's nonnegative half-away rounding and its full-period
  tick convention. They are not a C2000 TBPRD/phase-register configuration.

Limits: steady-state ideal stiff ports, zero magnetizing current, no nonlinear
Coss, deadtime, diode recovery, core/device switching losses or voltage loop.
Commutation currents are signed, sampled primary-referred inductor currents;
minimum absolute current or `0.5*L*I²` cannot establish ZVS. Every point reports
`zvs_status=UNKNOWN`. Output ripple is switching-period ripple only. The
circulation metric is a proxy, not IEEE reactive power. Zero commanded power
has undefined relative tracking error, exported as empty/None rather than a
fabricated percentage. An exactly undamped harmonic resonance is rejected.

Project JSON saves current inputs. Result CSV and its `.config.json` companion
save the completed run snapshot, even when input widgets have subsequently
changed. Plots can be saved through their Matplotlib toolbar.

```python
from dab_design import DABConfig, analyze_dab
from dab_design.core import export_csv
result = analyze_dab(DABConfig())
export_csv(result, "dab-results.csv")
```

Validation includes all default cases, equal-voltage SPS analytic power/current,
nonunity turns ratios, continuity at all mode boundaries, saturation semantics,
LC power conservation and GUI/export integration. MATLAB/Octave was not available
in this environment; no claim of independently executing the `.m` file is made.

## Three-phase two-level dq / PLL

Open PFC → **Three-Phase Two-Level dq / PLL**. This is a six-switch two-level
converter with one DC-link capacitor and a floating three-wire neutral. It has
no Vienna zero-state duty mapping or split-capacitor midpoint loop.

Amplitude-invariant, cosine-aligned Park convention:

```
alpha = (2*a-b-c)/3       beta = (b-c)/sqrt(3)
d = alpha*cos(theta)+beta*sin(theta)
q = -alpha*sin(theta)+beta*cos(theta)
P = 1.5*(vd*id+vq*iq)
```

Current is positive from grid to converter. The plant is integrated in abc;
the controller uses only sampled grid voltages/currents and PLL state:

```
Vbus reference - Vbus -> voltage PI -> nonnegative Id target -> slew limiter
Iq target = configured value (default 0)
Id/Iq errors -> current PIs (output unit V)
vcd* = vd + omega*L*iq - PI_d
vcq* = vq - omega*L*id - PI_q
inverse Park -> circular linear-region limit -> centered SVPWM -> held duties
```

The inner PI includes saturation back-calculation. The outer PI includes
current-vector limiting and anti-windup. Current, PLL and PWM run at the same
configured rate; the voltage rate must divide it. Configurable integer PWM
update delay is applied to the generated duties. The nonlinear plant consumes
the actual delayed duty and current DC voltage. Integration substeps are
independent of control updates.

The SRF-PLL uses normalized `Vq/|V|`, a PI frequency correction and a phase
accumulator, with frequency bounds and continuous lock qualification. Positive
Vd is required to avoid declaring antiphase alignment locked; low voltage
removes lock. `pll_bandwidth_hz` is the PI design natural frequency `wn/(2*pi)`,
not the exact closed-loop -3 dB bandwidth. The user interface labels it as such.
The phase detector/PI/VCO structure was cross-checked against TI's
[SPLL_3PH_SRF module documentation](https://software-dl.ti.com/C2000/c2000_apps_public_sw/c2000ware_sdk/digitalpowersdk/5_03_00_00/html_guide/group__SPLL__3PH__SRF.html).
This is an independent Python implementation, not a copied TI firmware library.

When lock is lost, the Id target becomes zero and slews down; Iq reference
becomes zero. Converter voltage control continues. This is a simulation control
policy, **not a hardware safe-off path**. The model starts with a precharged bus
and a configured load ramp; it does not implement diode rectification,
precharge, startup protection or switching-device commutation physics.

Bus energy uses converter-side power:

```
L*di_abc/dt = vgrid_abc - vconverter_abc - R*i_abc
d(0.5*C*Vbus^2)/dt = sum(vconverter_abc*i_abc) - Pload
total E = 0.5*C*Vbus^2 + sum(0.5*L*i_abc^2)
dE/dt = Pgrid - sum(R*i_abc^2) - Pload
```

No Vbus clamping is used to hide an energy collapse: an unbounded/collapsed
simulation is withheld with an error. The mean-power drift, mean-Vbus error
and qualified PLL lock checks govern the final `settled` diagnostic; they are
not a proof of small-signal or nonlinear stability. PF is from averaged
waveforms and excludes switching ripple. Balanced positive-sequence grids are
supported. Negative-sequence rejection, unbalance/harmonic grid injection,
ADC/front-end errors and switching losses remain outside this version.

The SVPWM view reconstructs six complementary ideal gates at the final frozen
workpoint, with two pole levels. It does not integrate a second switching
plant or claim deadtime-correct gate outputs. CSV/JSON exports preserve the
run configuration, waveform signals, diagnostics and model limits.

```python
from dataclasses import replace
from pfc_design.three_phase import ThreePhaseConfig, simulate_three_phase
config = replace(ThreePhaseConfig(), grid_after_hz=60, phase_step_deg=20,
                 load_step_ratio=0.7)
result = simulate_three_phase(config)
```

Tests include transform/power invariance; PLL acquisition, voltage-loss unlock
and antiphase qualification; duty limits and two-level gate reconstruction;
default control; 50→60 Hz + 20° phase + 30% load-drop recovery; nonzero Iq;
disabled-controller falsification; energy conservation and integration refinement;
invalid settings; and real asynchronous GUI execution and exports.
