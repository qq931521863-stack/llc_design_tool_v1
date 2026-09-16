# TTPL Shared-ngspice Closed-Loop Verification

This document defines the first circuit-level digital closed-loop path for the single-phase Totem-Pole PFC workspace.

## Ownership

```text
PFC Control / Sensing / Bode
        ↓
PFCControlLabAnalysis
        ↓
PFCControlHandoff
        ↓
Exact current H(z) + exact voltage H(z)
        ↓
shared-ngspice TTPL closed loop
```

The controller rule is strict:

> **The shared-ngspice stage consumes the frozen exact H(z) artifact. It does not reconstruct a controller from Kp/Ti, poles/zeros or an analog prototype, and it does not perform a second S2Z conversion.**

The coefficient convention is the project canonical form:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)

y[n] = Sum(b[k] x[n-k]) - Sum(a[k] y[n-k])
```

Before a transient starts, `assert_handoff_matches_analysis()` compares the handoff coefficient arrays and sample times with the controller objects that produced the PFC Bode analysis. A mismatch fails closed.

## What ngspice owns

The V1 switching circuit is a full four-switch single-phase totem-pole network:

```text
                    BUS+
                     |
              +------+------+
              |             |
           HF high       LF positive
              |             |
AC line -- Lboost -- SW    Neutral -- AC source -- Line
              |             |
           HF low        LF negative
              |             |
              +------0------+ 
                     |
                  Cbus/load
```

The actual netlist contains:

- sinusoidal AC voltage source;
- boost inductor and explicit DCR;
- high-frequency upper/lower controlled switches;
- line-frequency positive/negative controlled switches;
- antiparallel diode commutation paths;
- DC-bus capacitor and ESR;
- resistive load derived from the selected operating point;
- small bleeders to keep the idealized switching network numerically well posed.

ngspice owns continuous state for:

- inductor current;
- DC-bus capacitor energy;
- switching-node voltage;
- AC source/network state;
- switch/diode commutation of the ideal correlation model.

## What Power Design Toolkit owns

Python owns the digital and event semantics:

- exact current-loop H(z);
- exact bus-voltage-loop H(z);
- current / AMC / voltage-loop multi-rate scheduling;
- Vrms feed-forward path and `gcmd` generation;
- current-reference generation;
- duty feed-forward and `indu_comp`;
- duty limiting / minimum effective pulse;
- PWM shadow-update timing;
- line-polarity dependent TTPL gate mapping;
- zero-cross gate inhibit;
- deterministic external gate callbacks;
- simulator/control event synchronization.

The initial voltage-loop operating point uses an explicit analytical bias. H(z) then acts on perturbation error around that bias. This avoids inventing PI internal state from Kp/Ti while preserving the exact analyzed b/a dynamics.

## Positive and negative half cycles

At positive input voltage:

- LF negative/bottom device is held on;
- HF low device is the active boost switch;
- HF high device is the synchronous switch.

At negative input voltage:

- LF positive/top device is held on;
- HF high device becomes the active boost switch;
- HF low device becomes the synchronous switch.

Around the configured zero-cross voltage threshold all commanded gates are off. Antiparallel diode paths keep the ideal circuit electrically continuous during the deadband.

## First-class GUI stage

The TTPL engineering flow now ends with:

```text
7. Exact H(z) / C99
8. Closed-Loop Verification
```

The closed-loop page supports:

- transient duration;
- AC phase at t=0;
- optional bus-reference step;
- ngspice output/max timestep override;
- wall-clock timeout;
- switching-circuit waveform display;
- final Vbus / ripple / peak IL / duty / controller-saturation summary.

A short sub-millisecond run is intended for fast switching/control correlation. A run of at least one full line cycle is required before the page reports line-cycle RMS input current, real input power and PF.

## Model boundary

This V1 path is **switching correlation**, not semiconductor or hardware sign-off.

It does not yet claim release accuracy for:

- nonlinear `Coss(V)`, `Qoss(V)` or `Eoss(V)`;
- reverse recovery / `Qrr`;
- gate-driver source/sink impedance and propagation skew;
- common-source inductance and package/interconnect parasitics;
- EMI input filter interaction;
- differential/common-mode ringing;
- current-sense amplifier saturation/noise;
- protection state machines;
- thermal feedback;
- real hardware waveforms.

The PFC small-signal workspace remains the owner of the detailed analog sensing-chain Bode model in V1. The shared-ngspice loop currently samples engineering-unit electrical quantities; moving the analog sensing network into the circuit-level simulation is a later fidelity step.

## Nonlinear controller semantics

Exact H(z) specifies the unsaturated linear transfer function. It does **not** by itself specify anti-windup or controller reset state.

The shared-ngspice V1 runtime executes the exact b/a recurrence and direct-form limiting. The handoff separately records the firmware PI/PIF/2P2Z saturation and state semantics. When the controller spends material time in saturation, the circuit-level result must therefore be interpreted as a switching/control correlation run rather than a bit-true firmware proof.

This distinction is intentional and must remain visible in result metadata.

## Validation gates

Software regression checks:

1. full TTPL circuit/netlist contains all four controlled switches and diode paths;
2. gate roles swap correctly between positive and negative AC half cycles;
3. exact handoff b/a arrays are preserved in the shared-ngspice result metadata;
4. `controller_re_discretized == False`;
5. real libngspice smoke executes finite Vbus and IL when the shared library is installed.

Hardware validation remains a separate evidence level.
