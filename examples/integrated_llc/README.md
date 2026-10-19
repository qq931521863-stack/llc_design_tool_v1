# Integrated LLC software examples

These inputs are declared linear software fixtures, **not measurements or hardware
qualification**. Open either JSON in the LLC desktop and calculate again. The
project input panel exposes **磁件结构 / 集成谐振漏感**; both examples preserve actual
30:4 turns and explicitly separate the APR electrical ratio.

- `nominal_geometry_reference.json`: a two-sided leakage model whose P/2–S–P/2
  nominal-point winding field screen matches 7 µH balanced leakage and fits the
  17 mm reference window. This does not establish full-envelope regulation or
  thermal feasibility. Inspect all FAIL/CHECK reasons rather than treating the
  word “reachable” as a complete design PASS.
- `electrical_reference_unreachable.json`: demonstrates simultaneous Lr/Lm/n_eff
  transformation with both physical leakage terms nonzero. Its requested balanced
  leakage cannot fit the reference winding window. The rejection is intentional.

Calculation books and formula PDFs record the complete physical inductance
matrix, source note, APR quantities, required spacing/window build, and absence
of a separate Lr magnetic part. Changing core or turns requires a new declared
characterization. The one-dimensional leakage screen does not predict a unique
physical leakage split or replace measurements/FEA/insulation certification.

See [the integrated LLC workflow](../../docs/magnetics/integrated_llc_workflow.md)
for the definitions, approximation boundaries and supported consumers.
