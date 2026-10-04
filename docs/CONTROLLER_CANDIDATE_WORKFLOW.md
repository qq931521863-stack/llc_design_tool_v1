# Controller candidate → compare → Apply

The LLC and TTPL desktop workspaces reuse their existing **Fc × PM Solution Map**
for controller design. There is one applied controller in the maintained analysis;
a candidate is a preview until **Apply selected PI** is pressed.

## Use

1. Build the maintained LLC digital-loop or TTPL analysis, including plant,
   sensing, ADC, PWM/FM, sample rates and timing.
2. Open **Fc × PM Solution Map**. For TTPL choose the current inner loop or the
   bus-voltage outer loop. Enter the candidate **Fc / PM** and objective constraints.
3. Press **Generate candidate from Fc / PM**, or select a point from a built map.
   The existing exact firmware-Tustin PI solver is used. Infeasible targets retain
   their diagnostic status and cannot be applied.
4. Inspect **Current / candidate comparison**: overlaid open-loop Bode,
   sensitivity S, closed-loop frequency response T, Fc/PM/GM, Ms/Mt and candidate
   H(z) coefficients. **Cancel preview** leaves the maintained controller unchanged.
5. Press **Apply selected PI**. The existing analysis is rebuilt with the same
   candidate coefficients. The normal H(z), C99/export and supported SPICE paths
   consume this applied analysis. Repeated clicks cannot apply the same stale
   candidate twice. Re-run analysis after changing physical/controller inputs.

The Guided System Design **Auto Design** mode seeds these targets and opens the
comparison after the maintained model is built. It does not apply a controller.
Both explicit Fc and PM are required for automatic generation. For TTPL, the
single guided target describes the **current inner loop**; design the outer loop
separately against the newly applied inner-loop closure.

## Scope and evidence boundaries

- Automatic synthesis currently supports **PI only**. PIF and 2P2Z auto-design
  requests are explicitly reported as unsupported rather than converted to PI.
- Comparison is frequency-domain evidence for the current modeled operating
  point. S/T curves are not switching transients, measured FRA, worst-corner
  qualification, or proof of hardware stability.
- Gain margin marked `not observed` means no qualifying phase crossover was found
  in the sampled frequency range; it is not a measured infinite margin.
- If switching frequency lies outside the analyzed range, |L(Fsw)| is explicitly
  marked unevaluated. A feasible candidate passes the available frequency-grid
  checks, not a missing switching-attenuation check.
- SPICE validation remains a separate explicit operation for the existing
  full-bridge LLC and TTPL paths. No additional topology or SPICE service is added.
- Candidate Kp/Ti and sampling values retain full precision through Apply and
  subsequent Run. Rounded spinboxes are display/edit adapters, not an alternate
  applied-controller source. Editing controller fields adopts the new user values.
- Changing targets/constraints, switching loop source, accepting a new system
  definition, or receiving analysis from stale inputs invalidates the preview.
  Reopening and cancelling Guided Design does not adopt a candidate.

This workflow changes desktop design tooling, not MCU peripheral configuration,
protection logic, or hardware qualification requirements.
