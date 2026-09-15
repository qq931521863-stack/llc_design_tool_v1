# Digital Control Architecture

## 1. Scope

Power Design Toolkit treats digital control as a first-class engineering object, not as a plotting accessory. The canonical chain is:

```text
Control Tools exact H(z)
        -> plant G(s)/G(z)
        -> sensing / ADC / sampling
        -> modulator / PWM / ZOH
        -> timing delay
        -> open-loop and closed-loop analysis
        -> C99 float32_t
        -> optional switching-circuit verification
```

The controller/filter engine in `power_control_tools` is the common source for Control Tools, FRA design and code generation. Topology workspaces may provide their own local controller editors, but the exact discrete coefficients remain the interface between design and implementation.

## 2. Canonical H(z) contract

The project uses transfer functions in powers of `z^-1`:

```text
H(z) = (b0 + b1 z^-1 + ... + bm z^-m)
       ---------------------------------
       (1  + a1 z^-1 + ... + an z^-n)
```

with the difference equation:

```text
y[k] = Σ bi x[k-i] - Σ aj y[k-j]
```

`a0` is normalized to 1. The same sign convention is used by the Python frequency response, runtime execution and C99 generator. PI controllers may legitimately contain a pole at `z=1`; standalone marginal stability of the controller is therefore not automatically treated as an implementation error.

When Control Tools sends a controller into the LLC loop, the exact `b/a` arrays and sample rate are used. The receiver does **not** infer or re-fit another PI/PID structure from those coefficients.

## 3. Analog-to-digital mapping

Current controller/filter design supports Tustin, prewarped Tustin and backward-Euler mappings. The selected discretization method and sample rate are part of the controller definition and must match the target firmware timing.

The important distinction is between:

- the analog structure used to design the compensator;
- the exact discrete H(z) that is finally analyzed/exported/executed.

The second object is the implementation authority.

## 4. Mixed-domain loop model

The LLC digital-voltage-loop model evaluates each block in its native domain:

- continuous power-stage and analog-sense blocks at `s = jω`;
- digital controller/filter blocks at `z = exp(jωTs)`;
- explicit ZOH/PWM/timing terms at their physical locations.

Typical LLC loop:

```text
C(z)
  · G_FM
  · G_vf(s)
  · H_sense(s)
  · H_ADC(z, sampling timing)
  · H_delay
```

A separate all-discrete approximation is constructed for z-plane pole analysis. This avoids pretending that continuous plant dynamics and discrete firmware are the same mathematical object while still allowing closed-loop pole checks.

## 5. Sensing and ADC timing

The sensing path can include analog divider/op-amp/RC response, ADC acquisition aperture, multiple SOC samples and recursive averaging.

A key project convention is that delays are owned exactly once:

```text
ADC/SOC acquisition and conversion timing -> sampling block
firmware computation + PWM update timing  -> command/timing block
```

Do not count the same ADC delay again in the firmware block. The complete loop can evaluate minimum/nominal/maximum timing envelopes where the PWM update wait varies with the switching event.

## 6. LLC frequency modulation

LLC frequency modulation remains explicit. The model supports:

- PCMD -> switching frequency;
- PCMD -> TBPRD;
- timer clock frequency;
- up-count or up/down-count PWM semantics;
- piecewise LUT interpolation;
- local small-signal slope at the operating point;
- command headroom/polarity diagnostics;
- timer/TBPRD quantization in nonlinear runtime paths.

For an up/down counter:

```text
Fsw = TBCLK / (2 · TBPRD)
```

The linear Bode model uses the local modulator slope because it is a small-signal model. Nonlinear co-simulation can execute the sampled controller and quantized modulation path directly; see `docs/NGSPICE_CLOSED_LOOP.md` for the current switching-circuit boundary.

## 7. Stability outputs

The loop analyzer does not assume there is only one crossover. It searches all available gain and phase crossings and reports the critical/worst margins.

Depending on workspace and model, outputs include:

- gain-crossover frequencies;
- phase margins;
- phase-crossover frequencies;
- gain margins;
- delay margin;
- sensitivity `S = 1/(1+L)`;
- complementary sensitivity `T = L/(1+L)`;
- discrete closed-loop poles and maximum pole radius;
- command/modulator headroom warnings.

A positive PM/GM from sampled frequency data is evidence within the analyzed model and frequency band. It is not a topology-independent proof of nonlinear closed-loop stability.

## 8. C99 export

`power_codegen` and Control Tools export portable C99/`float32_t` control cores. The generated controller uses the same H(z) coefficient convention as the Python reference.

Where a C compiler is available, regression tests compile and execute the generated C and compare its impulse/step behavior with the Python model. That verifies coefficient/sign/runtime consistency; it does not verify ISR scheduling, ADC trigger placement, peripheral configuration or plant behavior on a specific DSP.

## 9. Nonlinear features outside linear Bode

The following behavior must not be silently folded into one LTI transfer function:

- saturation and anti-windup policy;
- soft start;
- burst mode;
- minimum pulse;
- current-limit state selection;
- protection/state-machine transitions;
- line zero-crossing state changes;
- PWM quantization and switching ripple.

Those features require time-domain/runtime verification at the appropriate model fidelity.

## 10. Engineering rule

Design flow should remain:

```text
controller structure
 -> exact H(z)
 -> complete loop margins
 -> code-generation equivalence
 -> nonlinear/switching verification where needed
 -> hardware correlation
```

Do not skip from a nominal Bode plot directly to a hardware-release claim.
