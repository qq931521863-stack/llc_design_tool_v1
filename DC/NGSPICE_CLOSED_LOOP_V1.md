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

The exact discrete controller is reused from the existing LLC Digital Control / Control Tools path. Coefficients are not re-entered and are not re-discretized in the SPICE layer.

## 2. Reference code used

The supplied `schematic-ai` reference archive was reviewed for:

- a small circuit IR separating topology from SPICE text;
- deterministic netlist generation;
- ngspice executable discovery and batch execution;
- ASCII raw parsing and run-artifact organization.

Its image-recognition / OCR / schematic-redraw front end is deliberately outside this task. Power Design Toolkit already owns the electrical topology and design parameters, so reconstructing its own circuit from an image would reduce reliability rather than improve it.

## 3. Why two ngspice backends exist

### 3.1 Batch backend

Use for generated-netlist smoke tests, fixed-frequency transient correlation and CI. The batch backend starts an ngspice process, forces ASCII RAW output and parses vectors into NumPy arrays.

It is **not** the digital closed-loop architecture. Restarting ngspice for every controller sample would destroy continuous L/C state and be far too slow.

### 3.2 Shared-library backend

Closed loop uses `libngspice` / shared-ngspice. The implemented binding uses:

- `ngSpice_Circ()` for in-memory circuit loading;
- `ngSpice_Command()` / `bg_run` for simulator control;
- `SendData` for accepted transient points;
- `GetVSRCData` for host-controlled external gate sources;
- `GetSyncData` to force solver steps onto PWM/control events;
- `ngGet_Vec_Info()` for direct vector access and diagnostics.

`ngSpice_SetBkpt()` is a solver timestep breakpoint API, **not a pause/resume primitive**, so V1 does not use it as the digital control clock.

One important implementation finding is that shared ngspice is effectively process-global. Repeated native `ngSpice_Init()` calls from independent wrapper objects caused a real native crash in CI. `NgSpiceSharedLibrary` is therefore a process singleton: native callbacks are installed once and Python-side handlers are retargeted between runs.

## 4. Existing digital code reused as authority

`power_sim` remains the single digital execution implementation:

- `SamplerRuntime`
- ADC quantization / sample delay
- `DigitalTransferRuntime` for exact H(z)
- `ControllerLimitConfig`
- `LLCFMRuntime`
- `LLCFMLUTRuntime` for firmware-style PCMD tables
- TBPRD quantization
- computation / PWM timing
- closed-loop diagnostics

No second PI/2P2Z implementation is introduced inside the SPICE backend.

## 5. Code structure

```text
power_sim/spice/
    ir.py               backend-neutral circuit representation + validation
    netlist.py          deterministic CircuitIR -> SPICE text
    ngspice_batch.py    batch simulator + ASCII RAW parser
    shared.py           ctypes binding + process-singleton lifetime
    llc.py              LLCDesignSpec/TankDesign -> LLC CircuitIR
    gate.py             phase-continuous variable-frequency full-bridge gates
    sync.py             control/PWM event synchronization
    closed_loop.py      exact H(z) <-> shared-ngspice closed-loop runner
    waveforms.py        adaptive SPICE vectors -> existing WaveformBundle
```

GUI integration:

```text
LLC workspace
    Digital Control / Control Tools exact H(z)
        -> Closed-Loop Verification tab
        -> worker-thread shared-ngspice run
        -> WaveformBundle
        -> existing synchronized waveform/cursor/statistics GUI
```

## 6. LLC V1 electrical model

V1 is a **switching correlation model with explicit small physical damping**, not a vendor semiconductor model:

```text
DC bus
 -> full bridge voltage-controlled switches
 -> Lr + DCR
 -> Cr + ESR
 -> coupled-inductor transformer + winding R, K<1
 -> diode full bridge
 -> Cout + ESR
 -> Rload
```

FULL_BRIDGE primary only. Half bridge is rejected explicitly rather than silently approximated.

The explicit DCR/ESR/winding resistance and finite transformer coupling are important. A first near-lossless prototype produced real ngspice `timestep too small` failures at the first secondary commutation. The current small damping terms regularize the physical switching network without pretending to provide vendor-device loss accuracy.

Later fidelity layers may add nonlinear MOSFET Coss, body diode/Qrr, SR timing and vendor `.lib` models.

## 7. Gate timing

Fixed-frequency batch mode uses complementary PULSE sources with symmetric deadtime:

- positive bridge state: AH + BL;
- negative bridge state: AL + BH;
- all four switches off around each half-cycle transition.

Shared closed-loop mode uses `EXTERNAL` voltage sources. Gate state is a deterministic function of **absolute simulation time** and a piecewise frequency/phase timeline. If ngspice retries a timestep, repeated source queries at the same time return the same value.

When switching frequency changes, accumulated phase is continuous:

```text
phase_new(t_change) == phase_old(t_change)
```

so control updates do not create nonphysical bridge phase jumps.

## 8. Time synchronization

`LLCCoSimulationSynchronizer` constrains ngspice's proposed transient delta through `GetSyncData` so accepted solver points do not jump over:

1. digital control sample times;
2. full-bridge switching edges/deadtime boundaries;
3. scheduled switching-frequency application times.

The controller itself runs only at its discrete sample clock. Circuit integration remains continuous/adaptive between those events.

## 9. Shared callback name contract

A real Ubuntu `libngspice.so.0` CI run exposed an important naming difference:

```text
ngGet_Vec_Info expression      SendData raw vector
v(out)                         out
i(lr)                          lr#branch   (implementation dependent alias)
```

The bridge now normalizes expression-style names to the raw callback names at one boundary. This fixed the initial false diagnosis that `SendData` was not streaming: the callback was actually producing hundreds of accepted points, but the first parser looked only for `v(out)` instead of `out`.

## 10. Real closed-loop milestones already proven

The dedicated GitHub workflow installs **real `ngspice` and `libngspice0`** and runs the SPICE tests instead of mocking the engine.

Validated sequence:

1. Circuit IR -> deterministic netlist -> real batch ngspice RC transient.
2. Generated FULL_BRIDGE LLC -> real batch transient with bridge switching and finite resonant current.
3. shared-ngspice in-memory DC operating point.
4. shared-ngspice background transient with live `SendData` callback.
5. shared LLC with a zero exact-H(z) controller at 40 kHz, proving the digital clock/gates/vector path.
6. shared LLC with a **nonzero exact PI H(z)** and a +1 V Vref step:
   - sampled error changes at the discrete controller tick;
   - exact controller output changes;
   - FM changes switching frequency in the correct LLC direction;
   - SPICE continues with finite switching waveforms to the end of the run.
7. the same shared result is converted into the existing `WaveformBundle` contract.

The nonzero PI test is a causal/plumbing smoke test, not a claim that those example PI values are an optimized production loop.

## 11. WaveformBundle / GUI contract

ngspice accepted points are adaptive and non-uniform. Existing waveform statistics assume uniform sampling. Therefore the adapter:

- preserves raw adaptive vectors in `NgSpiceClosedLoopResult`;
- removes duplicate/non-increasing display points;
- resamples analog SPICE vectors onto a uniform display grid;
- zero-order-holds controller signals between exact ISR ticks;
- returns the existing `WaveformBundle` used by the LLC GUI.

Initial displayed signals include:

- bridge leg A/B and differential bridge voltage;
- resonant current;
- Vout and Vout ripple;
- four gate voltages;
- ideal-switch reconstructed VDS;
- Vref, sampled feedback, error, controller output and switching frequency.

This avoids building a second plotting/cursor/statistics stack for SPICE.

## 12. Controller and FM interpretation

The exact controller coefficients are consumed directly:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)
```

No S->Z conversion occurs in the co-simulation runner.

For a small-signal controller designed around an LLC operating point, controller output is interpreted as a PCMD perturbation about the operating command. The code now also contains a firmware-style `LLCFMLUTRuntime` that supports:

- PCMD -> TBPRD LUT;
- PCMD -> frequency LUT;
- operating-command bias;
- interpolation;
- PCMD endpoint clamp;
- timer/TBPRD quantization.

The first GUI bridge still uses the locally linearized FM slope while the exact LUT runtime is being connected end-to-end. The GUI/report must state this boundary until that last link is complete.

## 13. GUI V1

The LLC workspace receives a `Closed-Loop Verification` tab rather than another top-level application.

Current intended user flow:

```text
LLC power design
 -> LLC Digital Control or Control Tools exact H(z)
 -> Closed-Loop Verification
 -> choose Vref step / duration / Vbus / load
 -> RUN CLOSED LOOP
 -> power + control waveforms and transient metrics
```

The SPICE solve runs in the existing Qt worker thread path; it must never block the UI thread.

## 14. Validation ladder

```text
FHA / small signal
   -> harmonic balance
   -> internal Python switched solver
   -> ideal shared-ngspice switching model
   -> detailed/vendor ngspice model
   -> hardware
```

Each higher layer is compared with the previous one before adding another nonideality.

## 15. Remaining V1 work before merge

- connect the new exact FM LUT runtime into the shared LLC runner/GUI end-to-end;
- finish GUI smoke and user-flow validation with the exact controller source bridge;
- add a controlled comparison against the existing Python switched solver at the same operating point;
- make ngspice installation/runtime discovery clear on Windows and macOS packages;
- update localized help strings after the engineering workflow is frozen;
- final user GUI review before merging to `main`.

## 16. Model boundary

This implementation is currently a **design/correlation switching model**. It must not be presented as release-accurate prediction of ringing, switching loss, nonlinear Coss commutation, Qrr or synchronous-rectifier device stress. Those require the later detailed-device fidelity layer and hardware correlation.
