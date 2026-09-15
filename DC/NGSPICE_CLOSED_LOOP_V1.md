# ngspice Closed-Loop Verification V1

Date: 2026-09-15

## 1. Product goal

The target is **not** a standalone SPICE viewer. The feature closes the design chain already present in Power Design Toolkit:

```text
Power Design (LLC/PFC/Vienna)
        -> Circuit IR
        -> ngspice switching power stage
        -> sensing / ADC
        -> exact digital controller H(z)
        -> limiter / FM / PWM / gate schedule
        -> ngspice power stage
        -> closed-loop waveforms and metrics
```

The digital controller is reused from Control Tools / FRA Loop Designer. The user must not re-enter coefficients after design.

## 2. Reference code used

The supplied `schematic-ai` reference archive was reviewed for:

- a small circuit IR separating topology from SPICE text;
- deterministic netlist generation;
- ngspice executable discovery and batch execution;
- ASCII raw parsing and run-artifact organization.

Its image-recognition / OCR / schematic-redraw front end is deliberately outside this task. Power Design Toolkit already owns the electrical topology and design parameters, so reconstructing its own circuit from an image would reduce reliability rather than improve it.

## 3. Why two ngspice backends exist

### 3.1 Batch backend

Use for:

- generated-netlist smoke tests;
- open-loop/fixed-frequency transient correlation;
- CI tests that prove ngspice can parse and solve the generated circuit;
- future Python switched-solver vs ngspice waveform comparison.

The batch backend starts an ngspice process, forces ASCII raw output, and parses vectors into NumPy arrays.

It is **not** the production digital closed-loop architecture. Restarting a process for each controller sample would lose continuous circuit state and would be far too slow.

### 3.2 Shared-library backend

Production closed-loop uses `libngspice` / shared-ngspice. The official API provides:

- `ngSpice_Circ()` for in-memory circuit loading;
- `ngSpice_Command()` for simulator control;
- `SendData` callbacks for accepted transient points;
- `GetVSRCData` / `GetISRCData` callbacks for host-controlled external sources;
- `GetSyncData` for transient-step synchronization;
- `ngSpice_SetBkpt()` for exact time breakpoints.

This is the mechanism that allows ngspice to remain the continuous-time power-stage solver while the existing Python/C99-equivalent digital runtime executes at discrete control instants.

## 4. Existing code reused as-is

`power_sim` already contains the digital closed-loop primitives:

- `SamplerRuntime`
- ADC quantization and sample delay
- `DigitalTransferRuntime` for exact H(z)
- `ControllerLimitConfig`
- `LLCFMRuntime`
- TBPRD quantization
- control / computation / PWM scheduling
- closed-loop diagnostics

These remain the single source of truth. No second PI/2P2Z implementation is introduced in the SPICE layer.

## 5. New code structure

```text
power_sim/spice/
    ir.py               backend-neutral circuit representation + validation
    netlist.py          deterministic CircuitIR -> SPICE text
    ngspice_batch.py    batch simulator + ASCII raw parser
    shared.py           ctypes binding to sharedspice.h
    llc.py              LLCDesignSpec/TankDesign -> ideal LLC CircuitIR
    gate.py             phase-continuous variable-frequency gate generation
    sync.py             control/PWM event synchronization for ngspice timestep
```

## 6. LLC V1 electrical model

Initial model deliberately corresponds to the current ideal switching correlation layer:

```text
400-V source
   -> full bridge ideal voltage-controlled switches
   -> Lr
   -> Cr
   -> coupled-inductor transformer (Lp = Lm, Ls = Lm/n^2)
   -> diode full bridge
   -> Cout + ESR
   -> Rload
```

The model supports full-bridge primary only in V1. Half-bridge is rejected explicitly rather than approximated silently.

The full-wave diode bridge is a placeholder for the first electrical correlation step. Synchronous-rectifier timing, Qrr, third-quadrant behavior, nonlinear MOSFET Coss and vendor `.lib` models are later fidelity layers.

## 7. Gate timing

Fixed-frequency batch mode generates complementary PULSE sources with symmetric deadtime:

- positive state: AH + BL;
- negative state: AL + BH;
- all switches off during deadtime around each half-cycle transition.

Shared closed-loop mode uses `EXTERNAL` voltage sources. Gate voltage is evaluated from absolute simulation time and a **piecewise frequency/phase timeline**, not by incrementing phase inside the callback.

This is important because ngspice can retry a timestep or query a source at irregular times. Repeated evaluation at the same time must return exactly the same gate value.

When the controller changes switching frequency, the timeline preserves accumulated phase:

```text
phase_new(t_change) == phase_old(t_change)
```

so frequency control does not create a nonphysical phase jump.

## 8. Time synchronization

The shared backend must force ngspice accepted points onto:

1. ADC/control sample times;
2. gate turn-on/turn-off/deadtime edges;
3. scheduled switching-frequency update times.

`LLCCoSimulationSynchronizer` computes the next event and limits ngspice's proposed transient `delta` through `GetSyncData`.

This prevents a large solver step from jumping across a PWM edge or controller sample.

## 9. First closed-loop milestone

The first real closed-loop scenario is intentionally narrow:

```text
Vout
 -> Sampler/ADC
 -> exact designed H(z)
 -> output clamp
 -> LLC FM/TBPRD runtime
 -> phase-continuous full-bridge gates
 -> shared ngspice LLC
 -> Vout
```

Initial scenario set:

- steady regulation;
- Vref step.

After this is numerically stable and correlated, add:

- Vin step (external bus source);
- load step;
- soft start;
- protection and nonlinear state machine behavior;
- SR gate algorithm;
- device nonlinear models.

## 10. Validation order

The engineering validation ladder is:

```text
FHA / small signal
   -> harmonic balance
   -> internal Python switched solver
   -> ideal ngspice switching model
   -> detailed/vendor ngspice model
   -> hardware
```

Each higher layer should be compared against the previous layer before adding another nonideality.

## 11. V1 acceptance criteria

Foundation is accepted only when:

- Circuit IR/netlist unit tests pass;
- ASCII raw parser regression passes;
- GitHub CI installs real ngspice and solves an RC transient;
- shared `libngspice` loads and solves a simple in-memory DC circuit;
- default full-bridge LLC netlist is generated with correct Lr/Cr/Lm/n/Cout/load values;
- gate schedule is phase-continuous under frequency changes;
- timestep synchronizer hits control and PWM events;
- no existing `power_sim` digital-control tests regress.

The shared closed-loop LLC run is the next commit after this foundation is green; it will not be declared complete merely because the ctypes API loads.

## 12. Model boundary

This first implementation is an **ideal switching circuit correlation model**. It is not yet a vendor-device SPICE model and must not be presented as predicting ringing, switching loss, Coss commutation or Qrr with release accuracy.
