# ngspice Closed-Loop — Exact H(z) + Firmware FM LUT R1

Date: 2026-09-15

## Scope

This follow-up closes the remaining modulator gap after the ngspice foundation merge.

The transient verification path is now intended to execute:

```text
Vout (shared-ngspice)
    -> Sampler / ADC timing
    -> exact digital controller H(z)
    -> controller perturbation ΔPCMD
    -> steady PCMD bias + ΔPCMD
    -> firmware PCMD LUT
    -> TBPRD interpolation / timer quantization
    -> phase-continuous bridge gates
    -> shared-ngspice LLC power stage
    -> Vout
```

No controller re-discretization is allowed in this path.

## FM LUT semantics

The existing LLC digital-loop model already owns the firmware-style `FrequencyModulatorLUT`. The runtime adapter consumes the same table as either:

- PCMD -> TBPRD, or
- PCMD -> switching frequency.

For PCMD -> TBPRD with up/down PWM counting:

```text
Fsw = TBCLK / (2 * TBPRD)
```

The exact H(z) designed by the small-signal/FRA/control tools produces a perturbation around the operating PCMD:

```text
PCMD_abs = PCMD_bias + ΔPCMD_controller
```

The absolute command is limited to the LUT domain and additionally constrained by the active LLC design's Fmin/Fmax. Timer quantization is applied after LUT interpolation.

## Why the bias is required

The linear controller is designed around a nonzero LLC operating point. Treating controller output as an absolute PCMD would discard that operating point and cause an artificial large-signal command jump at t=0. The explicit bias preserves the small-signal design interpretation while still exercising the nonlinear firmware LUT during transient simulation.

## Validation

A dedicated live shared-ngspice test uses:

- real `libngspice` installed in CI;
- a 40 kHz exact discrete PI H(z);
- a nonlinear PCMD -> TBPRD table centered at TBPRD=600 / 100 kHz;
- +Vref step;
- real switching LLC CircuitIR.

Acceptance checks include:

- sampled error changes at the exact digital tick;
- H(z) controller output moves in the correct direction;
- TBPRD changes according to the table;
- Fsw obeys `TBCLK/(2*TBPRD)` after quantization;
- switching frequency changes in the correct LLC control direction;
- shared-ngspice continues to the requested stop time with finite Vout vectors.

This is an end-to-end controller/modulator/simulator plumbing validation. It is not a claim that the test PI gains are a production-optimal LLC loop.

## Model boundary

The power stage remains the V1 switching-correlation model: controlled ideal bridge switches plus explicit small physical damping, coupled transformer, diode bridge, output capacitor and load. It is not yet a vendor MOSFET/Coss/Qrr/SR loss/stress model.
