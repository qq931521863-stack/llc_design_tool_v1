# Engineering Validation Policy

Power Design Toolkit distinguishes **software reproducibility**, **model correlation** and **hardware validation**. These are different evidence levels and must not be collapsed into one word such as “verified.”

## 1. Bundled LLC regression baseline

The repository-controlled `400 V -> 53 V / 3 kW` case is a reproducible software-regression baseline. Passing it confirms that the current Python implementation reproduces the recorded tank, operating-point and analysis results within maintained tolerances.

It does **not** by itself validate:

- component selection;
- magnetic loss accuracy;
- thermal performance;
- ZVS device margin;
- semiconductor stress;
- insulation/safety compliance;
- hardware release readiness.

## 2. Evidence status

Two concepts are kept separate:

### Evidence status

- **VERIFIED** — the stated claim can be reproduced from a traceable artifact within its stated scope.
- **UNKNOWN** — the required source, simulation or measurement artifact is absent or insufficient.

### Hardware verification

`verified_for_hardware` is a separate decision. A software regression or datasheet transcription may be VERIFIED for a narrow claim while still being unsuitable for hardware release.

## 3. Current evidence matrix

| Evidence | Status | Hardware verified | Meaning |
| --- | --- | ---: | --- |
| Python baseline calculation | VERIFIED | No | Repository baseline can be recomputed and checked against tolerances |
| Internal FHA/HB/TD consistency | VERIFIED within software scope | No | Model implementations can be regression-tested against repository contracts |
| Real ngspice execution path | VERIFIED for simulator plumbing | No | CI runs actual ngspice/libngspice; this does not establish device-model accuracy |
| External PLECS/LTspice correlation | UNKNOWN unless artifact supplied | No | Requires traceable external model/result package |
| Bench measurement correlation | UNKNOWN unless artifact supplied | No | Requires raw measurement evidence and operating conditions |
| Generic/reference semiconductor records | Reference only | No | Not automatically manufacturer-qualified ordering codes |
| Generic magnetic/core/material fits | Reference only | No | Need traceable source curves, ranges and fit records for release use |

The code-level validation summary remains available through the repository validation utilities; hardware-release gates must stay conservative while evidence is missing.

## 4. Required external-simulation evidence

A correlation claim should record at least:

- simulator and version;
- schematic/model revision;
- semiconductor/magnetic models and their sources;
- input/output/load operating point;
- solver settings;
- exported raw measurements/waveforms;
- comparison metric and tolerance;
- date and responsible engineer.

A screenshot without the corresponding model/settings/raw data is not sufficient for a reproducible validation claim.

## 5. Required bench evidence

Bench evidence should additionally record:

- equipment and probe type;
- calibration state;
- probe location/reference;
- input source and load conditions;
- ambient/component temperature where relevant;
- sample/board revision;
- raw oscilloscope/analyzer captures;
- processing steps used to derive the reported metric.

For FRA measurements, record injection point, measurement meaning, source instrument/export format, operating point and the exact controller active during the scan when controller de-embedding is required.

## 6. Device and magnetic data

Release-quality device/material records should contain:

- exact manufacturer ordering code;
- source document/revision;
- extraction or fitting method;
- units and reference conditions;
- temperature/frequency/flux/current range;
- tolerances or min/typ/max interpretation.

Synthetic or generic reference data are useful for software and architecture development but must remain labeled as such.

## 7. Falsification rule

A claim is no longer considered verified if:

- the corresponding regression no longer reproduces;
- its cited source cannot reproduce the recorded number;
- model correlation exceeds the approved tolerance;
- operating conditions fall outside the stated evidence range;
- a higher-fidelity model or measurement contradicts the lower-fidelity result.

The intended validation ladder is:

```text
analytical model
 -> higher-fidelity software model
 -> circuit simulation
 -> measured hardware
 -> reviewed release evidence
```

Passing an earlier stage is necessary engineering evidence, not permission to skip later stages.
