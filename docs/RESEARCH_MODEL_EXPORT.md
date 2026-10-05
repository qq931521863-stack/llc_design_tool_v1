# MATLAB / Python research-model export

LLC **Digital Voltage Loop** and TTPL PFC **Control / Sensing / Bode** have
`MATLAB .m` and `Python .py` buttons above the results. Run the analysis first.
PFC can export the current loop, voltage loop, or both loops in one script.

## Which result is exported?

The buttons export the **last completed analysis shown by the workbench**.
Changing an input does not silently recalculate an export. Run the analysis
again to include that change. Previewing a candidate controller does not export
that candidate; apply it and complete its analysis first. Export is disabled
while an analysis is running and before the first completed result.

Manual PI, PIF and 2P2Z controllers use their analyzed coefficients. An externally
linked LLC controller likewise retains its analyzed coefficients; export is not
restricted to automatic PI tuning. Export does not retune or rediscretize.

## Standalone research scripts

The generated file carries its model coefficients, sample times in seconds,
frequency grid in Hz, complex frequency responses and provenance. It does not
import this repository or load another data file. Keep the file under version
control, modify the exposed model coefficients for your own experiments, and
retain an unmodified export as a reproducibility baseline.

- MATLAB requires **Control System Toolbox**. The script avoids newer script
  local-function and string-array syntax for compatibility with R2014b-era
  MATLAB. Compatibility is a source-level target, not a claim of execution on
  every MATLAB release
- Python requires **NumPy, SciPy and Matplotlib**. Run with `python model.py`;
  for headless execution, use `MPLBACKEND=Agg python model.py`
- Embedded numerical checks compare rational-model responses to reference
  complex responses from the completed analysis. A deliberate model change can
  make a reference check fail; retain the original check to distinguish changes
  from export errors

Provenance parameters describe the saved operating point and analysis settings.
They are not a replacement analysis engine: editing descriptive metadata alone
does not rebuild coefficients or sampled responses.

### Work with the exported models

The scripts expose `model_info`, `responses`, `models`, `poles`, `zeros` and
`loop_models`. LLC's controller is `C_z`; PFC's are `Ci_z` and `Cv_z`. In MATLAB
these rational models are also workspace variables. `loop_models` provides the
selected loop's G/C/L/T/S, with frequency-data objects where a rational model
is unavailable.

For example, after running an unmodified LLC MATLAB export:

```matlab
C_trial = 1.1 * C_z;
bode(C_z, C_trial); grid on;
```

After loading a Python export with `runpy.run_path`, use its SciPy models:

```python
import copy
import runpy
import numpy as np
from scipy import signal

saved = runpy.run_path("llc_voltage_research.py")
trial = copy.deepcopy(saved["models"]["C_z"])
trial.num = 1.1 * trial.num
f_hz = np.geomspace(1, 1000, 200)
_, response = signal.dfreqresp(trial, w=2*np.pi*f_hz*trial.dt)
```

These experiments alter the controller only. The saved full-loop arrays and
default Bode plots still show the original completed analysis; they do not
silently become predictions for modified hardware or controllers. For the
original rational pole/zero values, use the exported `poles`/`zeros` dictionaries;
SciPy's own TF-to-ZPK normalization can discard extremely small numerator terms.

## Exact model boundaries

Continuous transfer functions use descending powers of `s`. Discrete
controller coefficients retain their difference-equation convention:
`(b0 + b1 z^-1 + ...)/(a0 + a1 z^-1 + ...)`, with the analyzed sample time.
Leading numerator zeros represent delay and must not be discarded without
preserving their effect. Continuous and discrete blocks stay separate.

The complete LLC and PFC loops include sensing, sampling, hold/delay and, for
PFC, nested multirate paths. Their completed complex frequency responses are
exported directly. They are **frequency-response data**, not silently fitted
rational transfer functions. The scripts provide Bode plots for these curves
and poles/zeros only for explicitly represented rational models. No poles or
zeros are invented from sampled loop data. Any exported discretized plant is
identified as a discrete approximation rather than the continuous plant.

An exported linear controller does not specify nonlinear saturation,
anti-windup, resets, protection, firmware timing or switching hardware behavior.
The existing small-signal model assumptions and warnings still apply. Software
frequency-response agreement is not hardware validation.
