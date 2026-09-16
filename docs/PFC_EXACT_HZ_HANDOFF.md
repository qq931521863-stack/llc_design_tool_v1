# PFC Exact H(z) Handoff

The TTPL PFC workflow uses the **analyzed discrete controller** as the linear-controller source of truth for Bode analysis, downstream simulation, C99 audit artifacts and future shared-ngspice closed-loop verification.

## Canonical convention

```text
H(z) = (b0 + b1 z^-1 + ...)/(1 + a1 z^-1 + ...)

y[n] = sum(b[k] x[n-k]) - sum(a[k] y[n-k])
```

The denominator is normalized to `a0 = 1`. This is the same sign convention used by the digital-loop analysis and `power_sim.digital_control.DigitalTransferRuntime`.

## Why a handoff artifact exists

Historically PFC controller configuration (`Kp`, `Ti`, PIF pole or 2P2Z coefficients) could be passed to another subsystem and discretized or reconstructed again. That is unnecessary and creates a risk that:

- Bode analysis uses one H(z);
- generated firmware uses a slightly different implementation;
- time-domain simulation reconstructs another H(z);
- a future circuit co-simulation uses yet another coefficient set.

`pfc_design.control.handoff.PFCControlHandoff` freezes the already-computed current-loop and voltage-loop transfer functions directly from `PFCControlLabAnalysis`.

No S2Z operation occurs in the handoff.

## Artifact content

Each controller artifact includes:

- controller role (`current` or `voltage`);
- controller kind;
- exact normalized `b[]` and `a[]`;
- sample rate and sample time;
- output limits;
- coefficient/difference-equation convention;
- explicit saturation / anti-windup semantics;
- explicit controller-state semantics;
- provenance identifying the analyzed controller object.

The complete TTPL handoff also includes AMC rate, switching rate and duty limits.

## power_sim handoff

`PFCControllerArtifact.runtime_transfer()` converts the analyzed coefficients into `power_control_tools.models.DigitalTransferFunction` directly:

```text
analysis H(z)
    ↓  coefficient copy only
PFCControllerArtifact
    ↓  coefficient copy only
power_control_tools.models.DigitalTransferFunction
    ↓
power_sim.digital_control.DigitalTransferRuntime
```

The conversion is regression-tested by comparing complex frequency response across the usable digital frequency range. Re-discretization is prohibited.

## C99 handoff

`power_codegen.generate_ttpl_control_code_exact()` wraps the existing TTPL C99 generator and adds:

- `exact_hz_manifest.json`
- `ttpl_exact_hz_coefficients.h`
- `EXACT_HZ_CONTRACT.txt`

The existing C runtime intentionally retains controller-kind-specific nonlinear implementation semantics. For example, PI/PIF conditional integrator freeze is not replaced with a generic transfer-function state clamp merely to make the runtime look uniform.

Therefore the contract is deliberately split:

```text
Linear controller coefficients
    exact H(z) artifact is authoritative

Nonlinear implementation semantics
    controller kind / saturation / anti-windup / state update are explicit
```

H(z) alone does **not** define anti-windup.

## GUI workflow

The TTPL workspace now ends with:

```text
6. Control / Sensing / Bode
        ↓
7. Exact H(z) / C99
```

The old direct C99 button inside the Control Lab is hidden from the normal workflow. The Exact H(z) page first validates/freeze the analyzed controller and then exports either the JSON contract or the audited C99 package.

## Future shared-ngspice rule

The TTPL shared-ngspice closed-loop implementation must consume the exact H(z) handoff produced here. It must not recreate the current or voltage controller from `Kp/Ti`, analog poles/zeros, or GUI settings.

The remaining closed-loop implementation still needs to model separately:

- ADC/sensing timing and quantization;
- multi-rate current / AMC / voltage scheduling;
- duty feedforward;
- controller output saturation / anti-windup;
- minimum-pulse and zero-crossing logic;
- switching plant / bus dynamics;
- protection and state-machine behavior where relevant.

Those features are not encoded by the linear H(z) coefficients and must stay explicit.
