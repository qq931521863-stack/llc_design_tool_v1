# ngspice Closed-Loop Architecture

## 1. Purpose

The ngspice integration is not a standalone schematic viewer. It is the circuit-level verification layer for the engineering/control chains already present in Power Design Toolkit.

Two closed-loop topology paths are currently maintained:

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

and:

```text
TTPL engineering/control analysis
 -> PFCControlHandoff
 -> exact current + voltage H(z)
 -> sensing / ADC / firmware-correlated state runtime
 -> duty / PWM / zero-cross gate state
 -> shared-ngspice four-switch TTPL circuit
 -> IL / Vbus / switch-node / gate waveforms
```

The SPICE layer does not create an independent controller design path. Exact discrete transfer functions produced upstream remain the linear-controller authority.

## 2. Two simulator backends

### Batch ngspice

`power_sim/spice/ngspice_batch.py` starts a normal ngspice process, forces ASCII RAW output and parses vectors back into NumPy arrays.

Use it for:

- generated-netlist smoke tests;
- fixed-frequency transient correlation;
- CI proof that ngspice can parse and solve the emitted circuit.

It is not suitable for stepping a digital controller sample-by-sample because restarting a process would destroy continuous circuit state.

### shared libngspice

`power_sim/spice/shared.py` binds the shared library with `ctypes`. The closed-loop paths use in-memory circuit loading, transient callbacks, external sources and synchronization callbacks while ngspice retains continuous inductor/capacitor state.

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

    pfc.py
    pfc_gate_runtime.py
    pfc_closed_loop.py

    waveforms.py
```

`CircuitIR` represents connectivity/models/directives. `netlist.py` renders deterministic SPICE text. This keeps topology generation testable without tying the engineering model to raw string concatenation throughout the codebase.

## 4. LLC switching circuit

The current LLC circuit is a **full-bridge switching-correlation model**:

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

## 5. TTPL switching circuit

`power_sim/spice/pfc.py` builds the single-phase four-switch totem-pole PFC correlation circuit:

```text
AC source
 -> Lboost + DCR
 -> HF high / HF low leg
 -> DC bus

neutral
 -> LF positive / LF negative leg
 -> DC bus / return

DC bus
 -> Cbus + ESR
 -> load
```

The model includes antiparallel diode paths so deadtime and zero-cross intervals retain a physical commutation route. Small damping/bleeder elements are used for numerical regularization.

The current TTPL circuit is intentionally an **ideal-switching engineering model**. It does not claim vendor-accurate nonlinear Coss/Qrr, layout ringing, gate-driver propagation, switching energy or thermal behavior.

## 6. Gate scheduling

### LLC

Fixed-frequency batch mode uses complementary PULSE sources with deadtime.

Shared closed-loop mode drives `EXTERNAL` sources. Gate state is calculated from absolute simulation time and a piecewise frequency/phase timeline. The callback is deterministic even when ngspice retries a timestep.

When switching frequency changes, accumulated phase is continuous:

```text
phase_new(t_change) == phase_old(t_change)
```

so the controller does not create an artificial bridge phase jump.

### TTPL

The TTPL path uses duty/PWM timelines and line-polarity-aware HF/LF gate scheduling. The newer firmware-correlated path also consumes the eight-state zero-cross runtime instead of deciding commutation from instantaneous Vac sign alone.

The state chain is:

```text
positiveHalf
 -> negativeZeroCrossing1
 -> negativeZeroCrossing2
 -> negativeZeroCrossing3
 -> negativeHalf
 -> positiveZeroCrossing1
 -> positiveZeroCrossing2
 -> positiveZeroCrossing3
 -> positiveHalf
```

Zero-cross states determine LF-leg state, HF inhibit/soft-start behavior, target polarity and current-controller reset requests.

## 7. Event synchronization

Shared-ngspice remains continuous/adaptive between explicitly important digital/switching events.

The LLC synchronizer constrains the solver around:

1. digital-control sample instants;
2. switching/deadtime edges;
3. scheduled frequency-application events.

The TTPL path additionally aligns:

1. current-loop ticks;
2. AMC ticks;
3. voltage-loop ticks;
4. sensing/ADC sample events;
5. PWM/deadtime edges;
6. duty-application / shadow-update events;
7. zero-cross state changes as they become observable on control ticks.

Coincident multi-rate controller events use deterministic ordering rather than depending on adaptive solver step placement.

## 8. Exact digital-controller contract

The project canonical convention is:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)

y[n] = sum(b[k] x[n-k]) - sum(a[k] y[n-k])
```

No S-to-Z conversion is performed inside the LLC or TTPL shared-ngspice closed-loop path.

For TTPL, `PFCControlHandoff` freezes the analyzed current/voltage `b[]`, `a[]`, sample rates, output limits, provenance and nonlinear implementation semantics. The runner validates identity against `PFCControlLabAnalysis` before transient execution and records `controller_re_discretized=False` in result metadata.

Exact H(z) owns the linear controller. Saturation, anti-windup, reset ordering and protection-state behavior are separate implementation semantics and must be modeled explicitly.

## 9. Firmware-correlated TTPL runtime

`pfc_design/control/firmware_runtime.py` moves the TTPL closed-loop path closer to real embedded execution without redefining the exact H(z).

### Controller arithmetic/state

The runtime includes:

- float32 state arithmetic;
- PI/PIF state decomposition derived from frozen H(z) coefficients;
- identity checks that the decomposition reproduces the exact H(z);
- conditional-integrator anti-windup when saturation and error push outward;
- PIF output filtering after PI saturation;
- 2P2Z with clamped output stored in denominator history;
- explicit controller reset support during zero-cross transitions.

This is a software execution contract, not a claim that Python instructions are identical to C28x assembly instruction-by-instruction.

### Sensing / ADC

`PFCSampledSenseRuntime` executes the configured measurement chain in time domain:

```text
physical signal
 -> analog poles
 -> ADC sample event
 -> ADC-resolution quantization
 -> recursive/multi-SOC stage
 -> digital filter
 -> sampled calibrated engineering-unit value
```

The quantization LSB comes from configured ADC Vref, bit width and front-end gain. Filter/state arithmetic is float32.

The current schema does not yet carry every board-specific ADC common-mode offset, rail clamp, SOC register sequence and calibration constant. The present implementation therefore remains **firmware-correlated**, not absolute MCU bit identity.

### Timing / PWM

TTPL control results can be applied at scheduled PWM boundaries instead of immediately at an arbitrary simulator integration point. This is the basis for progressively adding C2000 TBPRD/CMP/deadband/peripheral semantics where explicit project evidence is available.

## 10. Vector naming boundary

Real `libngspice` exposed a naming difference between direct vector queries and `SendData` callback names, for example:

```text
expression query     callback vector
v(out)               out
i(lr)                lr#branch
```

The shared adapter normalizes these aliases at one boundary so backend-specific vector names do not leak into controller or waveform contracts.

## 11. Waveform contracts

ngspice accepted transient points are adaptive/non-uniform.

For LLC, the adapter:

- retains raw simulator vectors in the SPICE result;
- removes duplicate/non-increasing display timestamps;
- resamples analog circuit vectors onto a uniform display grid;
- zero-order-holds discrete controller signals between ISR ticks;
- returns the standard LLC `WaveformBundle`.

For TTPL, the result retains raw switching vectors plus control samples/metrics such as Vbus, IL, duty range, controller saturation fraction and, when a sufficient line-cycle interval is simulated, input RMS current / real power / PF.

## 12. Real CI validation

The dedicated `ngspice-smoke` GitHub Actions workflow installs real Ubuntu `ngspice` and `libngspice` packages and exercises the actual executable/shared library.

The validation ladder includes:

- Circuit IR -> real batch transient;
- generated full-bridge LLC -> real switching transient;
- shared-library in-memory solve;
- background transient with live callbacks;
- exact sampled H(z) clock around the shared LLC circuit;
- nonzero-controller LLC reference-step causal checks;
- LLC conversion to the standard `WaveformBundle`;
- TTPL four-switch circuit execution;
- TTPL exact-H(z) coefficient identity in shared-ngspice;
- TTPL firmware-runtime regression for float32 controller state, ADC/sensing quantization/filtering and zero-cross sequencing.

These tests validate architecture, execution semantics and numerical plumbing. They do not certify hardware accuracy.

## 13. Model boundary

Current shared-ngspice results must **not** be presented as release-accurate predictions of:

- MOSFET nonlinear Coss/Qoss/Eoss commutation;
- package/layout ringing;
- body-diode or SR Qrr;
- switching loss versus current/voltage/temperature/gate resistance;
- detailed gate-driver propagation/skew;
- exact C2000 ADC/ePWM register behavior unless explicitly modeled from project evidence;
- complete startup/protection/recovery state machines;
- detailed deadtime device stress;
- EMI-filter interaction;
- thermal behavior.

The intended validation ladder remains:

```text
analytical / averaged model
 -> topology time-domain model
 -> firmware-correlated runtime
 -> circuit-level shared-ngspice
 -> bench measurement
 -> reviewed release evidence
```

Each stage adds evidence; none automatically proves the stages below it.
