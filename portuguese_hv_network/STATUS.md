# PT60 v2 status

Updated: 2026-09-04

The Portuguese >=60 kV candidate topology builds, validates and solves as a
declared public-data-informed AC power-flow benchmark.

| Item | Current result |
|---|---:|
| Voltage levels | 60, 130, 150, 220, 400 kV |
| Buses | 3,783 |
| Lines | 4,943 |
| Transformers | 228 |
| Load rows | 468 |
| Mapped generation candidates | 1,040 / 1,190 |
| Scenario-active components | 1 |
| Portugal--Spain boundary | 7 external buses / 9 circuits / 6 PT locations |
| Automated validation | 32 / 32 passed |
| Full-scale AC power flow | converged |
| Full-scale load | 10,268.8 MW |
| Voltage range | 0.9324--1.0207 pu |
| Maximum line loading | 95.59% |
| Maximum transformer loading | 77.55% |

All scaling cases converge and all mapped generator injections remain at or
below public nameplate capacity. The 1.00 full-scale case passes the declared
screen; 1.10 and 1.20 remain diagnostic stress cases.

The winter scenario preserves the REN source totals with 9,861.0 MW assigned to
mapped assets and 6.71 MW of separately labelled other-thermal and battery
residuals. Observed net
import is held out rather than used to rescale asset dispatch.

The boundary inventory is fixed to 2026-01-20. It includes the three 220 kV
Douro circuits omitted from the earlier configuration. The Ponte de
Lima--Fontefría 400 kV interconnector is marked out of service because its
commissioning milestone is after the scenario timestamp; the later
4,200/3,500 MW exchange capacities are deliberately not used.

Of 4,943 line rows, 1,517 have partial PDIRD source backing and 3,426 use
voltage-class engineering proxies. Reactive demand, individual dispatch,
remaining line parameters, transformer impedance/taps and switch states are not
complete synchronized operator observations.

PT60 is therefore an analysis-ready candidate benchmark, not an operator
state-estimation case.

The multi-snapshot evidence runner additionally maps every available E-REDES
record for the January (395), March (390), and season-matched September (394)
profiles. All three primary cases converge without mapped-generator nameplate
violations. Correcting anonymous multi-unit de-duplication restores hydro and
gas inventories to 102.1% and 100.6% of the March REN aggregates; DGEG licensed
assets raise wind and solar coverage to 90.0% and 83.5%. The former 113.0 MW
March hydro residual is zero. PDIRT PdE weights replace the six residual-load
hubs, storage consumption is included, and the boundary uses one angle
reference plus capacity-weighted equivalents. All 72 hourly samples converge;
winter and proxy-summer panels retain diagnostic overload hours. RNT-scope
losses remain below REN monthly values, and REE/ENTSO-E independent border
validation remains pending API credentials.
