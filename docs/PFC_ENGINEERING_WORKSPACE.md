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

## Phase 1 implemented in PR #9

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

The GUI now exposes two TTPL stages:

1. `Power Stage / Sizing`
2. `Control / Sensing / AC / Switching`

`Apply` copies the electrical specification, calculated `Lboost` and recommended `Cbus` into the existing mature control/line-cycle/switching path. Existing Bode, sensing, PF/THD, inductor-design and C99 features are preserved rather than reimplemented.

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

## Explicit non-goals of Phase 1

This sizing layer is **not** used to hide nonlinear behaviour. The following remain later validation stages:

- DCM/CRM transition near line zero crossing;
- minimum-pulse parking and zero-crossing commutation;
- nonlinear semiconductor capacitances and switching loss;
- EMI filter interaction;
- current saturation / duty saturation state-machine behaviour;
- thermal correlation;
- circuit-level closed-loop ngspice execution;
- hardware validation.

## Next implementation sequence

1. Shared PFC semiconductor library and TTPL device/loss comparison.
2. Dedicated capacitor and thermal design page.
3. Split AC/PF/THD and switching views out of the monolithic control page.
4. Exact H(z) handoff and one digital-controller source of truth.
5. TTPL shared-ngspice closed-loop verification.
6. Apply the same architecture to Vienna, including split-bus and midpoint-balance design.
