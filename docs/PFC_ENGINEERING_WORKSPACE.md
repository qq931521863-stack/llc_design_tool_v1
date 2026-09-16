# PFC Engineering Workspace V2

The PFC workspace is being upgraded from a control-lab-first page into an engineering workflow comparable to the LLC workspace.

## Product workflow

```text
Electrical Requirements
        ↓
Power Stage / Sizing
        ↓
Device / Loss Selection
        ↓
Capacitor / Thermal Design
        ↓
AC Line Cycle / PF / THD
        ↓
Switching Workpoints / Zero Crossing
        ↓
Sensing / ADC / Bode
        ↓
Current Loop + Voltage Loop
        ↓
Exact H(z) / C99
        ↓
Switching Closed-Loop Verification
```

The first implementation target is **single-phase totem-pole PFC (TTPL)**. Vienna will reuse the same workflow after the TTPL architecture is proven.

## Phase 1 — specification to power-stage sizing

The deterministic `pfc_design.engineering` kernel owns specification-to-hardware sizing. It is intentionally separate from both the historical two-phase PFC loss model and the TTPL control laboratory.

Inputs include minimum / nominal / maximum AC RMS voltage, line frequency, DC-bus voltage, output power and efficiency estimate, switching frequency, boost-inductor ripple target, twice-line bus-ripple target, hold-up time/end voltage and duty/minimum-pulse limits.

Outputs include low/nominal/high-line current stress, required boost inductance, line-cycle duty and current/ripple envelope, high-line boost headroom, DC-bus capacitance from twice-line ripple and hold-up, recommended capacitance and a first-order capacitor twice-line RMS-current estimate.

`Apply` copies the electrical specification, calculated `Lboost` and recommended `Cbus` into the existing mature control/line-cycle/switching path. Existing Bode, sensing, PF/THD, inductor-design and C99 features are preserved rather than reimplemented.

## Phase 2 — PFC MOSFET library and TTPL device/loss screening

`PFCDeviceDatabase` merges the packaged PFC MOSFET data with a persistent user library. User records are stored outside the installed package and can be created, edited, cloned, deleted, imported and exported as JSON. The same database is intended for TTPL, Vienna and future PFC design pages.

Built-in PFC MOSFET records are deliberately shown as **Built-in / unverified**. The repository engineering-data catalogue does not yet provide normalized datasheet revision/extraction provenance for those named records, so they are screening inputs rather than hardware-release truth.

The TTPL device page compares two roles:

- **HF half-bridge** — one candidate MOSFET is evaluated in both active-boost and synchronous-rectifier positions;
- **line-frequency leg** — the complete two-device slow leg is evaluated from line-current conduction plus gate drive.

The HF screening model integrates over the selected low/nominal/high-line half-cycle:

```text
I_local,rms^2(theta) = Iavg(theta)^2 + DeltaIL(theta)^2 / 12
Pactive,cond = RDS(T) * mean[D * I_local,rms^2]
PSR,cond     = RDS(T) * mean[(1-D) * I_local,rms^2]
```

Switching energy uses the database Eon/Eoff reference point when available:

```text
E(V,I) = Eref * (V / Vref) * (I / Iref)
```

and otherwise falls back to the linear `tr/tf` edge estimate. The page also reports active-switch switching loss, SR turn-off loss, deadtime reverse-conduction loss, Coss/gate loss, total loss and VDS/current screening checks.

A datasheet Eoff point may already include some output-capacitance energy, so a separate Coss term can overlap that energy. The UI exposes this modelling limitation instead of treating the number as a release-grade loss prediction.

## Phase 3 — DC-bus capacitor bank and thermal screening

The capacitor page starts from the Phase-1 required bus capacitance and twice-line RMS ripple-current estimate. For an identical-capacitor bank with `Ns` devices in series and `Np` strings in parallel:

```text
Ns = ceil(Vbus / (Vrated * voltage_derating))
Np,C = ceil(Crequired * Ns / Cunit)
Np,I = ceil(Icap,rms / (Irated * ripple_derating))
Np = max(Np,C, Np,I)

Cbank   = Cunit * Np / Ns
ESRbank = ESRunit * Ns / Np
Pesr    = Icap,rms^2 * ESRbank
```

The page reports bank topology/count, actual capacitance, ESR, total/per-cap ripple current, ESR loss, per-cap voltage, predicted twice-line bus ripple and PASS/FAIL against capacitance, voltage and ripple-current constraints.

Per-cap hot-spot temperature is estimated with a lumped thermal resistance:

```text
Tcap = Tamb + Pcap,ESR * Rtheta,cap
```

and lifetime uses the common empirical 10 °C rule:

```text
Life = Life_rated * 2^((T_rated - Tcap) / 10)
```

This lifetime estimate is deliberately labelled as screening only. Vendor frequency multipliers, ripple-current life equations, electrolyte chemistry and case-specific thermal data remain required for release decisions. Series strings also require an actual voltage-sharing/balancing design; equal voltage sharing is only an engineering assumption here.

The selected physical capacitor bank can be applied explicitly to the downstream control plant. That action transfers **actual Cbank and ESR**, rather than silently retaining the minimum capacitance calculated during Phase 1.

### Semiconductor loss ↔ temperature fixed point

The thermal page uses selected HF and line-frequency MOSFETs from the shared PFC device library. It iterates semiconductor loss and lumped junction temperature until convergence:

```text
Tj,new = Tamb + Ploss(Tj) * Rtheta_JA
```

For the HF half-bridge, active and SR conduction/switching losses are kept separate; common two-device Coss/gate/deadtime terms are divided equally for the thermal estimate. For the two-device slow leg, the total line-leg loss is divided equally between the physical devices.

The result reports active/SR/slow-device loss and junction temperature, fixed-point convergence, thermal-limit PASS/FAIL, semiconductor VDS/current PASS/FAIL and the switching-loss model used.

The thermal network is intentionally lumped. Junction-to-case, interface spreading, shared heatsink, airflow, transient thermal impedance and CFD/hardware correlation are not inferred from a single Rtheta input.

## Phase 4 — first-class AC/PF/THD and switching validation

The mature TTPL time-domain solver already produced full AC-cycle, harmonic, local switching and zero-crossing results, but those outputs were historically buried inside the large Control Lab page. Phase 4 changes the **product architecture**, not the solver equations.

The TTPL workflow now presents six first-class stages:

1. `Power Stage / Sizing`
2. `Devices / Loss`
3. `Capacitor / Thermal`
4. `AC Line / PF / THD`
5. `Switching / Zero Crossing`
6. `Control / Sensing / Bode`

### AC Line / PF / THD

The AC page consumes the exact `PFCLineCycleWaveforms` returned by `simulate_pfc_line_cycle()` and shows only the settled final electrical cycle. It reports:

- Vin RMS, Iin RMS/peak and real/apparent power;
- PF, displacement factor and distortion factor;
- current THD and fundamental current;
- bus average/ripple and bus-capacitor RMS current;
- duty range, current-error RMS and zero-crossing error RMS;
- minimum-pulse activity fraction;
- integer-harmonic spectrum from the same solver metrics.

The line-cycle plots expose grid voltage/input current, Vbus/capacitor current, Iref/current tracking and nonlinear duty/zero-crossing constraints. No independent PF/THD calculation path is introduced in the GUI.

### Switching / Zero Crossing

The switching page consumes the same settled AC result and `build_pfc_switching_waveforms()` reconstruction. A user may change electrical angle and rebuild the local switching workpoint **without running a second AC solver or changing the plant model**.

The local view reports/plots:

- HF high/low and LF-polarity gate states;
- switching-node and inductor voltage;
- inductor average/ripple current and HF device currents;
- boost-output and DC-bus capacitor current;
- source time, PWM state and duty at the selected settled line-cycle point.

The zero-crossing view uses the full final AC cycle and exposes current reference/tracking, current PI reset strobe, effective minimum duty, minimum-pulse activation, PWM state code, zero-crossing-active flag and deadband fraction.

The old embedded AC/switching result tabs are removed from the visible Control Lab UI so the user no longer sees two competing workflows. Their widgets are retained internally for this migration tranche because the historical `set_result()` renderer still updates them; once the new pages have accumulated regression history, those legacy rendering calls can be removed safely.

## Current equations and boundaries

### Input current

For the unity-power-factor first-order design model:

```text
Pin = Pout / eta
Iin,rms = Pin / Vin,rms
Iin,pk = sqrt(2) * Iin,rms
```

### Boost ripple

For rectified instantaneous input voltage `v`:

```text
D = 1 - v / Vbus
DeltaIL_pp = v * D / (L * fsw)
```

The required inductance is selected so the maximum low-line ripple over the half-line cycle does not exceed the specified fraction of low-line sinusoidal peak current.

### Twice-line DC-bus ripple

```text
DeltaVpp ≈ Pout / (omega_line * Cbus * Vbus)
omega_line = 2*pi*fline
Cbus,ripple >= Pout / (omega_line * Vbus * DeltaVpp_allowed)
```

### Hold-up

```text
Cbus,hold >= 2 * Pout * thold / (Vbus^2 - Vend^2)
Cbus,recommended = max(Cbus,ripple, Cbus,hold)
```

## Explicit model boundaries

The engineering sizing/device/thermal/time-domain layers are **not** used to hide model limitations. The following remain separate validation stages:

- DCM/CRM fidelity around the zero crossing beyond the current averaged-plant approximation;
- real gate-driver propagation/skew and parasitic commutation;
- nonlinear Coss/Qoss/Eoss curves;
- switching-energy dependence on temperature, gate resistance and commutation path;
- vendor-specific capacitor lifetime/frequency multipliers;
- EMI filter interaction;
- detailed heatsink/airflow/transient thermal correlation;
- circuit-level closed-loop ngspice execution;
- hardware validation.

## Next implementation sequence

1. Exact H(z) handoff and one digital-controller source of truth.
2. TTPL shared-ngspice closed-loop verification.
3. Apply the same engineering architecture to Vienna, including split-bus and midpoint-balance design.
