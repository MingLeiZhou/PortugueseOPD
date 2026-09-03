# PT60 v2 status

Updated: 2026-09-03

The Portuguese >=60 kV candidate topology builds, validates and solves as a
declared public-data-informed AC power-flow benchmark.

| Item | Current result |
|---|---:|
| Voltage levels | 60, 130, 150, 220, 400 kV |
| Buses | 3,783 |
| Lines | 4,943 |
| Transformers | 228 |
| Load rows | 401 |
| Mapped generation candidates | 1,421 / 1,563 |
| Scenario-active components | 1 |
| Automated validation | 22 / 22 passed |
| Full-scale AC power flow | converged |
| Full-scale load | 10,268.8 MW |
| Voltage range | 0.9293--1.0117 pu |
| Maximum line loading | 94.94% |
| Maximum transformer loading | 83.44% |

The 50%, 75% and 100% scale cases pass the declared numerical voltage and
thermal screen. The 110% and 120% stress cases converge but exceed at least one
screening limit.

Of 4,943 line rows, 1,517 have partial PDIRD source backing and 3,426 use
voltage-class engineering proxies. Reactive demand, individual dispatch,
remaining line parameters, transformer impedance/taps and switch states are not
complete synchronized operator observations.

PT60 is therefore an analysis-ready candidate benchmark, not an operator
state-estimation case.
