# Integrated LLC resonant magnetics

The existing **separate resonant inductor + transformer** mode remains the default.
The integrated mode represents the transformer leakage as the resonant inductance,
while retaining the actual two-winding transformer and its one magnetic-loss and
thermal budget. It is an explicitly declared linear model, not a claim that the
requested leakage has been manufactured or measured.

## Desktop workflow

1. Open the LLC workspace from the main launcher or the Power Stage Designer LLC
   topology entry. In the left-hand global inputs, scroll to **磁件结构 / 集成谐振漏感**.
2. Select **集成 Lr：变压器漏感 / 两绕组全桥 SR**. The primary bridge can be half or full;
   this model requires a two-winding full-bridge secondary, not a center tap.
3. Choose either **电感矩阵 Lp / Ls / M** or **开短路 Lp / Ls / Lshort**. Enter the actual
   physical turn counts, characterized core, characterization frequency and a
   source/condition note. Use the same transformer, winding arrangement, dot
   convention and test conditions for all entries. State when values are a
   design hypothesis rather than measurements.
4. Optionally enter a target Lr. A target is compared with the characterized model;
   it does not replace the measured/declared inductances. Enter the two P–S
   insulation spacings and the same-winding interlayer spacing.
5. Calculate. The summary shows the physical turns ratio and the distinct
   all-primary-referred (APR) electrical ratio. The transformer page evaluates the
   same locked magnetic body, including winding build and leakage feasibility.
   The ordinary independent-Lr candidate is absent in integrated mode.
6. Save the project. Reopening restores inputs and provenance; calculate again to
   obtain current results. Export the calculation book or formula PDF only from
   a current accepted calculation. Changing the source characterization, turn
   count, core or operating inputs invalidates the old result.

Two clearly labeled software examples are provided. **载入双漏感参考模型（非实测）**
illustrates the electrical transformation and an unreachable winding geometry.
**载入可达绕组参考例（标称点）** demonstrates a nominal-point geometric fit. Neither
button represents tested hardware or guarantees full-envelope feasibility.

## Long-running model comparisons

The **FHA / HB / TD** page retains its normal 7-harmonic / 1024-sample settings.
**取消计算** requests cooperative cancellation inside HB/TD numerical work. The
page remains busy until the actual worker exits, then shows a cancellation reason;
closing the workspace or changing its physical inputs also cancels the owned
comparison. No cancelled or timed-out partial result is published for export.

**本次计算总预算** is a session-only execution limit, default 300 s, adjustable from
0.1 to 3600 s. It does not change harmonic order, sampling or convergence criteria,
and is not saved as a physical project parameter. A timeout is shown explicitly;
retry with an appropriate budget or inputs. A finished comparison can still contain
a nonconverged numerical model, which remains ineligible for TD-loss publication.

## Electrical and physical definitions

All inductances below use henries; inductance-matrix currents enter the dotted
terminals. Lp is the primary open-circuit self-inductance, Ls the secondary
open-circuit self-inductance, and M the positive mutual inductance. The matrix
must be positive definite. The actual turns ratio is n_phys = Np/Ns.

The exact linear APR transformation is:

- Lr = Lp − M²/Ls
- Lm_APR = M²/Ls
- n_eff = M/Ls

Thus the primary short-circuit inductance equals APR Lr **only while transforming
Lm and the electrical ratio consistently**. It is incorrect to substitute Lshort
for Lr and leave the old Lm and actual turns ratio in the tank equations.

The physical T-equivalent quantities are:

- Lμ = n_phys M
- Lσp = Lp − Lμ
- Lσs = Ls − M/n_phys

Both physical leakage terms must be nonnegative. Tank gain, reflected load and
secondary electrical current use n_eff. Winding geometry, ampere-turn balance and
physical core flux use n_phys. With Isec defined leaving the secondary dot:

- Iμ = Ir − Isec/n_phys
- B = Lμ Iμ/(Np Ae)

The APR magnetizing state is not silently relabeled as the physical magnetizing
current. Waveforms distinguish the APR branch voltage, physical core-induced
voltage, and physical primary-terminal voltage including leakage. Gap estimation
uses Lμ. The GUI FHA and fast EDF displays combine fundamental sinusoidal states with
square-clamp/KVL voltage projections, so their displayed Vμ is not an exact
time-domain derivative of the displayed current. The actual source is labeled
FHA or EDF. FHA magnetic loss uses a separate triangular-current reconstruction
and is explicitly not attributed to those displayed waveforms. Accepted
time-domain magnetic losses use the actual mapped current/flux samples.

## Winding feasibility and accounting

The implemented geometric screen integrates H² across a P/2–S–P/2 stack,
including conductor layers, interlayer insulation and both P–S insulation regions.
The balanced-MMF field integral yields **Lσp + n_phys² Lσs**, not generally APR Lr.
It compares that quantity with the characterized matrix, solves the required
interface spacing, and checks minimum insulation and available window build.
It also reports APR bounds when the physical leakage split is not determined.

A geometry match is a consistency screen. Finite edges, shields, gap fringing,
frequency-dependent permeability and nonlinear capacitance are not synthesized.
Measured Lp/Ls/M and insulation construction still need verification. A reported
MODEL_REACHABLE result does not turn into a hardware qualification or override
current, flux, temperature, regulation or other operating-point failures.

Integrated mode has one transformer core and its primary/secondary copper loss.
There is no second Lr core, winding, candidate CSV, BOM or thermal node. A zero
external-inductor entry in an aggregate numerical ledger is an absence marker,
not a fabricated physical part. Characterized core and turns are locked during
optimization; changing them requires a new characterization rather than retaining
old inductance data on a different transformer.

## Scope and remaining external validation

This is a linear two-winding model with explicit primary and secondary leakage.
It does not imply a center-tapped or arbitrary multiwinding reduction. The
optional finite magnetizing series-resistance plant/HB branch is explicitly rejected in
integrated mode until its physical core-loss mapping is defined; it is not silently
ignored. Hardware
measurement, nonlinear magnetization, detailed 3D leakage/fringing and native
Windows/macOS frozen qualification remain separate validation activities. The
three-phase star model is explicitly rejected, and independent Lr/Lm/turns
tolerance sweeps cannot override a characterized correlated inductance matrix.
This addition is exposed through desktop/Python/CLI consumers; the existing
bounded Web/MCP API does not accept the new integrated-magnetics fields.
The model and actual consumer tests do not assert exact equivalence to TI's
unexecuted software policies.


The APR-equivalent convention follows the linear two-winding treatment in
[ST AN2450, section 5](https://www.st.com/resource/en/application_note/an2450-llc-resonant-halfbridge-converter-design-guideline-stmicroelectronics.pdf).
The product additionally keeps physical leakage, actual turns, geometric limits
and source provenance explicit; the reference is not a validation of a particular
manufactured transformer.
