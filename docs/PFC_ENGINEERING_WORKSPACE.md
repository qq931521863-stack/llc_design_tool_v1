# PFC Engineering Workspace V2

The PFC workspace is being upgraded from a control-lab-first page into an engineering workflow comparable to the LLC workspace. The first implementation target is **single-phase totem-pole PFC (TTPL)**; Vienna reuses the same architecture after the TTPL chain is proven.

## Current TTPL workflow

```text
1. Power Stage / Sizing
        ↓
2. Devices / Loss
        ↓
3. Capacitor / Thermal
        ↓
4. AC Line / PF / THD
        ↓
5. Switching / Zero Crossing
        ↓
6. Control / Sensing / Bode
        ↓
7. Exact H(z) / C99
        ↓
8. Closed-Loop Verification
```

The design rule is that each stage owns one engineering question. Later stages consume earlier artifacts instead of rebuilding an alternate model silently.

## 1. Power Stage / Sizing

The deterministic `pfc_design.engineering` kernel owns specification-to-hardware sizing. Inputs include minimum/nominal/maximum AC RMS voltage, line frequency, DC-bus voltage, output power and efficiency estimate, switching frequency, boost-inductor ripple target, twice-line bus-ripple target, hold-up time/end voltage and duty/minimum-pulse limits.

For the first-order unity-PF design model:

```text
Pin = Pout / eta
Iin,rms = Pin / Vin,rms
Iin,pk = sqrt(2) * Iin,rms
```

Boost ripple is scanned over the low-line half cycle:

```text
D = 1 - |Vin| / Vbus
DeltaIL_pp = |Vin| * D / (L * fsw)
```

The required `Lboost` is selected so the worst low-line ripple does not exceed the user target.

DC-bus capacitance is sized from both twice-line energy ripple and hold-up:

```text
Cbus,ripple >= Pout / (omega_line * Vbus * DeltaVpp_allowed)
Cbus,hold   >= 2 * Pout * thold / (Vbus^2 - Vend^2)
Cbus,recommended = max(Cbus,ripple, Cbus,hold)
```

`Apply` transfers the electrical specification, calculated `Lboost`, recommended `Cbus`, efficiency and duty/minimum-pulse settings into the downstream control/time-domain model.

## 2. Devices / Loss

`PFCDeviceDatabase` merges packaged MOSFET records with a persistent user library. User records can be created, edited, cloned, deleted, imported and exported as JSON. The same database is intended for TTPL and Vienna.

Built-in PFC MOSFET records are shown as **Built-in / unverified** because the engineering-data catalogue does not yet contain normalized datasheet revision/extraction provenance for those named records.

The TTPL screening page evaluates:

- HF active switch conduction/switching loss;
- HF synchronous device conduction/turn-off loss;
- deadtime reverse-conduction loss;
- Coss and gate-drive loss;
- complete line-frequency-leg conduction/gate loss;
- VDS derating and current-rating checks.

Line-cycle conduction uses:

```text
I_local,rms^2(theta) = Iavg(theta)^2 + DeltaIL(theta)^2 / 12
Pactive,cond = RDS(T) * mean[D * I_local,rms^2]
PSR,cond     = RDS(T) * mean[(1-D) * I_local,rms^2]
```

When datasheet switching-energy reference points exist:

```text
E(V,I) = Eref * (V/Vref) * (I/Iref)
```

Otherwise the model falls back to a linear `tr/tf` estimate. Eoff/Coss overlap remains an explicit modelling risk.

## 3. Capacitor / Thermal

For an identical capacitor bank with `Ns` devices in series and `Np` strings in parallel:

```text
Ns = ceil(Vbus / (Vrated * voltage_derating))
Np,C = ceil(Crequired * Ns / Cunit)
Np,I = ceil(Icap,rms / (Irated * ripple_derating))
Np = max(Np,C, Np,I)

Cbank   = Cunit * Np / Ns
ESRbank = ESRunit * Ns / Np
Pesr    = Icap,rms^2 * ESRbank
```

The selected physical bank can be explicitly applied to the downstream plant, transferring **actual Cbank and ESR** instead of leaving the Phase-1 minimum capacitance in the control model.

Capacitor hot-spot/lifetime screening uses:

```text
Tcap = Tamb + Pcap,ESR * Rtheta,cap
Life = Life_rated * 2^((T_rated - Tcap) / 10)
```

This is an empirical screening rule, not a vendor lifetime model. Series stacks also require a real voltage-sharing/balancing design.

Semiconductor thermal screening iterates loss and junction temperature:

```text
Tj,new = Tamb + Ploss(Tj) * Rtheta_JA
```

It reports active/SR/slow-device loss and temperature, convergence, thermal-limit status and electrical screening status. The thermal network is lumped; interface spreading, heatsink coupling, airflow, transient Zth and CFD/hardware correlation remain external validation tasks.

## 4. AC Line / PF / THD

This page consumes the exact `PFCLineCycleWaveforms` returned by `simulate_pfc_line_cycle()` and shows the settled final electrical cycle. It reports Vin/Iin, real/apparent power, PF, displacement/distortion factor, current THD, fundamental current, bus average/ripple, bus-capacitor RMS current, duty range, tracking error and minimum-pulse activity.

The harmonic spectrum comes from the same solver metrics. The GUI does not introduce a second PF/THD calculation path.

## 5. Switching / Zero Crossing

This page consumes the same settled AC result and `build_pfc_switching_waveforms()`. The user can rebuild a local switching workpoint at another electrical angle without running a second AC solver or changing the plant model.

It exposes HF/LF gate states, switch-node and inductor voltage, inductor/device currents, boost-output/bus-capacitor current, source time, PWM state, duty and zero-crossing state-machine signals.

The old embedded AC/switching tabs are removed from the visible Control Lab. Their widgets remain alive internally during this migration tranche because the historical renderer still updates them; those legacy render calls can be deleted after the new pages accumulate regression history.

## 6. Control / Sensing / Bode

The existing detailed PFC control model remains the owner of:

- current inner loop;
- bus-voltage outer loop;
- AMC/reference scheduling;
- duty feedforward and induction compensation;
- analog sensing/filtering;
- ADC acquisition/conversion timing;
- ZOH/computation/PWM update delay;
- PI/PIF/2P2Z tuning and Bode margins.

This stage produces `PFCControlLabAnalysis.current_loop.controller` and `.voltage_loop.controller`, which are already exact discrete transfer functions in the project canonical convention.

## 7. Exact H(z) / C99

The analyzed controller objects are promoted into the formal downstream contract. No controller is re-discretized from `Kp/Ti` or analog poles/zeros after analysis.

Canonical convention:

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)
y[n] = sum(b[k] x[n-k]) - sum(a[k] y[n-k])
```

`PFCControlHandoff` stores current/voltage `b[]`, `a[]`, sample rates, limits, provenance and explicit nonlinear implementation semantics. It converts directly to `power_control_tools.models.DigitalTransferFunction` for `power_sim` by copying coefficients only.

The `Exact H(z) / C99` page verifies frequency-response identity between the analyzed controller and the `power_sim` transfer, displays the difference equations, and exports either a JSON manifest or an audited C99 package.

`generate_ttpl_control_code_exact()` adds:

- `exact_hz_manifest.json`;
- `ttpl_exact_hz_coefficients.h`;
- `EXACT_HZ_CONTRACT.txt`.

The topology C runtime retains kind-specific PI/PIF/2P2Z saturation and state behavior. **H(z) owns the linear coefficients; H(z) does not by itself define anti-windup, reset ordering or protection-state behavior.** See [PFC_EXACT_HZ_HANDOFF.md](PFC_EXACT_HZ_HANDOFF.md).

## 8. Closed-Loop Verification

The TTPL closed-loop stage is now implemented with real shared `libngspice`. It consumes the frozen `PFCControlHandoff` directly and checks `assert_handoff_matches_analysis()` before transient execution.

The core contract is:

```text
PFCControlLabAnalysis
      ↓
PFCControlHandoff
      ↓
Exact current H(z) + exact voltage H(z)
      ↓
firmware-correlated sampled runtime
      ↓
Duty / PWM / zero-cross gate state
      ↓
shared-ngspice four-switch TTPL circuit
      ↓
IL / Vbus / switch node / gate vectors
```

### 8.1 No controller reconstruction

The closed-loop runner does **not** recreate the controller from `Kp`, `Ti`, analog poles/zeros or a second S-to-Z conversion.

The normalized `b[]/a[]` coefficients in `PFCControlHandoff` remain the linear-controller source of truth. Result metadata records the coefficients used and keeps `controller_re_discretized=False`.

### 8.2 Four-switch TTPL circuit

The circuit-level model contains:

- sinusoidal AC source;
- boost inductor and DCR;
- HF high/low controlled switches;
- LF positive/negative controlled switches;
- antiparallel diode paths;
- DC-bus capacitor and ESR;
- resistive load for the present switching-correlation layer;
- small physical damping/bleeder paths for numerical regularization.

This is a switching-correlation circuit, not a vendor semiconductor sign-off model.

### 8.3 Firmware-correlated controller state

`pfc_design.control.firmware_runtime` adds a float32 execution layer around the exact H(z) contract.

For PI/PIF controllers, the internal Tustin PI state is derived algebraically from the frozen H(z) coefficients. The runtime then applies the explicit nonlinear state semantics used by the project:

- float32 state updates;
- conditional-integrator freeze on outward saturation;
- PIF output filtering after PI saturation;
- 2P2Z execution with clamped output stored in denominator history;
- explicit reset support for zero-cross transitions.

This does not replace the exact H(z): the decomposition is checked against the frozen coefficients before use.

### 8.4 Sensing and ADC runtime

`PFCSampledSenseRuntime` models the configured measurement path in time domain:

```text
physical signal
 -> analog first-order poles
 -> sampled ADC event
 -> configured ADC-resolution quantization
 -> multi-SOC recursive stage
 -> digital filter
 -> sampled engineering-unit feedback
```

The ADC LSB is derived from configured Vref, bit width and raw front-end gain. State/filter arithmetic is float32.

Current limitation: the project schema does not yet carry board-specific ADC common-mode offset, unipolar rail clipping and every MCU ADC peripheral detail. The present quantizer therefore uses calibrated signed engineering units. It is closer to firmware behavior than V1, but should be described as **firmware-correlated**, not absolute MCU-code bit identity.

### 8.5 Timing and PWM application

The shared-ngspice event scheduler keeps continuous electrical state inside ngspice and aligns important discrete events:

- current-loop sample ticks;
- AMC ticks;
- voltage-loop ticks;
- sensing sample events;
- PWM/deadtime edges;
- duty-application events.

Coincident control ticks follow deterministic ordering. Duty updates can use PWM shadow-application behavior instead of changing pulse width at an arbitrary adaptive-solver point.

### 8.6 Eight-state zero-cross runtime

The time-domain runtime carries the TTPL commutation sequence:

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

The zero-cross runtime controls PI reset requests, LF state, HF inhibit/soft-start behavior and target polarity. Gate scheduling consumes the zero-cross state instead of using only the instantaneous sign of Vac.

The implementation is aligned with the maintained PFC waveform-state contract and the TI-style state progression used as the reference for this architecture. Exact register sequencing/deadband counter values still require board/firmware-specific evidence.

### 8.7 Current evidence

The dedicated `ngspice-smoke` CI installs real `ngspice` and `libngspice` and exercises live simulator integration. Software regression covers exact-H(z) identity, float32 controller behavior, sensing quantization/filter state and zero-cross sequencing.

Passing these tests proves architecture/runtime execution. It does not prove hardware accuracy.

## Explicit model boundaries

The engineering layers do not hide model limitations. The following remain separate validation requirements:

- DCM/CRM fidelity around line zero crossing beyond the present engineering model;
- board-specific ADC offsets, rails, calibration and exact C2000 SOC/interrupt timing;
- full ePWM register/TBPRD/deadband/compare-action bit identity;
- complete startup/protection/recovery state machines;
- real gate-driver propagation/skew and parasitic commutation;
- nonlinear Coss/Qoss/Eoss curves;
- switching-energy dependence on temperature, gate resistance and commutation path;
- vendor-specific capacitor lifetime/frequency multipliers;
- EMI filter interaction;
- detailed heatsink/airflow/transient thermal correlation;
- hardware validation.

## Next implementation sequence

1. Add board-specific ADC offset/rail/calibration and explicit C2000 timing semantics where the project schema has sufficient evidence.
2. Refine PWM/deadband/TBPRD/compare behavior toward peripheral-level correlation.
3. Integrate startup/protection/recovery state semantics into the TTPL closed-loop harness.
4. Add higher-fidelity semiconductor/parasitic model options without weakening the current fast engineering mode.
5. Remove transitional hidden legacy AC/switching renderers after sufficient regression history.
6. Apply the same engineering architecture to Vienna, including split-bus, midpoint-balance, exact-H(z) handoff and circuit-level verification.
