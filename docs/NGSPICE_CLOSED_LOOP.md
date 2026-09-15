# ngspice Closed-Loop Architecture

## 1. Purpose

The ngspice integration is not a standalone schematic viewer. It is the circuit-level verification layer for the engineering/control chain already present in Power Design Toolkit:

```text
LLC design objects
 -> Circuit IR
 -> ngspice switching circuit
 -> sampled Vout
 -> exact discrete H(z)
 -> limiter / FM / PWM / gate schedule
 -> ngspice circuit state
 -> power + control waveforms
```

The SPICE layer does not contain a second PI/2P2Z implementation. The existing `power_sim` digital runtime remains the control authority.

## 2. Two simulator backends

### Batch ngspice

`power_sim/spice/ngspice_batch.py` starts a normal ngspice process, forces ASCII RAW output and parses vectors back into NumPy arrays.

Use it for:

- generated-netlist smoke tests;
- fixed-frequency transient correlation;
- CI proof that ngspice can parse and solve the emitted circuit.

It is not suitable for stepping a digital controller sample-by-sample because restarting a process would destroy continuous circuit state.

### shared libngspice

`power_sim/spice/shared.py` binds the shared library with `ctypes`. The closed-loop path uses in-memory circuit loading, transient callbacks, external sources and synchronization callbacks while ngspice retains the continuous inductor/capacitor state.

The binding uses, among others:

- `ngSpice_Circ()`;
- `ngSpice_Command()`;
- `SendData`;
- `GetVSRCData`;
- `GetSyncData`;
- `ngGet_Vec_Info()`.

A real CI failure demonstrated that shared-ngspice behaves as process-global state. The wrapper therefore uses one native initialized session and retargets Python-side handlers between runs instead of repeatedly calling native initialization from independent objects.

## 3. Circuit IR

The simulator-specific text is separated from electrical intent:

```text
power_sim/spice/
    ir.py
    netlist.py
    ngspice_batch.py
    shared.py
    llc.py
    gate.py
    sync.py
    closed_loop.py
    waveforms.py
```

`CircuitIR` represents connectivity/models/directives. `netlist.py` renders deterministic SPICE text. This keeps topology generation testable without tying the engineering model to raw string concatenation throughout the codebase.

## 4. Current LLC circuit model

The current V1 circuit is a **full-bridge switching-correlation model**:

```text
DC bus
 -> full bridge controlled switches
 -> Lr + small DCR
 -> Cr + ESR
 -> coupled transformer + winding damping, K < 1
 -> diode full bridge
 -> Cout + ESR
 -> Rload
```

FULL_BRIDGE is supported in this first correlation layer. Unsupported primary topology is rejected explicitly instead of silently changing the source scaling.

Small physical damping is intentional. A nearly lossless first prototype produced real `timestep too small` failures at secondary commutation. The present resistance/coupling terms regularize the switching network without pretending to represent vendor loss accurately.

## 5. Gate scheduling

Fixed-frequency batch mode uses complementary PULSE sources with deadtime.

Shared closed-loop mode drives `EXTERNAL` sources. Gate state is calculated from absolute simulation time and a piecewise frequency/phase timeline. The callback is therefore deterministic even when ngspice retries a timestep.

When switching frequency changes, accumulated phase is continuous:

```text
phase_new(t_change) == phase_old(t_change)
```

so the controller does not create an artificial bridge phase jump.

## 6. Event synchronization

`LLCCoSimulationSynchronizer` constrains the transient step so the solver does not jump over:

1. digital-control sample instants;
2. switching/deadtime edges;
3. scheduled frequency-application events.

The controller executes only on its discrete clock. ngspice remains continuous/adaptive between those events.

## 7. Exact digital controller

The closed-loop runner consumes the project `DigitalTransferFunction` directly:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)
```

No S-to-Z conversion is performed inside the co-simulation path.

The runtime chain reuses:

- `SamplerRuntime`;
- `DigitalTransferRuntime`;
- `ControllerLimitConfig`;
- LLC frequency-modulation runtime;
- control/computation/PWM timing;
- closed-loop diagnostics.

The linear design model naturally uses the local FM slope at the operating point. The nonlinear runtime also contains the firmware-style PCMD/LUT/TBPRD execution primitives; exact end-to-end LUT use should be stated according to the current GUI/runtime revision rather than inferred from the small-signal model.

## 8. Vector naming boundary

Real `libngspice` exposed a naming difference between direct vector queries and `SendData` callback names, for example:

```text
expression query     callback vector
v(out)               out
i(lr)                lr#branch
```

The shared adapter normalizes these aliases at one boundary so backend-specific vector names do not leak into the controller or waveform contracts.

## 9. Waveform contract

ngspice accepted transient points are adaptive/non-uniform. Existing GUI statistics expect synchronized display data. The adapter therefore:

- retains raw simulator vectors in the SPICE result;
- removes duplicate/non-increasing display timestamps;
- resamples analog circuit vectors onto a uniform display grid;
- zero-order-holds discrete controller signals between ISR ticks;
- returns the normal LLC `WaveformBundle`.

This allows existing cursor/statistics/plotting infrastructure to display both power-stage and control traces without a second GUI stack.

## 10. Real CI validation

The dedicated `ngspice-smoke` GitHub Actions workflow installs real Ubuntu `ngspice` and `libngspice` packages and exercises the actual executable/shared library.

The validation ladder includes:

- Circuit IR -> real batch RC transient;
- generated full-bridge LLC -> real switching transient;
- shared-library in-memory solve;
- background transient with live callbacks;
- exact sampled H(z) clock around the shared LLC circuit;
- nonzero-controller reference-step causal checks;
- conversion to the standard `WaveformBundle`.

These tests validate architecture and numerical plumbing. They do not certify hardware accuracy.

## 11. Model boundary

Current shared-ngspice results must **not** be presented as release-accurate predictions of:

- MOSFET nonlinear Coss commutation;
- package/layout ringing;
- body-diode or SR Qrr;
- switching loss;
- detailed deadtime device stress;
- thermal behavior.

Those require vendor/device models, parasitics, model correlation and hardware measurement.
