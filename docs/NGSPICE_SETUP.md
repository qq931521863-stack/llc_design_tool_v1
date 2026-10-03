# Connecting desktop SPICE verification

The desktop LLC and single-phase TTPL closed-loop pages load **shared libngspice**
into the application. They do not launch the batch executable. Installing only
`ngspice.exe` or setting `NGSPICE_EXE` does not satisfy this requirement.
The release build does not currently provision/bundle this optional native engine.
Web/API/MCP interfaces do not currently expose these desktop SPICE runners.

## Install a matching shared library

Use the same CPU architecture as the application/Python process.

- **Windows x64:** use the official [ngspice downloads](https://ngspice.sourceforge.io/download.html)
  and its 64-bit distribution containing the shared DLL. Keep its companion DLLs
  together. Set `NGSPICE_SHARED_LIB` to the actual extracted `ngspice.dll` (or
  `libngspice-0.dll`) full path before launching PowerDesignTool. Do not point it at
  the executable or assume an extraction directory is automatically discovered.
- **macOS:** the [Homebrew shared-library formula](https://formulae.brew.sh/formula/libngspice)
  installs with `brew install libngspice`. The loader checks
  `/opt/homebrew/lib/libngspice.dylib` and `/usr/local/lib/libngspice.dylib`.
  For a nonstandard prefix, set `NGSPICE_SHARED_LIB` to the real dylib location
  before launching the app. A terminal environment variable is not automatically
  inherited by an already-running app or one launched separately in Finder.
- **Ubuntu/Debian:** install `libngspice0` for the shared engine; install `ngspice`
  additionally for batch smoke tests. The repository's Ubuntu CI uses
  `sudo apt-get install -y ngspice libngspice0`.

The loader also uses the system library finder. A DLL/dylib/so existing on disk
is not sufficient: its architecture and dependent libraries must be loadable.
TTPL's engine status now reports the loader's per-candidate errors.

## Run the maintained path

1. TTPL: run Control / Sensing / Bode to produce the analyzed Exact H(z), then
   open **8. Closed-Loop Verification**. Select **Recheck engine** after making a
   library available. If you changed environment variables outside the process,
   restart the application first.
2. Keep max/output timestep at **0** for the solver's automatic defaults. Start
   with the short default transient and select **Run Shared-ngspice**.
3. Confirm the returned power/gate traces and Exact H(z) identity summary. Short
   transients do not provide full-line-cycle PF/THD validation.
4. LLC: use **Closed-Loop Verification** after obtaining the digital controller;
   this SPICE power stage currently supports **FULL_BRIDGE**, not half bridge.

The maintained circuit topologies are full-bridge LLC and single-phase TTPL,
not Vienna. These are switching-correlation models, not vendor-device sign-off
models. See [architecture and model limits](NGSPICE_CLOSED_LOOP.md).

## Source checkout diagnosis

From the checkout's Python environment:

```python
from power_sim.spice import find_ngspice_shared_library
errors = []
print(find_ngspice_shared_library(diagnostics=errors))
print("\n".join(errors))
```

Then run `python -m pytest power_sim/tests -ra`. Shared tests skip when the shared
library is absent; batch tests skip when the executable is absent. A test run
with skips is not evidence that those skipped simulator paths work.
