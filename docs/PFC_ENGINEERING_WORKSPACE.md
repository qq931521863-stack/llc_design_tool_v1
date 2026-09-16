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
8. Closed-Loop Verification     (next phase)
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

Phase 5 promotes those analyzed controller objects into the formal downstream contract. No controller is re-discretized from `Kp/Ti` or analog poles/zeros after analysis.

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

The topology C runtime retains the existing kind-specific PI/PIF/2P2Z saturation and anti-windup/state behavior. **H(z) owns the linear coefficients; H(z) does not define anti-windup.** See [PFC_EXACT_HZ_HANDOFF.md](PFC_EXACT_HZ_HANDOFF.md).

## Explicit model boundaries

The engineering layers do not hide model limitations. The following remain separate validation requirements:

- DCM/CRM fidelity around line zero crossing beyond the present averaged-plant assumptions;
- real gate-driver propagation/skew and parasitic commutation;
- nonlinear Coss/Qoss/Eoss curves;
- switching-energy dependence on temperature, gate resistance and commutation path;
- vendor-specific capacitor lifetime/frequency multipliers;
- EMI filter interaction;
- detailed heatsink/airflow/transient thermal correlation;
- circuit-level closed-loop ngspice execution;
- hardware validation.

## Next implementation sequence

1. TTPL shared-ngspice closed-loop verification consuming the exact H(z) handoff.
2. Remove transitional hidden legacy AC/switching renderers after sufficient regression history.
3. Apply the same engineering architecture to Vienna, including split-bus and midpoint-balance design.
