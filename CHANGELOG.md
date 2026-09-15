# Changelog

This file keeps the **maintained product history**. Detailed debugging notes, one-off migration instructions, CI result snapshots and binary release artifacts are intentionally kept out of the source documentation tree; Git history, Pull Requests, Actions and Releases provide that archive.

## Unreleased / main after 9.2.2

### Added

- shared-ngspice circuit-simulation foundation for LLC closed-loop verification;
- backend-neutral Circuit IR and deterministic netlist generation;
- batch ngspice ASCII RAW execution/parser;
- shared `libngspice` binding with external source callbacks and control/PWM event synchronization;
- exact discrete H(z) controller execution around a continuous switching LLC circuit;
- standard `WaveformBundle` adapter for SPICE power/control traces;
- real-ngspice CI smoke workflow.

### Engineering boundary

The ngspice V1 power stage is a switching-correlation model with small physical damping. It is not yet a vendor MOSFET/Coss/Qrr/SR switching-loss model.

## 9.2.2 — 2026-09-15

### Added

- Simplified Chinese / English / Japanese / Korean desktop UI;
- localized in-application help and runtime UI strings;
- language-dependent CJK font stacks;
- i18n regression coverage for workspace UI/help strings and language persistence.

## 9.2.1 — 2026-09-15

### Added

- application-wide Help/F1 system for all four workspaces;
- implementation notes, model boundaries and known-limit sections inside the application;
- contact/support information and documentation links.

### Documentation

- formalized descriptions of FHA/HB/TD, mixed-domain digital loop analysis, sampling/timing separation, discretization, C99 DF2T/SOS, FRA de-embedding/model fitting and Auto Design.

## 9.2.0 — 2026-09-15

### Added — FRA Loop Designer

- Bode100 CSV, SIMPLIS TXT and Generic frequency/gain/phase import;
- explicit Plant TS vs Complete Loop TS semantics;
- existing-controller de-embedding and Equivalent Plant reconstruction;
- Quick Tune and New Structure controller workflows;
- target-Fc/PM Auto Design with robustness checks/fallback;
- low-order rational model identification with fit confidence;
- identified-plant × controller loop analysis;
- Bode, all-crossover PM/GM, S/T, Ms/Mt and model-derived step gating;
- exact H(z) / C99 export;
- shared controller catalogue alignment with Control Tools.

### Fixed / validated

- str-Enum GUI data handling in Complete Loop import;
- LEAD/LAG/1P1Z/Modified-PI parameter mapping;
- Type-II/III pole mapping;
- unified step authorization based on model confidence, loop status and measurement-band coverage;
- deeper FRA regression for multiple phase turns/crossovers and real Bode100-format fixtures.

## 9.1.0 — 2026-09-09

### Added

- LLC formula PDF worksheet with formulas, substituted numbers, units and model boundaries;
- versioned engineering project JSON with input and result/source metadata;
- improved SemVer update checking;
- report/project/GUI regression coverage.

## 9.0 — Digital control baseline

### Architecture

- promoted Control Tools exact H(z) to a first-class controller source;
- linked exact discrete coefficients into LLC loop analysis without PI/PID re-fit;
- consolidated sensing/ADC, FM/PWM, timing, stability and C99 coefficient semantics.

## 8.x — LLC multi-fidelity architecture

### Added

- common LLC analysis request/result contracts;
- FHA / nonlinear multi-harmonic HB / switched time-domain comparison;
- Golden/reference waveform and diagnostics structure;
- DCM/rectifier/SR timing/loss extensions;
- 2-phase and 3-phase interleaved LLC analysis;
- expanded magnetic/device loss and parasitic modelling.

## 7.x — Workspaces, PFC/Vienna and code generation

### Added

- independent LLC/PFC workspaces and later shared Control Tools workflow;
- three-phase Vienna PFC analysis;
- detailed TTPL/Vienna sensing/Bode/AC-cycle/switching views;
- PFC inductor design;
- LLC transformer design workflow;
- portable C99 `float32_t` controller generation;
- desktop UI restructuring, theme/update mechanisms and engineering waveform tools.

## Earlier history

Earlier iterations established the original LLC design workflow, Bode cursors, basic digital-control visualization and desktop packaging. Exact per-commit history remains available in Git.
