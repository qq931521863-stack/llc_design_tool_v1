# Guided System Design: loop-analysis scope and corrective validation

## Entry and supported models

Open **系统建模与设计 / Guided System Design** on the launcher, complete the eight
pages, then choose **Analyze Complete System**. This is distinct from the
**Power Stage Designer** analog-loop workspace.

The Guided adapter supports the LLC FM voltage loop and single-phase Totem-Pole
PFC current-inner / bus-voltage-outer loops. Its Vienna, Buck/Boost family,
generic and imported/FRA plant cards are not implemented and remain disabled.
Manual controller mode retains the actual expert controller; typing a target Fc
is not an Apply action. Automatic Fc/PM synthesis is bounded to Tustin PI, and
unimplemented structures must remain disabled rather than masquerading as PI.

This is a small-signal software model. A computed Bode plot, positive finite-band
margins or a host-verified controller export do not certify nonlinear startup,
current limiting, clipping, switching convergence or a hardware control loop.

## Confirmed defects and exact repairs

1. **LLC SOC timing.** The guided spacing was stored but never reached the ADC
   model. Literal spacing Δ now becomes aperture centres Ta/2 + iΔ. Trigger-to-EOC
   is (N−1)Δ + Ta + Tconv. The expert page displays the exact offsets; changing
   them invalidates results and candidate/export sources. Saved projects preserve
   the values. Zero spacing means simultaneous centres in this declared model,
   not an undocumented automatic sequential-conversion option. Sequences that
   do not fit the supported single control frame are rejected before analysis;
   command computation/PWM update delays remain separate and may span frames.
2. **TTPL command hold.** The Guided ZOH checkbox previously changed no result.
   It now controls the current PWM and AMC command holds, with the corresponding
   explicit expert option and saved configuration. ADC measurement holds remain
   separately modeled. Turning off one owner does not silently remove the others.
3. **PFC stability label.** Requiring a standalone PI controller to be strictly
   stable incorrectly rejected its intentional z=1 integrator. The UI/summary
   now report a finite-band margin screen and `closed_loop_stability=NOT_PROVEN`.
   Missing margins and arbitrary marginal/unstable controller poles are not
   accepted. A positive screen is not a complete Nyquist or hardware proof.
4. **LLC polynomial identity.** Continuous s-power coefficients have different
   units. Comparing each to the largest coefficient removed real high-order
   dynamics and produced a 2.05% in-band discrepancy in the audited case.
   Conversion now normalizes the selected input and output to avoid unit-scale cancellation,
   preserves all nonzero coefficients and evaluates the polynomial verbatim.
   Independent state-space equations, input/output unit changes and direct
   evaluation check the result. No acceptance tolerance was relaxed.
5. **PFC frequency domain.** Inner and outer loops require their own valid
   frequency grids. Requested frequency bounds remain saved; actual plotted and
   exported samples must stay strictly below every active sample-rate Nyquist
   boundary of the corresponding chain. The lower-rate outer loop must not
   inherit the inner loop's 20 kHz extent. Review and Analyze use the same
   configuration and report an invalid/empty range normally rather than raising
   an unhandled launcher exception.
6. **ADC operating headroom.** Positive gain alone is not a range check. Review
   now checks nominal DC output/bus, maximum AC peak and a conservative ideal-CCM
   input-current peak/ripple bound. The new Guided bus-divider example uses
   249 kΩ / 1.6 kΩ (illustrative, not a qualified board design). Loading an old
   overrange definition preserves its values and fails Review. Existing expert
   configurations are not silently altered: their analysis/export warns when
   the linear model omits clipping at the configured operating point. Bipolar
   offsets, transients and board-specific ADC availability still require review.

7. **PFC phase budget.** TTPL's Gvg already contains the closed current loop;
   the budget previously listed that inner response twice. It now lists the
   composite factor once and labels it explicitly. Vienna uses a different,
   pure-bus factorization: its voltage and midpoint budgets previously omitted
   their command holds/delay. The existing physical factors are now exposed and
   included. This changes diagnostic composition, not the computed open-loop
   responses. Cursor tests check all complex samples and summed gain/phase,
   including non-grid cursor frequencies.

## Actual consumer evidence

The baseline real Qt launcher replays independently checked the complete
stage → sensing/ADC/PWM → plant/controller → Bode → candidate Apply → exact H(z)
→ save → fresh-window reopen → recompute/export chain. Default LLC and TTPL
responses matched independent complex equations and bracketed root finding.
The default TTPL outer-loop PM was only about 10.85°, and its baseline AC run was
not settled; neither fact may be hidden behind a successful Bode calculation.

The first frozen timing/hold/label repair (`afdbec85`) passed 149 selected tests
and independent real Qt workflows. LLC N4/5 µs produced centres
[0.15, 5.15, 10.15, 15.15] µs and EOC 15.516667 µs, including exact saved replay.
TTPL command-hold comparisons matched independent sinc/phase and closed-inner-loop
ratios within 8e−16. These numbers identify that intermediate source only;
subsequent plant/domain corrections need their own final qualification.

Before the model correction, a deliberately marginal LLC case was displayed as
GM 6.01 dB while its same state-space model gave approximately 5.985 dB. This
counterexample is retained so a later rounded/displayed value cannot be used to
waive the original 6 dB acceptance constraint.

The final actual TTPL consumer comparisons at `efe62bd` match independent
complex equations within 3.15e−12. At a 40 kHz current sample rate the independent
inner-loop result is Fc 2452.593351 Hz / PM 52.696413° / GM 9.404844 dB, with all
samples strictly below 20 kHz. The outer loop stays below its own 5 kHz boundary.
A real 60 kHz / command-ZOH-off candidate Apply gives Fc 1500.000638 Hz,
PM 59.999966° and GM 19.282280 dB; fresh saved replay preserves both controllers
and both response arrays exactly. Twelve actual TTPL Python export files pass
standalone self-checks. New and legacy ADC-divider cases respectively pass and
fail the declared headroom check without silent migration of saved numbers.

At 1280×720, the actual LLC window has a 1071×691 minimum size hint. The new SOC
field is editable and Run/Export are reachable using the existing page scrolling.
These are Linux Qt offscreen checks, not native display-scaling qualification.

## Remaining boundaries

Saved expert sessions retain the authoritative controller, sample period,
analysis inputs and reproducible results. The original Guided wizard definition
is historical context: it is not automatically synchronized after expert edits
and is not yet persisted as an editable wizard session in a newly opened process.
Reopening the original definition must not be confused with the current expert
configuration. Hardware correlation, exact TI executable policies and native
package qualification remain separate from these model/consumer checks.

The integrated application candidate `efe62bd` has independent actual-consumer
checks: default LLC Fc 46.724015 Hz / PM 105.627682° / GM 6.012494 dB; the explicit
4SOC/5 µs case gives Fc 46.723947 Hz / PM 105.501506° / GM 4.648958 dB. New-process
saved replay is exactly identical to its own pre-save result; actual Python
research exports execute their reference/feedback identity checks successfully.
The stronger input/output normalization independently matches all 18 real MIMO
state-space paths within 8.85e−14, including physical command-unit transforms.

Independent TTPL adversarial checks retain every crossing: at outer Kp=100,
the valid-band signed GM is −9.470278 dB and Fc 4896.933169 Hz / PM −157.160478°.
At Kp=1000, there is no valid-band Fc/PM and the signed GM is −29.470278 dB.
The old 14.2057 kHz phase crossing is outside the outer 5 kHz Nyquist boundary.
The scalar GM helper's existing nonnegative-first convention is explicitly
identified; summary/GUI/research evidence now also disclose every signed GM and
its phase-crossing frequency. Negative GM describes a critical gain reduction;
it alone does not prove closed-loop instability.

Initial integrated targeted checks preserve two failed assertions out of 104:
nonfinite TTPL sampling rates are correctly rejected by the new earlier rate
validator, while those tests expected the later frame validator's exact text.
That expectation is corrected without weakening rejection or any numerical
threshold. A separate older snapshot test's shared-grid mask and provenance expectation
are updated to the explicit per-loop frequency grid and all-active-block bound.
Its first rerun retained 124 passes and one obsolete text assertion failure;
the physical arrays and snapshot invariants were not relaxed. Final integrated test results and any remaining
failures are recorded in the feature matrix, validation report and development status. A
partial or historical pass must not be relabeled as a complete current-source
qualification. Formal publication remains paused.


## Reproducible regression entry points

Run the repository's pytest suite with the GUI dependencies installed and a
working Qt platform. Focused regression modules include
`test_guided_loop_contracts.py`, `test_continuous_transfer_identity.py`,
`test_adc_frame_contract.py`, `test_guided_adc_headroom_gui.py`,
`test_guided_frequency_domain.py` and `test_phase_budget_gui.py` under
`llc_design/tests`, plus `pfc_design/tests/test_frequency_domain_contract.py`,
`pfc_design/tests/test_margin_screen.py` and
`power_control_tools/tests/test_adc_range_contract.py`.

The dedicated complete current-source regression, affected long PFC consumer,
installed-wheel and Linux frozen checks remain pending at this documentation
checkpoint. Historical qualifications listed elsewhere keep their own source
identities. Windows/macOS native package acceptance is separately tracked; no
successful local test grants hardware validation or permission to release.

The corrected combined focused run passes all 125 cases with no skip/error/failure.
CJK font warnings in bare test rendering remain recorded; formal GUI captures
initialize the application fonts. This does not replace full qualification.
