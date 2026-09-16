# PFC Engineering Workspace V2

The PFC workspace is being upgraded from a control-lab-first page into an engineering workflow comparable to the LLC workspace.

## Product workflow

```text
Electrical Requirements
        ↓
Power Stage / Sizing
        ↓
Inductor / Capacitor / Device Design
        ↓
Loss / Thermal / Efficiency
        ↓
AC Line Cycle / PF / THD
        ↓
Switching Workpoints / Zero Crossing
        ↓
Sensing / ADC
        ↓
Current Loop + Voltage Loop
        ↓
Exact H(z) / C99
        ↓
Switching Closed-Loop Verification
```

The first implementation target is **single-phase totem-pole PFC (TTPL)**. Vienna will reuse the same workflow after the TTPL architecture is proven.

## Phase 1 — specification to power-stage sizing

The new deterministic `pfc_design.engineering` kernel owns specification-to-hardware sizing. It is intentionally separate from both the historical two-phase PFC loss model and the TTPL control laboratory.

Inputs:

- minimum / nominal / maximum AC RMS voltage;
- line frequency;
- DC-bus voltage;
- output power and efficiency estimate;
- switching frequency;
- boost-inductor ripple target;
- twice-line bus-ripple target;
- hold-up time and hold-up end voltage;
- duty and minimum-pulse limits.

Outputs:

- low/nominal/high-line input-current stress;
- required boost inductance from the low-line ripple target;
- line-cycle duty and inductor ripple/current envelope;
- high-line boost headroom;
- DC-bus capacitance from twice-line ripple;
- DC-bus capacitance from hold-up energy;
- recommended capacitance as the more restrictive requirement;
- first-order capacitor twice-line RMS current estimate;
- explicit warnings for insufficient boost headroom, hold-up dominance, minimum-pulse limits and zero-current regions.

`Apply` copies the electrical specification, calculated `Lboost` and recommended `Cbus` into the existing mature control/line-cycle/switching path. Existing Bode, sensing, PF/THD, inductor-design and C99 features are preserved rather than reimplemented.

## Phase 2 — PFC MOSFET library and TTPL device/loss screening

The TTPL engineering workflow now contains three stages:

1. `Power Stage / Sizing`
2. `Devices / Loss`
3. `Control / Sensing / AC / Switching`

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

and otherwise falls back to the linear `tr/tf` edge estimate. The page also reports:

- active-switch switching loss;
- SR turn-off loss;
- deadtime reverse-conduction loss;
- two-device Coss loss;
- two-device gate-drive loss;
- total HF-leg loss;
- slow-leg conduction/gate loss;
- VDS derating PASS/FAIL;
- temperature-dependent current-rating PASS/FAIL.

This is intentionally a screening layer. A datasheet Eoff point may already include some output-capacitance energy, so a separate Coss term can overlap that energy. The UI exposes this modelling limitation instead of treating the number as a release-grade loss prediction.

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

Using the first-order energy-pulsation approximation:

```text
DeltaVpp ≈ Pout / (omega_line * Cbus * Vbus)
omega_line = 2*pi*fline
```

Therefore:

```text
Cbus,ripple >= Pout / (omega_line * Vbus * DeltaVpp_allowed)
```

### Hold-up

From capacitor energy:

```text
Cbus,hold >= 2 * Pout * thold / (Vbus^2 - Vend^2)
```

The design recommendation is:

```text
Cbus,recommended = max(Cbus,ripple, Cbus,hold)
```

## Explicit model boundaries

The engineering sizing/device layers are **not** used to hide nonlinear behaviour. The following remain later validation stages:

- DCM/CRM transition near line zero crossing;
- minimum-pulse parking and zero-crossing commutation;
- nonlinear Coss/Qoss/Eoss curves;
- switching-energy dependence on temperature, gate resistance and commutation path;
- EMI filter interaction;
- current saturation / duty saturation state-machine behaviour;
- thermal-network correlation;
- circuit-level closed-loop ngspice execution;
- hardware validation.

## Next implementation sequence

1. Dedicated capacitor and thermal design page.
2. Split AC/PF/THD and switching views out of the monolithic control page.
3. Exact H(z) handoff and one digital-controller source of truth.
4. TTPL shared-ngspice closed-loop verification.
5. Apply the same architecture to Vienna, including split-bus and midpoint-balance design.
