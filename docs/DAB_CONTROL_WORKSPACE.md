# DAB forward control workspace

Development checkpoint only. Formal release, hardware qualification and native
Windows/macOS package acceptance remain separate.

## Actual entry and workflow

Open DAB TPS from the launcher, then **Voltage Loop / TPS Optimization**. The
existing DAB project Save/Open actions now save the entire control session;
legacy stage-only project files still load as stage-only inputs. The control header, parameter area and candidate page scroll on a 1280×720
viewport. The native plot canvas remains outside a scroll viewport. Result display
collapses the input header to give Bode/time plots enough height; the persistent
“Show model / sensing / timing parameters” toggle reopens all inputs.

1. Select `TPS: Pcmd [W]` or `SPS: Pcmd [W]`; set the voltage and power point.
2. Specify sample rate, integer command and measurement delays, digital low-pass,
   calibration, ADC divider/reference/bits, load, power map and time stimulus.
3. Run the physical loop. Select G/H/C/L/T/R Bode curves, drag the shared cursor,
   and inspect the gain/phase budget. G contains the capacitor-energy update and
   command delay once; H contains the measurement low-pass and delay once.
4. Generate a bounded 3×3 Fc/PM PI candidate grid. A row is a preview until Apply.
   Apply uses the exact synthesized Tustin coefficients. Imported arbitrary H(z)
   is analyzed verbatim, never converted into or refitted to PI.
5. Retain operating points and scan them with the unchanged current controller.
   Invalid branch/authority/headroom points report their own rejection reasons.
6. Save the project, reopen in a fresh process, then Run. Reopened results and
   candidate evidence are historical; current exports require recomputation.
7. The actual export buttons write JSON, a standalone HTML report, Python or
   MATLAB research scripts (choose `.py` or `.m`), and a C99 controller header.

## Physical and timing contract

The controller produces incremental `Pcmd [W]` about the nominal command. The
forward actuator maps `p=Pcmd/Pbase(Vout)` into TPS's existing piecewise phases,
or into SPS `D=(1-sqrt(1-p))/2` with phases `(0,D,D)`. Here `D=phi/pi` and ideal-L
SPS power is `Va*(Vout/n)/(2*fsw*L)*D*(1-D)`. Reverse power remains unsupported.
Zero command and maximum-power/clipped points are not two-sided operating points.
The SPS `D=0.5` maximum has zero raw-phase control authority.

Periodic power is the existing event-exact ideal-L or periodic RLC calculation.
The capacitor update is `E[k+1]=E[k]+Ts*(Pout-Pload)`. The linear plant is the
Jacobian of that same update, not a substituted ZOH continuous approximation.
The command is held for each energy-update interval; no second hold is appended.

TPS mode changes do not by themselves invalidate a derivative. The derivative
check evaluates left/right slopes at h, h/2 and h/4, compares extrapolated limits
and convergence with absolute `1e-6` plus natural-scale relative `2e-5` tolerance.
Smooth ideal-L mode boundaries pass. Known periodic-RLC boundaries at
300 V / 2343.75 W and 500 V / 3333.333333 W fail for a non-unique Jacobian. Such a
point cannot generate a Bode result or an applicable controller candidate.

Sensing is an incremental engineering-voltage model about a nominal trim:
`Vmeas=V0+H(z)*(Vout-V0)`. The optional exponential digital low-pass uses
`alpha=exp(-2*pi*fc/Fs)` and `(1-alpha)/(1-alpha*z^-1)`, followed by integer
measurement delay and calibration gain. Exactly the same recurrence runs in the
nonlinear simulation. ADC divider/reference/bits check nominal headroom and
report engineering LSB; quantization/clipping, analog sensor dynamics, acquisition
aperture/SOC sequencing, fractional EOC/PWM timing and switching aliasing are not
implemented. This is deliberately not claimed to match detailed LLC/TTPL ADC
aperture functionality.

All plotted samples are strictly below controller Nyquist. Requested and actual
frequency ranges are both stored. T is measured-output complementary sensitivity
`L/(1+L)`; R is physical-output/reference `G*C/(1+L)`. Closed-loop roots come from
the full declared discrete G*C*H characteristic polynomial, including delays and
measurement states. They do not establish switching or hardware stability.

## Candidate and source ownership

The positive-gain Tustin PI synthesis reuses the shared exact target solver.
A model-screen pass requires stable declared discrete roots, observed crossover
and PM near target, every signed GM at least 6 dB, and sampled peak sensitivity at
most 2. Missing GM remains unknown and blocks Apply. Switching-frequency
attenuation above Nyquist is not extrapolated or silently accepted as evidence.
This is a bounded PI grid, not arbitrary controller-family synthesis or global
robust optimization. Workpoint scans are retained per-point evidence, not a claim
that the nominal candidate passes every workpoint.

Stage, sensing, timing, actuator, operating point and exact controller identity
all participate in source ownership. Same-Fs external-controller imports still
invalidate current results and candidates. Old plots may remain visible with a
historical label; current export and Apply are disabled. Saving preserves active
settings/exact coefficients, retained workpoints, visible curves and historical
result/scan/candidate evidence. Full session parsing, range and precision checks
finish before any widget changes. Missing fields, mismatched sample periods and
unrepresentable desktop values are rejected without partial restoration.

Run/scan/optimization jobs support cooperative cancellation. Late cancelled
results are discarded. Close requests cancel a control job and keep the window
alive until its worker has finished; stage-sweep jobs retain their earlier close
policy.

## Evidence and remaining qualification

`dab_design/tests/test_control_parity.py` compares the sensing/time implementation
with independently assembled discrete recurrences, verifies branch boundaries,
SPS values, candidate identity and unknown-margin rejection. The actual Qt tests
in `test_workspace_parity_gui.py` click candidate Apply and Run, save using the
main-window action, restore in a separate Python process, and compare every
complex G/H/C/L/T/R sample exactly. They execute the exported Python research
script and compile/run the actual C99 header against impulse/step references.
C99 is float32 SOS controller delivery only, with no MCU pin mapping, ADC chain,
TPS/SPS modulator, application saturation/anti-windup or protection integration.
Host verification is not TI execution or hardware acceptance. MATLAB source
export is supported through the shared renderer; MATLAB execution is not part of
this focused checkpoint. HTML is a readable frozen report, not a PDF report.

Native scaling, measured/FRA correlation, analog ADC aperture parity, reverse
power, Cp transient-state loops and nonlinear hardware startup/protection remain
outside this checkpoint. Independent integration audit and complete current-
source qualification are still required.

### Shown-window paint lifecycle correction

Checkpoint `8b1fa09` passed the initial focused tests but failed two subsequent
shown-window capture runs with native SIGSEGV while pumping events after result
display. That red evidence remains valid for that source. The same failure occurred
with synchronous computation (no worker), with the worker retained/no signal
object deletion, with the cursor removed, and even with an empty canvas. Keeping
the summary tab selected or removing the outer scroll viewport avoided it.
This isolated the newly introduced visible-canvas-under-scroll containment path;
it did not establish an upstream Qt defect or a hardware-specific cause.

The correction keeps the plot tab outside any scroll viewport and scrolls only
the header/parameters and the candidate page. Three separate-process runs of the
formerly failing normal shown-window path then completed event pumping and close
with exit 0. A dedicated subprocess regression now shows the actual window,
runs analysis, repeatedly changes Summary/Bode tabs, pumps repaint events, grabs
the window and closes with a queued draw. This is a repeatable local correction,
not a replacement for independent/native display qualification.
