# Third-party notices

Power Design Toolkit V9 does not bundle GeckoCIRCUITS, a Java runtime, or an external switching-simulation engine.

Python dependencies retain their respective upstream licenses. The project itself is distributed under `GPL-3.0-only`; see `LICENSE`.

The magnetic/PLL/control expansion uses independently implemented equations and
benchmark scripts. Reference repositories and pinned revisions are listed in
`docs/engineering-expansion.md`. No MKF, inductor_optimizer or TI SDK source code
is bundled. The original user-supplied 2002 TDK PDF and legacy XLS are not bundled;
geometry facts and approximate digitized fits retain source hashes and locations.

The optional 3C90 residual-model parameters were trained independently from the
MagNet-derived example data in `tinix84/Fundamentals_of_AI_for_PE` revision
`8dd961c185fcffd777382faa2aefe67e23cef4ca`. Original datasets and notebooks are
not bundled. The upstream repository distinguishes Apache-2.0 code from
CC BY-NC 4.0 educational content and separately sourced datasets; this project's
code license does not override upstream dataset/content terms. The validation
report records input data hashes, independent split, scope and reproduction steps.
