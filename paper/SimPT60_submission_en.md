# A traceable time-series power-flow dataset for Portugal’s high-voltage grid

# Abstract

Publicly accessible power-system data are commonly fragmented across network structure, equipment attributes, and operating records, which limits their direct use in continuous power-flow analysis and method comparison. We present SimPT60, a research dataset reconstructed from multiple public sources for the continental Portuguese 60--400 kV grid and the Portugal--Spain interconnection boundary. SimPT60 organizes the static network, operating time series, and alternating-current (AC) power-flow results in a unified data framework. It provides continuous 15-minute operating cases, source-level provenance, and validation products for assessing model consistency. The dataset supports time-series power-flow studies, scenario comparison, relative-risk screening, and testing of grid-analysis algorithms. Its research utility is demonstrated using E-REDES public datasets that were withheld from nodal-power construction, parameter-sensitivity experiments, and single-element (N−1) contingency screening. SimPT60 is a public-data-derived research model and has not been validated as an operator network model at equipment level.

**Keywords:** power-system dataset; Portugal; public data; grid reconstruction; power flow; time series; provenance; cross-source validation


# Background & Summary

Power-flow simulation underpins many studies of grid operation, planning, and risk, and requires data that describe network topology, equipment attributes, and operating conditions [@Thurner2018]. As research moves from individual snapshots to time-series analysis, a static network must also be linked to continuous load, generation, and other operating records [@Horsch2018; @Meinecke2020].

Several public resources already provide reusable grid models and benchmark datasets. PyPSA-Eur supplies a transmission-network model for European energy-system optimization and operational studies [@Horsch2018]. Recent open-data reconstruction work has expanded the geographic representation of the European high-voltage grid [@Xiong2025]. SimBench provides representative networks across several voltage levels together with annual load and generation profiles [@Meinecke2020]. These resources support system optimization, network reconstruction, and method comparison, respectively, and provide an important basis for open power-system research.

## Motivation

Portugal's National Energy and Climate Plan identifies renewable generation, storage, electrification, and cross-border interconnection as central elements of the energy transition [@DGEGPNEC2030Revision2024]. These developments alter the scale and spatial distribution of generation and demand and therefore affect line flows, bus voltages, and system risk. Studying the time-varying behaviour of the Portuguese grid consequently requires a data foundation that represents both network structure and continuous operating conditions.

The relevant public records are distributed across E-REDES, Redes Energéticas Nacionais (REN), the Direção-Geral de Energia e Geologia (DGEG), the Entidade Reguladora dos Serviços Energéticos (ERSE), and open geospatial platforms [@EREDESRND2026; @RENDataHub2026; @DGEGGeo2026; @ERSEPDIRT2024; @OSM2026]. Their spatial coverage, temporal resolution, identifiers, and field definitions differ. Using them in a common power-flow model requires topology reconstruction, completion of electrical parameters, asset-to-bus mapping, temporal alignment, and allocation of aggregate power to nodes.

Existing resources also have different modelling objectives and scopes. European-scale models primarily address cross-regional system studies [@Horsch2018]; open high-voltage reconstructions focus mainly on AC networks at 220 kV and above [@Xiong2025]; and representative benchmarks do not describe the geographic Portuguese grid [@Meinecke2020]. A public research resource that combines the continental Portuguese 60--400 kV network, 15-minute operating time series, solvable power-flow cases, and explicit provenance has therefore remained unavailable.

This study develops a traceable, computationally usable Portuguese high-voltage grid dataset with continuous time-indexed cases for power-flow analysis, grid-risk screening, scenario comparison, and method testing.

## Dataset scope and contribution

SimPT60 reconstructs the continental Portuguese high-voltage grid from multiple public sources. It covers the 60, 130, 150, 220, and 400 kV networks and retains the boundary nodes required to represent Portugal--Spain interconnections. Raw sources, the static network, time-series inputs, power-flow results, and validation records are stored in linked layers so that users can trace key fields to their source and processing rule.

The main contribution is a versioned data product that connects a traceable public-data reconstruction of the continental Portuguese 60--400 kV grid with 31,492 consecutive, reproducible 15-minute AC power-flow states. The geographic scope includes continental Portugal and the Portugal--Spain boundary but excludes the islands and distribution networks below 60 kV. Linked database layers preserve the raw records, static network, operating inputs, solved states, provenance, and validation results. The computed states are research-model outputs rather than equipment telemetry, and their appropriate interpretation is defined in the Limitations and Usage Notes sections.

The following sections describe construction of the resource, the released records, and the checks used to establish technical quality. Reuse guidance and interpretation boundaries are provided in Usage Notes.


# Methods

SimPT60 was constructed in five stages: archiving and standardizing public records; reconstructing the 60--400 kV static network; mapping assets and operating records to a common 15-minute calendar; generating and solving one AC power-flow case per interval; and storing the results in linked layers with explicit quality controls.

## Public data scope and standardization

### Scope and sources

The dataset covers the 60, 130, 150, 220, and 400 kV networks from 1 May 2025 to 24 March 2026 at 15-minute resolution. The E-REDES portal calls the national distribution grid the *Rede Nacional de Distribuição* (RND) and labels high voltage as *alta tensão* (AT). Network geometry is drawn mainly from the E-REDES 60/130 kV network and the OpenStreetMap (OSM)/Geofabrik 150/220/400 kV network [@GeofabrikPortugal2026]. DGEG and Agência Portuguesa do Ambiente (APA) records supplement generation, storage, and connection evidence [@DGEGGeo2026; @APA3403; @APAPPA421; @APAPPA407]. REN provides national 15-minute operating series, whereas E-REDES provides substation energy. The *Plano de Desenvolvimento e Investimento da Rede de Transporte* (PDIRT) and *Plano de Desenvolvimento e Investimento da Rede Nacional de Distribuição* (PDIRD) provide delivery-point profiles and selected equipment parameters [@EREDESRND2026; @OSM2026; @RENDataHub2026; @ERSEPDIRT2024]. The European Commission's Geographical Information System (GISCO) supplies the continental boundary [@GISCO2024]; REN and Red Eléctrica de España (REE) records support transmission and interconnection checks [@REE2012]. OpenInfraMap, which derives from OSM, is used only for display checks and is not treated as independent evidence [@OpenInfraMap2026].

Table 1 combines each source's coverage, modelling role, and temporal treatment. The common study window is determined by synchronized E-REDES and REN series. Static-source versions, case dates, and historical availability are recorded separately; the common window is not interpreted as the observation date of every device.

\Needspace{8\baselineskip}

**Table 1—Public sources, modelling roles, and temporal treatment.**

| Source | Data | Role | Time basis |
| --- | --- | --- | --- |
| E-REDES RND (AT) | 60/130 kV grid | Network | Snapshot |
| OSM / Geofabrik | 150/220/400 kV grid | Network, assets | Snapshot |
| DGEG / APA / projects | Assets, capacity, dates | Assets, evidence | Asset date |
| REN Data Hub | 15 series; 15 min | Dispatch, reference | Common window |
| E-REDES station energy | 397 stations; kWh | Direct load | Common window |
| PDIRT / PDIRD | Profiles, circuits, ratings | Weights, parameters | Planning |
| REN / REE | Length, capacity, interties | Check, calibration | Inventory |
| GISCO | National boundary | Cartography | Snapshot |
| E-REDES auxiliary | 14 products | Validation | Withheld |
| Minho--Galicia | Notice: 2 July 2026 | Inventory | Post-window; off |

**Table note.** The common window is 1 May 2025 to 24 March 2026. Proxy denotes an engineering assumption. For Estoi, voltage and capacity are source-backed; equal parameters and joint availability are Proxy [@REN2015Estoi].

### Archiving and standardization

Raw files are archived immutably by source together with the source Uniform Resource Locator (URL), download time, file size, and SHA-256 hash; processing reads the raw layer without modifying it. Coordinates are stored in World Geodetic System 1984 (WGS 84) and transformed to the Portuguese national projection for distance calculations. Voltage, power, energy, and distance are standardized to kV, MW/Mvar, kWh, and km. E-REDES 15-minute energy is converted to interval-average power according to Equation (1):

\[
P_{\mathrm{MW}}=\frac{E_{\mathrm{kWh}}}{250}. \tag{1}\label{eq:energy-to-power}
\]

Both Europe/Lisbon local time and Coordinated Universal Time (UTC) are retained. Stable identifiers are derived from source equipment codes, and every derived object retains source keys and processing status. Table 3 summarizes the principal transformations; the released data dictionary maps each source field to its standardized meaning, transformation, join key, and missing-data rule.

## Static network reconstruction

The static network is represented by Equation (2):

\[
G=(B,L,T), \tag{2}\label{eq:static-network}
\]

where \(B\), \(L\), and \(T\) denote buses, lines, and transformers. Connections are established in the order source relationship, voltage consistency, and constrained spatial matching. This hierarchy prevents geographic proximity or line crossings from being interpreted directly as electrical connectivity.

### Buses and lines

E-REDES is preferred for 60/130 kV lines, whereas OSM supplies most 150/220/400 kV lines and facilities; the latter are checked against REN length statistics. Duplicate E-REDES facilities, mobile facilities within 250 m of permanent stations, and colocated same-name stations are normalized while retaining a merge ledger.

Line endpoints are grouped by voltage and clustered within 75 m in EPSG:3763 coordinates. Different voltage levels are never merged. Each endpoint cluster is assigned to a same-voltage facility within 250 m; otherwise, a derived connection bus is created. OSM ways are split only at way endpoints or explicit shared nodes, so geometric crossings do not create connections. The OSM `circuit` and `line_section` relations restore circuit identity. Parallel circuits are retained, while self-loops and non-positive-length branches are blocked and recorded.

### Transformers and parameters

Transformers are created from three evidence classes. Explicit OSM transformers are matched to high- and low-voltage buses within 1 km. Adjacent voltage levels in the same OSM substation produce colocated transformers. RARI (Regulamento do Acesso às Redes e às Interligações do Setor Elétrico) boundary names are matched to facilities within 5 km, with a fallback to a 60 kV endpoint within 1 km of the high-voltage station. Public `rating` values take precedence; remaining capacities and impedances use voltage-pair proxies. A voltage-pair capacity is scaled upward when its aggregate falls below the corresponding REN total, and the calibration factor is retained [@RENDataHub2026].

Line parameters comprise \(r\), \(x\), \(c\), and \(I_{\max}\). Each 60 kV branch is matched to a public PDIRD circuit inventory by a length-weighted shortest path, accepted only when the model-to-source length ratio lies between 0.65 and 1.60 [@EREDESPDIRD2020AnnexB]. Remaining lines use voltage-class defaults and cable correction factors. Each parameter is labelled as directly published, derived from public documentation, aggregate-calibrated, or an engineering proxy. Some resistance support is transferred from conductor parameters reported in project documentation [@Tocha2019]; this transfer remains an engineering assumption rather than a direct line measurement.

Post-construction checks cover equipment keys, endpoints, positive length, voltage consistency, connected components, and the Portugal--Spain boundary inventory. Lengths at 150, 220, and 400 kV are compared with REN statistics. Parameter completeness means that the model is computationally usable; it does not mean that all parameters are operator measurements.

Table 2 consolidates these connection decisions, including same-voltage endpoint clustering, the distinction between geometric crossings and shared nodes, preservation of series and parallel-circuit identity, and cross-voltage links supported by explicit equipment, colocated facilities, or RARI boundary evidence.

**Table 2—Topology rules and thresholds.**

| Object | Criterion | Outcome |
| --- | --- | --- |
| Coordinates | EPSG:3763 | WGS 84 retained |
| Endpoints | Same kV; \(\leq 75\) m | Derived cluster |
| Facilities | Same kV; \(\leq 250\) m | Direct / Derived |
| Normalization | Same name; mobile \(\leq 250\) m | Derived; logged |
| OSM split | Endpoint / shared node | Crossing ignored |
| Circuit | Relation; parallel retained | Physical-circuit N−1 |
| Explicit transformer | Both buses \(\leq 1\) km | Direct |
| Colocated transformer | Adjacent kV; same station | Derived |
| RARI boundary | Name \(\leq 5\) km | Endpoint \(\leq 1\) km |
| PDIRD path | Shortest; ratio 0.65--1.60 | Partial / Proxy |
| Invalid branch | Loop; length \(\leq 0\); conflict | Blocked |

**Table note.** Direct = published link; Derived = deterministic reconstruction; Proxy = engineering substitution; Blocked = excluded. Distances are planar.

### Evidence applicability and historical availability

Static-source dates and operating-case dates are versioned separately, as summarized in Table 1. Commissioning dates are known for 366 assets and absent for 824. Only known dates switch availability by case; undated assets remain available and flagged. Ordinary lines, transformers, and interconnections retain the frozen static state when contemporaneous status records are unavailable. Project documents support only the stated equipment and fields and are not used to infer historical switching states. Cross-section scaling of conductor resistance and transfer to other conductor families remain engineering assumptions. Document pages, equipment identifiers, and field-level judgements are stored in `citation_evidence_ledger.csv`.

## Asset mapping and time-series alignment

### Asset mapping and calendar

The generation and storage inventory combines OSM, DGEG, and project evidence. Plant and generator representations within 3 km and with consistent capacity are merged. DGEG wind records replace incomplete OSM wind capacities, and non-duplicate DGEG photovoltaic (PV) records supplement OSM. Mapping priority is public connection evidence, declared voltage, and then constrained spatial proximity. An asset without declared voltage and with capacity of at least 100 MW searches only buses at 150 kV and above; smaller assets search buses at 60 kV and above. The maximum distance is 20 km. Assets beyond this distance remain in the inventory but are not injected. Assets commissioned after a case date are unavailable; missing dates are treated as available and explicitly flagged.

The common calendar uses `interval_start_utc` as its logical key. REN timestamps denote interval starts, whereas E-REDES timestamps denote interval ends, as formalized in Equation (3):

\[
t_{\mathrm{E\text{-}REDES}}=t_{\mathrm{REN}}+15\ \mathrm{min}. \tag{3}\label{eq:time-alignment}
\]

Calendar days follow Europe/Lisbon civil time, giving 92 and 100 intervals on the spring and autumn daylight-saving transitions. REN intra-day sequence numbers distinguish the repeated hour. E-REDES has no fold marker, so duplicate station timestamps are averaged and flagged as ambiguous. Missing intervals are not interpolated.

### Load, generation, and storage

E-REDES energy is mapped to buses by facility code and converted to nodal load. Equation (4) defines the unobserved national residual:

\[
R_t=P^{\mathrm{REN}}_{\mathrm{consumption},t}-\sum_j P^{\mathrm{EREDES}}_{j,t}. \tag{4}\label{eq:national-residual}
\]

Equation (5) assigns the residual to mapped delivery points using the PDIRT public reference profile from the same season and closest national-load condition [@ERSEPDIRT2024]:

\[
w_{j,t}=\frac{P^{\mathrm{PDIRT}}_{j,r(t)}}{\sum_{k\in\mathcal{M}}P^{\mathrm{PDIRT}}_{k,r(t)}},\qquad
P^{\mathrm{res}}_{j,t}=w_{j,t}R_t. \tag{5}\label{eq:residual-allocation}
\]

The profile provides only spatial weights and does not change the 15-minute resolution. E-REDES load uses a 0.97 power factor where reactive power is not observed; residual load retains the PDIRT \(Q/P\) relationship. Pumping and battery charging are distributed by mapped storage capacity, with any remainder assigned to an explicit national proxy. Nodal electric load must sum to REN `Consumption + Storage` within 0.11 MW.

REN generation by technology is distributed among mapped and commissioned assets of the same technology under \(0\le P_{g,t}\le P_g^{\mathrm{nameplate}}\). Hydro and natural gas use deterministic capacity-priority allocation, while other technologies use available-capacity shares. Generation beyond mapped capacity is injected at a designated 400 kV proxy bus as `UNMAPPED_NATIONAL_RESIDUAL_PROXY`; it must not be interpreted as the location of an omitted plant.

REN imports and exports are aligned to the common calendar but withheld from boundary-power enforcement. Before solution, the pipeline checks timestamps, required series, mapping completeness, load and generation conservation, and capacity limits. Table 3 summarizes the executable mapping and conservation rules. The associated source, bus-assignment, availability, observed-load, residual-load, reactive-power, boundary, and conservation fields are retained in the equipment tables and each case-level audit bundle.

**Table 3—Asset mapping and time-series transformation rules.**

| Process | Key rule | Status |
| --- | --- | --- |
| Asset deduplication | \(\leq 3\) km; capacity match | Derived; logged |
| Bus mapping | Evidence → voltage → nearest; \(\leq 20\) km | Direct / Derived |
| Missing voltage | \(\geq 100\) MW → \(\geq 150\) kV; else \(\geq 60\) kV | Derived |
| Commissioning | Date after case: off; missing: on | Known / Unknown |
| Time labels | E-REDES end −15 min | UTC start |
| DST / missing | 92 / 96 / 100 intervals | Mean duplicate; no fill |
| Energy | \(P_{\mathrm{MW}}=E_{\mathrm{kWh}}/250\) | Derived |
| Load | Observed + PDIRT residual | Direct / Proxy |
| Storage load | Capacity share | Derived |
| Generation | Technology, date, nameplate | Derived |
| Unmapped generation | 400 kV proxy bus | Proxy |
| Cross-border | Post-solution comparison | Withheld |

**Table note.** `timestamp_utc` is the stored field; `interval_start_utc` denotes its meaning. Proxy locations are non-physical.

## Time-indexed case generation and alternating-current power flow

Each 15-minute interval defines the steady-state case in Equation (6):

\[
C_t=(G,\theta,X_t,S_t,U_t,B_t), \tag{6}\label{eq:case-definition}
\]

where the static network \(G\) and parameters \(\theta\) are shared, while operating inputs \(X_t\), device status \(S_t\), controls \(U_t\), and boundary equivalent \(B_t\) vary with time. The principal dataset contains one case per interval, while sensitivity and contingency records use separately selected representative times and are stored in distinct scenario tables.

### Case assembly and solution

Each case updates load, generation, equipment availability, seasonal ratings, and boundary equivalents. An initial boundary solution determines model net exchange. One angular reference is then retained, and the other boundary nodes receive fixed active-power equivalents distributed by public voltage and circuit count. Contemporaneous REN exchange is used only for post-solution comparison.

AC power flow uses the pandapower Newton--Raphson (NR) implementation with direct-current (DC) initialization [@Thurner2018]. The principal workflow enforces reactive-power limits, resolves once after boundary reconstruction, and performs a bounded tap search. Generators outside the PV rule remain constant active and reactive power (PQ). Table 4 lists seasonal ratings, PV/PQ rules, compensation, reactors, boundary weights, and numerical settings. These common settings are computational proxies rather than operator control policies.

### Monthly execution

Cases are processed in parallel by civil month. Workers read inputs and solve cases independently, whereas the parent process writes summaries, state arrays, and audit bundles in one transaction. Re-runs skip complete cases and retry failed cases. A month is complete only when expected cases, completed cases, state arrays, and audit bundles agree.

**Table 4—Case and solver settings.**

| Item | Setting | Status |
| --- | --- | --- |
| Rating | May--Sep: 1.00; Mar--Apr, Oct--Nov: 1.10; Dec--Feb: 1.15 | Proxy |
| PV eligibility | Non-battery; \(\geq 20\) MW; \(\geq 60\) kV | Proxy |
| PV / Q | 1.0 p.u.; ±0.50 Mvar/MW | Proxy |
| Compensation | Power factor 0.98; \(\leq 15\) Mvar/bus | Proxy |
| Reactors | 5 × 150 Mvar; 400 kV | Proxy |
| Boundary | 1 angle reference; voltage × circuits | Derived |
| Solver | NR/DC; Q limits; 50 iterations; \(10^{-6}\) MVA | Solver |
| Taps | High side; ±8 × 1.25%; 16 rounds; 0.985--1.015 p.u. | Proxy |
| Screening | 0.90--1.10 p.u.; 100% loading | Screen |

**Table note.** Multipliers are relative to the summer rating. Settings are frozen in `model_config.json`.

## Layered storage, provenance, and quality control

### Database and provenance

The main DuckDB stores sources in `raw_eredes`, `raw_dgeg`, `raw_osm`, `raw_reference`, `raw_documents`, and `raw_eredes_aux`. The `grid` and `geo` schemas store the static network and geometry, `scenario` stores asset operating points, `main` stores the common calendar and operating series, and `provenance` stores file- and entity-level lineage.

Each month has a separate result database. `monthly_model.cases` stores one row per timestamp with status, summary metrics, and a complete JavaScript Object Notation (JSON) result. `bus_order`, `line_order`, and `state_arrays` store bus voltage and line-loading arrays in fixed equipment order. `audit_bundles` preserve load, generation, boundary, hotspot, and loss records. Views expand arrays to equipment-long tables. The static model is copied once per monthly database rather than repeated for every case.

Source records include URL, archive path, file size, and SHA-256. `table_lineage`, `raw_record_locator`, and `entity_evidence` provide table-, record-, and evidence-level traceability. Each monthly `run_manifest` records input fingerprints and case counts. The released data dictionary provides the complete data-layer, grain, field-type, constraint, and join-key inventory.

### Quality control and recovery

Quality control has four levels. Static checks cover keys, endpoints, voltage, length, connectivity, and the boundary inventory. Time-series checks cover temporal alignment, source completeness, load and generation conservation, and capacity limits. Solver checks cover convergence, voltage, loading, and the active-power-balance residual in Equation (7):

\[
\varepsilon_t=P_t^{\mathrm{gen}}+P_t^{\mathrm{model,net}}-P_t^{\mathrm{load}}-P_t^{\mathrm{loss}}. \tag{7}\label{eq:power-balance}
\]

External checks use E-REDES national, municipal, substation, production, and injection statistics that were excluded from nodal construction, together with REN exchange and monthly loss reports. The 0.90--1.10 p.u. voltage band and 100% loading threshold are screening criteria; violating cases remain in the release.

A case summary, state arrays, and audit bundle are committed in one transaction. Any failure rolls back the full case and retains an error record. Before formal monthly release, expected intervals, completed cases, state arrays, and audit bundles must have matching counts; a failed check prevents the month from being marked complete. Thresholds, failure states, and record locations are preserved in the released configuration, case tables, audit bundles, and run manifests.


# Data Records

SimPT60 comprises one main database and 11 monthly power-flow result databases. The main database contains the static network, common time series, source records, and validation observations. Monthly databases contain the 15-minute cases and their solved states. The frozen release distinguishes the CORE-3783 and N1-3787 model variants and records their permitted uses.

## Static network data

The static network contains 675 facilities, 3,783 buses, 4,943 lines, 228 transformers, 1,190 generation or storage units, 468 load points, and seven Portugal--Spain boundary endpoints. `grid.buses`, `grid.lines`, and `grid.transformers` provide connectivity and electrical parameters; `grid.generators`, `grid.load_points`, and `grid.interconnectors` link assets to buses. Line and facility geometry is stored separately in `geo.line_geometries` and `geo.facility_geometries` to avoid duplicating spatial objects in electrical tables.

Equipment tables retain `source`, `source_status`, `parameter_status`, and mapping-rule fields. Users can build the complete pandapower network, select only devices above an evidence threshold, or identify components whose parameters were derived from public records or engineering proxies.

Figure 1 shows the geographic network and one archived operating state. Projection and cartography use GeoPandas [@GeoPandas2026].

![Figure 1](figures_final/fig01_geographic_network_state.png)

**Figure 1—Geographic network and solved state at the annual peak.** (a) The 60--400 kV network containing 3,783 buses and 4,943 lines; (b) line loading; and (c) bus voltage. All panels use EPSG:3763 with the same scale and extent, retain original line geometry and Portugal--Spain boundary endpoints, and show a 100 km scale bar. The operating state is the archived annual-peak model at 2026-01-15 12:15 UTC (load 11,329.2 MW), joined by stable equipment identifiers. Missing solved states remain grey and are not imputed. The loading scale retains values above 100%. Values are simulated power flows rather than measurements.

## Time-series data

`main.interval_calendar` is the common index for all time series. It covers 328 civil days from 2025-05-01 to 2026-03-24 and contains 31,492 local 15-minute intervals. Interval counts follow Europe/Lisbon daylight-saving transitions and therefore need not equal the number of days multiplied by 96.

`main.eredes_load` contains 12,360,502 load records for 397 substations. Raw records extend to 2026-03-25, but the joint modelling window ends on 2026-03-24. `main.ren_dispatch` contains 472,380 records across 15 national operating series for load, generation by technology, storage, and cross-border exchange. `main.weather_hourly` holds 23,616 hourly records for Lisbon, Porto, and Faro; `main.ren_rnt_balance` contains 660 monthly balance records over 11 reporting periods.

The database also archives 14 E-REDES auxiliary datasets with 2,239,064 records. They cover national and municipal consumption, contracted power, production, distribution-grid injection, distributed self-consumption, seasonal substation loading, power quality, and continuity of supply. These tables are excluded from nodal-power construction and serve as withheld public observations in Technical Validation.

## Time-indexed power-flow cases

Interval results are partitioned by civil month to limit file size and support selective download. Eleven compact monthly databases map one-to-one to the common calendar and contain 31,492 cases. Every case in the present release completed AC power flow and is marked as converged. October 2025 contains 2,980 intervals because daylight saving time ends, whereas March 2026 covers only the first 24 days and contains 2,304 intervals.

Each case retains summary values, equipment-state arrays, and an allocation audit bundle. Fixed `bus_order` and `line_order` tables expand state arrays to equipment-long form; the static device order is stored once per month. The released data dictionary lists all fields and joins.

The main database also contains a sampled N−1 screening panel at six preselected operating times: maximum and minimum load, maximum wind, maximum photovoltaic generation, maximum net import, and maximum net export. The same 1,649 outage groups are used at every time, comprising 1,421 line-circuit groups and 228 transformer groups, for 9,894 contingency-level records. Line eligibility excludes 156 out-of-service rows, two station busbars, and 14 rows without a finite base-case result; the latter category is distinct from zero current and from out-of-service status. Dedicated scenario tables store membership, post-contingency overloads, islands, solution status, control-search bounds, and run provenance. These sampled records remain separate from the continuous 15-minute cases.

## File organization and distribution

The frozen release comprises the analysis-ready main database, 11 monthly result databases, source and provenance records, validation products, and scenario records. Stable model identifiers join the static network to the common calendar in the main database. Case identifiers and fixed equipment order join summaries, state arrays, and audit bundles in the monthly databases. Raw files are distributed when licensing permits; otherwise, the release provides source metadata and download scripts. `release.json`, `SHA256SUMS`, and the release README define file hashes, the environment, and reproduction entry points.

## Frozen release and model-version reconciliation

The paper freezes release **SimPT60-2026.09.21-r1**. The 31,492 historical cases and the main technical validation use CORE-3783: 3,783 buses, 4,943 lines, and 228 transformer rows. The sampled contingency panel uses N1-3787. That variant adds four colocated connection nodes to correct the Lanheses--Feitosa topology while retaining 4,943 line rows and 228 transformer rows. The single Estoi transformer row represents three parallel 126 MVA units rather than one equivalent unit; consequently, N1-3787 contains 230 equivalent transformer units but still 228 transformer rows and 228 transformer outage groups. An Estoi unit outage is represented by decreasing the row's parallel count from three to two. Results from the two variants cannot be interchanged and attributed to one model. The release manifest and `model_field_diff.csv` record the field-level differences, hashes, environment, and replay commands.

# Data Overview

Figure 2 shows interval availability, the peak-load week, and monthly case completeness.

![Figure 2](figures_final/fig02_temporal_coverage.png)

**Figure 2—Temporal coverage and case completeness.** (a) Daily fractions of available model cases, paired national-consumption observations, and paired wind observations. Denominators follow Lisbon civil days and daylight saving time; missing data are shown without interpolation. (b) The complete civil week containing maximum system load, with total electric load, generation, and REN net import. (c) Completed and converged 15-minute cases by month, totaling 31,492. The common window is 2025-05-01 to 2026-03-24; October contains the repeated daylight-saving hour, and March is partial through day 24.

Table 5 summarizes the principal static, time-series, validation, and application objects.

**Table 5—Principal dataset statistics.**

| Group | Object | Count |
| --- | --- | ---: |
| Static | Facilities | 675 |
| Static | Buses | 3,783 |
| Static | Lines | 4,943 |
| Static | Transformers | 228 |
| Static | Generation / storage | 1,190 |
| Static | Loads | 468 |
| Static | Border endpoints | 7 |
| Active | Buses / lines | 3,664 / 4,787 |
| Series | Days / intervals | 328 / 31,492 |
| Series | Station energy | 397 / 12,360,502 |
| Series | REN series | 15 / 472,380 |
| Context | Weather | 3 / 23,616 |
| Context | Monthly balance | 11 / 660 |
| Validation | Auxiliary products | 14 / 2,239,064 |
| Cases | Monthly files / cases | 11 / 31,492 |
| N−1 | Times / cases | 6 / 9,894 |

**Table note.** CORE-3783 counts apply except to the N−1 row, which uses N1-3787 and 1,649 outage groups per time.


# Technical Validation

No contemporaneous operator network model or branch-level state estimate was available at the spatial extent of SimPT60. Validation therefore does not target equipment-level error. It evaluates research plausibility and usability through network structure, computational consistency, sensitivity to modelling assumptions, and comparison with public observations.

## Validation design and evidence levels

Validation has three layers. Public structural records and sensitivity tests evaluate the network representation. E-REDES products withheld from nodal-power construction evaluate aggregate temporal and spatial behaviour. Case completeness, convergence, and power balance evaluate internal computational consistency. Comparisons are paired by common timestamp or region without interpolation or removal of anomalous months. Ninety-five-percent confidence intervals use clustered resampling by civil day or spatial entity.

The evidence layers answer different questions. REN and E-REDES demand and generation products do not share identical accounting boundaries, and municipal or substation statistics are not line-flow truth. The target is therefore research-level plausibility and usability rather than equipment-level accuracy.

## Structural and computational consistency

The full static network contains 3,783 buses, 4,943 lines, and 228 transformers. A single connected component contains 3,664 active buses and 4,787 active lines. The simple-graph cycle rank is 501; median and 95th-percentile node degrees are 2 and 4. Model-to-public-background route-km ratios at 60, 130, 150, 220, and 400 kV are 0.992, 1.090, 0.817, 0.819, and 1.033. REN aggregates provide the 150/220/400 kV backgrounds. The 60/130 kV comparisons use non-independent OSM context and cannot be interpreted as accuracy. The 130 kV ratio is based on only seven model lines and is descriptive only.

Line parameters are a principal uncertainty. Of 4,240 60 kV lines, 1,517 have partial support from PDIRD circuit paths and 2,723 use voltage-class engineering proxies. At 130--400 kV, every line is classified as proxy at the line-level `parameter_status`. This overall classification differs from field-level support, which Figure 3 reports separately.

The 11 compact monthly databases contain 31,492 completed and converged 15-minute cases. The mean absolute AC closure residual across generation, boundary exchange, load, and loss is \(1.77\times10^{-6}\) MW, showing numerical agreement among released states and audit quantities.

![Figure 3](figures_final/fig03_network_parameter_evidence.png)

**Figure 3—Structural coverage and field-level parameter evidence.** (a) Model route-km divided by public background length for each voltage class; the dashed line marks unity. Grey circles use non-independent OSM context and blue squares use REN background. Route-km and circuit length have different definitions, so the ratio is not an accuracy measure. (b) Field-level separation of project-based resistance transfer (orange; engineering assumption) and source-backed ratings (green); cells show proportions. Grey denotes wholly proxy fields. The 35.8% resistance support is not direct observation and must not be assigned the same evidence level as ratings. Reactance and capacitance are proxies throughout. The 130 kV result contains seven lines and is descriptive only. Table 6 reports line-level evidence counts.

**Table 6—Network structure, parameter evidence, and computational completeness.**

| kV | Buses | Lines | Route-km | Reference km | Ratio | Evidence |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 60 | 3,236 | 4,240 | 9,668.4 | 9,742.0 | 0.992 | Partial 1,517; Proxy 2,723 |
| 130 | 8 | 7 | 41.6 | 38.2 | 1.090 | Proxy 7 |
| 150 | 132 | 156 | 2,054.1 | 2,514.0 | 0.817 | Proxy 156 |
| 220 | 214 | 294 | 3,206.9 | 3,916.0 | 0.819 | Proxy 294 |
| 400 | 193 | 246 | 3,579.5 | 3,465.0 | 1.033 | Proxy 246 |

**Table note.** The 130 kV sample has seven lines. References are OSM at 60/130 kV and REN aggregates at 150--400 kV; route-km is not circuit length. Partial is line-level; all \(x\) and \(c\) values are Proxy.

## Parameter and spatial-allocation sensitivity

Sensitivity experiments perturb line \(R/X\), capacitance, current ratings, transformer capacity and impedance, and the spatial allocation of load, generation, and boundary exchange at 22 representative times. After shared baselines are removed, 264 cases remain and all converge. The minimum Spearman correlation is 0.998 for line-impedance perturbations and 0.993 for transformer perturbations. Current-rating assumptions change maximum loading by as much as 34.48 percentage points. System-wide rankings remain comparatively stable under spatial alternatives, but allocating residual load by transformer capacity reduces the Top-20 Jaccard index to 0.429. Specific hotspots are consequently more assumption-dependent than the global ranking.

Figure 4 summarizes ranking, hotspot-set, and maximum-loading responses. Across the 12 configurations evaluated at each time, 74 of 4,943 lines enter the Top 20 at least once, 56 reach an inclusion frequency of at least 0.8 at one or more times, and the largest frequency across all 264 experiments is 207/264 (78.4%); no line remains in the Top 20 at every time and configuration. A separate 242-case operating-proxy ablation changes reactive-power limits, PV targets, load compensation, shunt reactors, and tap control at the same 22 times. All cases converge. Halving reactive-power limits produces the largest minimum-voltage change (0.02845 p.u.) and a 1.419-percentage-point maximum-line-loading change; active ratio-tap voltage control produces the largest maximum-transformer-loading change (4.305 percentage points). The complete paired results and configuration identifiers are released as machine-readable comma-separated value (CSV) files.

![Figure 4](figures_final/fig04_parameter_spatial_sensitivity.png)

**Figure 4—Sensitivity to parameters and spatial allocation.** (a) Spearman correlation of line-loading ranks; (b) Top-20 Jaccard index; and (c) absolute change in maximum loading. Markers denote medians over 22 operating states and lines extend to the least favorable result. The 154 parameter cases and 132 spatial cases share 22 baselines.

## External temporal agreement

National demand comparison contains 31,388 paired intervals. The E-REDES reference includes losses. The ratio of mean SimPT60 consumption to public E-REDES consumption is 1.003, with a day-block bootstrap 95% confidence interval of [1.002, 1.005]. Fifteen-minute Pearson correlation is 0.997 [0.996, 0.999], and mean absolute error (MAE) is 30.9 MW. Across 327 paired daily means, Pearson correlation is 0.995 and normalized root-mean-square error (RMSE) is 0.013. October 2025 contains E-REDES missingness and local deviations, but the month is retained [@EREDESNationalConsumption].

Wind comparison contains 31,484 paired intervals. The mean ratio between SimPT60 and E-REDES distribution-grid wind injection is 1.056 [1.055, 1.058], and Pearson correlation is 0.9998 [0.9997, 0.9998]. Daily-mean correlation is also 0.9998. Photovoltaic correlation is 0.982, but mean national photovoltaic production in SimPT60 is approximately 21.2 times distribution-grid injection. Hydro correlation is 0.536. The latter comparisons span different accounting scopes and are not absolute-scale validation [@EREDESDistributionInjection; @EREDESNationalProduction].

Figure 5 presents the paired temporal comparisons, interval densities, and normalized daily consumption profiles.

![Figure 5](figures_final/fig05_temporal_validation.png)

**Figure 5—External temporal validation of aggregate inputs.** (a--b) Paired daily means for national consumption and wind; (c--d) density of the corresponding 31,388 and 31,484 15-minute pairs, with a shared logarithmic count scale, one-to-one line, and Pearson \(r\); and (e) consumption profiles grouped by Lisbon local time and normalized by each series' daily mean. Comparisons exclude only intervals missing a required field and use no anomaly deletion, smoothing, or interpolation. Consumption excludes pumping and battery charging and differs from total electric load in the cases. Wind compares national production with distribution-grid injection across different scopes. The figure validates aggregate inputs, not nodal allocation or branch flow. Table 7 gives bootstrap intervals.

## External spatial agreement

At municipal level, E-REDES monthly billed consumption is the reference [@EREDESMunicipalityConsumption]. The 2,183 municipality-month pairs yield a Spearman correlation of 0.881, with a 95% confidence interval of [0.841, 0.910] from 199 municipality clusters; they cover 94.8% of contemporaneous billed energy. At substation level, 792 seasonal pairs yield a Spearman correlation of 0.957 [0.946, 0.967] over 397 station clusters, a mean ratio of 1.044, and MAE of 2.45 MW.

Municipality comparisons use the municipality containing each substation as a proxy for its service area and therefore provide weak spatial evidence. Seasonal substation loads come from the E-REDES capacity and loading product [@EREDESSubstationCapacity]. They test facility ranking and scale, but share the same operator as the 15-minute load data and are not fully independent nodal truth.

Figure 6 displays both spatial comparisons and their cluster-bootstrap uncertainty.

![Figure 6](figures_final/fig06_spatial_validation.png)

**Figure 6—Spatial agreement at municipality and substation levels.** (a) 2,183 municipality-month load-share pairs; (b) 792 seasonal substation-peak pairs. Axes have equal scale and dashed one-to-one lines. Labels report Spearman correlation and archived 1,000-replicate cluster-bootstrap 95% intervals using 199 municipality and 397 substation clusters; bands are not regression intervals. Municipality assignment is a weak service-area proxy, and cross-product evidence from the same operator is not fully independent nodal truth.

### Station-held-out spatial reconstruction test

A station-held-out experiment divides 394 matched stations into five deterministic folds. At 22 times, it compares global capacity share, five geographically nearest stations, and five nearest stations along the reconstructed network, yielding 8,541 observation pairs. Neighbor predictions use inverse-distance weights; network distance uses in-service line length and a small positive transformer-edge length. Confidence intervals are obtained by a 1,000-replicate station-cluster bootstrap using fixed fold assignments. Table 7 shows that network-neighbor MAE is approximately 9.9% below global capacity share, but is not lower than geographic-neighbor MAE and has an overlapping confidence interval. Reconstructed connectivity therefore contains useful proximity information but does not independently recover precise nodal demand. Capacity-share, geographic-neighbor, and network-neighbor RMSE values are 6.357, 5.675, and 5.702 MW, respectively; the corresponding weighted absolute percentage error (WAPE) values are 38.4%, 34.5%, and 34.6%. Fold assignments, pair-level predictions, and recomputation code are included in the released validation package.

Table 7 consolidates the cross-source comparisons and the three held-out allocation methods.

\Needspace{8\baselineskip}

**Table 7—External validation and station-held-out reconstruction results.**

| Target / method | n / blocks | Metric | Estimate [95% interval] |
| --- | ---: | --- | --- |
| Consumption | 31,388 / 327 d | Ratio; \(r\) | 1.003 [1.002, 1.005]; 0.997 [0.996, 0.999] |
| Wind | 31,484 / 328 d | Ratio; \(r\) | 1.056 [1.055, 1.058]; 0.9998 [0.9997, 0.9998] |
| PV variation | 31,484 / 328 d | \(r\) | 0.982 [0.979, 0.984] |
| Municipal share | 2,183 / 199 | \(\rho\) | 0.881 [0.841, 0.910] |
| Substation peak | 792 / 397 | \(\rho\) | 0.957 [0.946, 0.967] |
| AC closure | 31,492 | MAE, MW | \(1.77\times10^{-6}\) |
| Capacity share | 394 / 8,541 | MAE, MW | 4.729 [4.412, 5.063] |
| Geographic neighbors | 394 / 8,541 | MAE, MW | 4.249 [3.972, 4.544] |
| Network neighbors | 394 / 8,541 | MAE, MW | 4.262 [3.971, 4.545] |

**Table note.** \(n\) is the number of paired observations; d denotes day blocks. Other blocks are municipalities or stations.


## Generation scope and quantified dependence on spatial proxies

Directly observed load accounts for 67.93--74.93% of monthly consumption; the remaining 25.07--32.07% is allocated with PDIRT reference profiles. National generation proxies account for only 0.010--0.063% of monthly generation, but this national average hides small technology classes. In May 2025, proxy shares reach 90.58% for batteries and 11.07% for other thermal generation. Aggregate balance therefore does not establish reliable local asset location.

All non-rounding generation proxies are injected at one 400 kV receiving bus, which represents a model balancing location only. Figure 7 shows consumption residuals, technology-level generation proxies, and their spatial distribution by latitude band. Monthly values for eight technologies, interval flags, and bus-level details are released as CSV.

![Figure 7](figures_final/fig07_proxy_provenance.png)

**Figure 7—Temporal and spatial distribution of observed and proxy power.** (a) Observed consumption and the PDIRT-allocated residual; (b) technology-level proxy shares for batteries and other thermal generation; and (c) load-residual share by latitude band of receiving buses. Panels use different denominators. Regions denote allocation locations in the model, not the true locations of missing assets.

## Limitations

Validation supports aggregate temporal agreement, plausible spatial ranking, and numerical self-consistency. Parameter and allocation perturbations also show that system-wide trends are generally stable. Public information remains insufficient to validate device-by-device topology, branch flows, switching status, or generator capability. Correlation, convergence, and screening counts must therefore not be interpreted as equipment-level accuracy of an operator network. SimPT60 is intended for reproducible research cases, relative-risk comparison, and method testing. Real-time dispatch, protection settings, and formal compliance assessment require contemporaneous operator-approved models and operating records.

# Usage Notes

The release is designed for reproducible time-series power-flow studies, relative scenario comparison, and method testing. Users should select the model variant explicitly: CORE-3783 is the static network used by the 31,492 archived operating cases and their validation products, whereas N1-3787 is restricted to the sampled contingency panel. The main and monthly DuckDB files join through stable model, equipment, case, and UTC timestamp identifiers. Principal evidence and mapping fields include `source_id`, `bus_id`, `bus_assignment_rule`, `available_from_utc`, `source_status`, and field-level parameter status; case-level inputs and results join through `case_id`, `timestamp_utc`, `bus_order`, and `line_order`.

The released power-flow states are simulations derived from public data. Branch loading, bus voltage, and contingency results should be interpreted comparatively and together with `parameter_status`, `source_status`, allocation-audit fields, and the stated screening thresholds. In the contingency panel, the incremental flag requires principal-solution convergence, no material island, and no new voltage or thermal violation relative to the corresponding N−0 state. It does not identify every worsening of a pre-existing violation, so absolute post-contingency voltage and loading values must remain the primary screening outputs.

Together, the linked records address the data gap identified in the Background & Summary by joining a traceable 60--400 kV network, continuous operating inputs, and reproducible solved states in one versioned resource. External comparisons support aggregate temporal agreement and spatial ranking; held-out tests show the additional but limited value of reconstructed network proximity; the sensitivity results identify which branch-loading and voltage conclusions depend on engineering assumptions; and the sampled N−1 records provide transparent risk-screening cases. These findings support comparative research and method evaluation while defining the boundary beyond which operator data are required.

# Data Availability

The frozen **SimPT60-2026.09.21-r1** dataset release [@SimPT60Release2026] is available from the [project's versioned GitHub release](https://github.com/MingLeiZhou/PortugueseOPD/releases/tag/SimPT60-2026.09.21-r1). It contains the reproducibility core, 11 compressed monthly result databases, the monthly manifest, `release.json`, and `SHA256SUMS`. Raw third-party files are redistributed only when their licences permit; otherwise, the release retains source URLs, archived metadata, file sizes, fingerprints, and retrieval instructions. The release manifest identifies the CORE-3783 and N1-3787 model variants and their permitted uses.

# Code Availability

Reproduction code, the locked Python environment, and the minimal replay commands are included in the [SimPT60-2026.09.21-r1 release](https://github.com/MingLeiZhou/PortugueseOPD/releases/tag/SimPT60-2026.09.21-r1). The immutable tag fixes the release state, while `release.json` and `CODE_VERSION.json` record the implementation commit and the limitation that the original historical monthly runs did not store a code commit. The replay workflow verifies selected released cases at a tolerance of \(10^{-5}\) in the units of each compared field.
