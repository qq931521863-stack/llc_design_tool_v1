# LLC Model Hierarchy and Engineering Boundaries

Power Design Toolkit deliberately maintains more than one LLC model. A fast analytical model, a harmonic model, a switching time-domain model and a circuit simulator do not have the same purpose. The correct question is not “which one is best?” but “which fidelity is required for the claim being made?”

## 1. Fidelity ladder

```text
FHA analytical design
        ↓
nonlinear multi-harmonic balance (HB)
        ↓
internal switched time-domain solver
        ↓
shared-ngspice switching correlation
        ↓
detailed/vendor device model
        ↓
hardware measurement
```

Each level should be correlated against the previous one before more nonidealities are added.

## 2. FHA — design baseline

The fundamental-harmonic approximation converts the rectifier/load to an equivalent AC load and evaluates the resonant tank at the switching fundamental.

Typical uses:

- initial Lr/Cr/Lm synthesis;
- resonant frequency, characteristic impedance, Q and Ln;
- gain-frequency maps;
- fast operating-point sweeps;
- first-order topology feasibility checks.

FHA is fast and interpretable, but it does not reproduce switching edges, higher-harmonic rectifier interaction or device-level commutation. It therefore must not be used by itself to claim detailed ZVS margin, ringing, switching loss or SR stress.

## 3. Nonlinear multi-harmonic balance

The HB solver keeps selected odd harmonics and solves them together with the nonlinear rectifier polarity/commutation state. It is not simply several independent FHA circuits added after the fact.

Use HB when:

- higher harmonics materially affect current/stress;
- the operating point is far enough from the FHA assumptions that a richer periodic solution is useful;
- a frequency-domain periodic solution is preferred over a long transient settle.

Light-load/discontinuous branch changes are intrinsically more difficult. When the solver falls back to a regularized result or reports a warning, that diagnostic is part of the engineering result and should not be discarded.

## 4. Internal switched time-domain solver

The internal switched solver integrates the nonlinear piecewise switching model and solves for a periodic steady state. The current implementation includes the resonant states and output dynamics, idealized switching/rectification behavior, and periodic shooting/settling logic.

Use it for:

- switching-period waveforms;
- resonant current/capacitor stress;
- consistency checks against HB/FHA;
- waveform statistics used by the GUI.

It is still an engineering model, not a vendor semiconductor simulator. Device capacitance, reverse recovery, layout parasitics and detailed deadtime commutation require a higher fidelity layer.

## 5. shared-ngspice switching correlation

The ngspice path converts the toolkit's design objects into a backend-neutral Circuit IR and then into a SPICE circuit. The current full-bridge LLC model includes:

- DC bus;
- controlled full bridge;
- Lr with small series damping;
- Cr with ESR;
- coupled transformer with finite coupling/winding damping;
- diode full bridge;
- Cout/ESR and load.

The simulator can run in batch mode for smoke/correlation work or through `libngspice` for a continuous-state digital closed loop.

This level is intended to verify that the real sampled H(z), modulation timing and switching circuit interact causally. It is **not** yet a release-accurate MOSFET/SR loss model.

See [NGSPICE_CLOSED_LOOP.md](NGSPICE_CLOSED_LOOP.md).

## 6. ZVS interpretation

The toolkit separates two ideas that are often mixed together:

1. **Theoretical inductive operation** — tank/input impedance is on the inductive side.
2. **Engineering ZVS margin** — available commutation current/deadtime compared with device charge/energy demand.

Even the second is an engineering criterion, not a transistor-level transient proof. Final ZVS verification should use detailed device/parasitic models and measurement at relevant voltage, current and temperature.

## 7. Magnetics and losses

Transformer and resonant-inductor tools provide engineering synthesis and loss estimates. Their usefulness depends on the quality of the material/core/device data supplied to them.

The repository contains reference datasets for software regression and design exploration. Unless a specific part/material record has traceable manufacturer evidence and the required temperature/frequency/flux range, it must not be treated as hardware-qualified data.

See [ENGINEERING_VALIDATION.md](ENGINEERING_VALIDATION.md).

## 8. Recommended model selection

| Engineering question | Minimum useful level |
| --- | --- |
| Can the tank reach required gain? | FHA |
| How do Q/Ln/load shift the gain region? | FHA |
| Do higher harmonics change periodic current/stress? | HB |
| What do idealized switching-period waveforms look like? | Internal switched TD |
| Does exact sampled H(z) control a continuous switching circuit correctly? | shared-ngspice |
| What is device ringing/Coss/Qrr switching loss? | Detailed device SPICE + parasitics |
| Is the design safe/releasable? | Reviewed hardware evidence |

## 9. Rule for reports and README claims

Every result should name the model that produced it. Terms such as “verified,” “stable,” “ZVS,” “loss,” and “validated” must always be interpreted within the stated model and evidence level.
