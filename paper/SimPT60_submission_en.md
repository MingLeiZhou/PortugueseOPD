# SimPT60: A Traceable Time-Series Power-Flow Dataset for Portugal’s High-Voltage Grid

# Abstract

Publicly accessible power-system data are commonly fragmented across network structure, equipment attributes, and operating records, which limits their direct use in continuous power-flow analysis and method comparison. We present SimPT60, a research dataset reconstructed from multiple public sources for the continental Portuguese 60--400 kV grid and the Portugal--Spain interconnection boundary. SimPT60 organizes the static network, operating time series, and alternating-current power-flow results in a unified data framework. It provides continuous 15-minute operating cases, source-level provenance, and validation products for assessing model consistency. The dataset supports time-series power-flow studies, scenario comparison, relative-risk screening, and testing of grid-analysis algorithms. Its research utility is demonstrated using E-REDES public datasets that were withheld from nodal-power construction, parameter-sensitivity experiments, and N−1 contingency screening. SimPT60 is a public-data-derived research model and has not been validated as an operator network model at equipment level.

**Keywords:** power-system dataset; Portugal; public data; grid reconstruction; power flow; time series; provenance; cross-source validation

------

# 1. Introduction

Power-flow simulation underpins many studies of grid operation, planning, and risk, and requires data that describe network topology, equipment attributes, and operating conditions ([Thurner et al., 2018](#ref-Thurner2018)). As research moves from individual snapshots to time-series analysis, a static network must also be linked to continuous load, generation, and other operating records ([Hörsch et al., 2018](#ref-Horsch2018); [Meinecke et al., 2020](#ref-Meinecke2020)).

Several public resources already provide reusable grid models and benchmark datasets. PyPSA-Eur supplies a transmission-network model for European energy-system optimization and operational studies ([Hörsch et al., 2018](#ref-Horsch2018)). Recent open-data reconstruction work has expanded the geographic representation of the European high-voltage grid ([Xiong et al., 2025](#ref-Xiong2025)). SimBench provides representative networks across several voltage levels together with annual load and generation profiles ([Meinecke et al., 2020](#ref-Meinecke2020)). These resources support system optimization, network reconstruction, and method comparison, respectively, and provide an important basis for open power-system research.

## 1.1 Motivation

Portugal's National Energy and Climate Plan identifies renewable generation, storage, electrification, and cross-border interconnection as central elements of the energy transition ([DGEG, 2024](#ref-DGEGPNEC2030Revision2024)). These developments alter the scale and spatial distribution of generation and demand and therefore affect line flows, bus voltages, and system risk. Studying the time-varying behavior of the Portuguese grid consequently requires a data foundation that represents both network structure and continuous operating conditions.

The relevant public records are distributed across E-REDES, Redes Energéticas Nacionais (REN), the Direção-Geral de Energia e Geologia (DGEG), the Entidade Reguladora dos Serviços Energéticos (ERSE), and open geospatial platforms ([E-REDES, n.d.-f](#ref-EREDESRND2026); [REN, n.d.](#ref-RENDataHub2026); [DGEG, n.d.](#ref-DGEGGeo2026); [REN, 2024](#ref-ERSEPDIRT2024); [OpenStreetMap contributors, n.d.](#ref-OSM2026)). Their spatial coverage, temporal resolution, identifiers, and field definitions differ. Using them in a common power-flow model requires topology reconstruction, completion of electrical parameters, asset-to-bus mapping, temporal alignment, and allocation of aggregate power to nodes.

Existing resources also have different modelling objectives and scopes. European-scale models primarily address cross-regional system studies ([Hörsch et al., 2018](#ref-Horsch2018)); open high-voltage reconstructions focus mainly on AC networks at 220 kV and above ([Xiong et al., 2025](#ref-Xiong2025)); and representative benchmarks do not describe the geographic Portuguese grid ([Meinecke et al., 2020](#ref-Meinecke2020)). A public research resource that combines the continental Portuguese 60--400 kV network, 15-minute operating time series, solvable power-flow cases, and explicit provenance has therefore remained unavailable.

This study develops a traceable, computationally usable Portuguese high-voltage grid dataset with continuous time-indexed cases for power-flow analysis, grid-risk screening, scenario comparison, and method testing.

## 1.2 Introducing SimPT60

SimPT60 reconstructs the continental Portuguese high-voltage grid from multiple public sources. It covers the 60, 130, 150, 220, and 400 kV networks and retains the boundary nodes required to represent Portugal--Spain interconnections. Raw sources, the static network, time-series inputs, power-flow results, and validation records are stored in linked layers so that users can trace key fields to their source and processing rule.

The main contribution is a versioned data product that links a traceable public-data reconstruction of the continental Portuguese 60--400 kV grid to 31,492 consecutive and reproducible 15-minute AC power-flow states. Raw records, derived objects, and validation results remain separate but connected. Table 1 summarizes the scope, principal products, and interpretation boundaries; Section 4.7 states the limitations.

**Table 1—Scope and data products of SimPT60.**

| Dimension | Scope or product | Interpretation boundary |
| --- | --- | --- |
| Geography | Continental Portugal and Portugal--Spain boundary endpoints | Excludes islands and distribution networks below 60 kV |
| Voltage | 60, 130, 150, 220, and 400 kV | Reconstructed from multiple public sources |
| Time | 2025-05-01 to 2026-03-24; 15 min | Europe/Lisbon civil days; aligned in Coordinated Universal Time (UTC) |
| Static network | Buses, lines, transformers, assets, and geometry | Public connection evidence and engineering proxies coexist |
| Operating cases | 31,492 steady-state AC power-flow cases | Computed model states, not equipment telemetry |
| Distribution and provenance | Main database, 11 monthly databases, raw-record locators, and source hashes | Source licensing and release snapshots are managed separately |
| Validation and application | External temporal/spatial comparisons, perturbation tests, stress scenarios, and N−1 | Aggregate consistency does not establish equipment-level truth |

**Table note.** Sections 1--3 define the scope, construction rules, and data products.

The remainder of the paper is organized as follows. Section 2 describes the source data and construction method. Section 3 presents the static network, time series, and power-flow cases. Section 4 reports validation results, Section 5 provides application examples, and Section 6 concludes the paper.

------

# 2. Methodology

SimPT60 was constructed in five stages: archiving and standardizing public records; reconstructing the 60--400 kV static network; mapping assets and operating records to a common 15-minute calendar; generating and solving one AC power-flow case per interval; and storing the results in linked layers with explicit quality controls.

## 2.1 Public Data Scope and Standardization

### 2.1.1 Scope and Sources

The geographic, voltage, and temporal scope is given in Table 1. Network geometry is drawn mainly from the E-REDES 60/130 kV network and the OpenStreetMap (OSM)/Geofabrik 150/220/400 kV network ([Geofabrik GmbH, n.d.](#ref-GeofabrikPortugal2026)). DGEG and APA records supplement generation, storage, and connection evidence ([DGEG, n.d.](#ref-DGEGGeo2026); [APA, 2021](#ref-APA3403); [APA, n.d.-a](#ref-APAPPA421); [APA, n.d.-b](#ref-APAPPA407)). REN provides national 15-minute operating series, whereas E-REDES provides substation energy. PDIRT and PDIRD documents provide delivery-point profiles and selected equipment parameters ([E-REDES, n.d.-f](#ref-EREDESRND2026); [OpenStreetMap contributors, n.d.](#ref-OSM2026); [REN, n.d.](#ref-RENDataHub2026); [REN, 2024](#ref-ERSEPDIRT2024)). GISCO supplies the continental boundary ([Eurostat GISCO, 2024](#ref-GISCO2024)); REN and REE records support transmission and interconnection checks. OpenInfraMap, which derives from OSM, is used only for display checks and is not treated as independent evidence ([OpenInfraMap contributors, n.d.](#ref-OpenInfraMap2026)).

Table 2 summarizes the source scope and modelling role. The common study window is determined by synchronized E-REDES and REN series. Static-source versions, case dates, and historical availability are recorded separately; the common window is not interpreted as the observation date of every device.

**Table 2—Public data sources, coverage, and modelling roles.**

| Publisher / product | Spatial or temporal coverage | Principal content | Role in this study |
| --- | --- | --- | --- |
| E-REDES / RND high-voltage network (AT in the source portal) | Continental 60/130 kV; archived snapshot | Lines, facilities, codes, and geometry | Network construction |
| OSM / Geofabrik | Continental Portugal and Spain boundary; archived snapshot | 150/220/400 kV facilities and circuits | Network and asset construction |
| DGEG; APA; project notices | Generation, storage, and connection records | Location, technology, capacity, and commissioning evidence | Asset completion and evidence |
| REN / Data Hub | 15 min in common window; 15 series | Demand, generation, storage, imports, and exports | Inputs and withheld comparison |
| E-REDES / substation energy | 397 stations; 15 min | Facility code, interval-end timestamp, and kWh | Direct nodal load |
| ERSE / PDIRT; PDIRD | Planning and equipment inventories | Delivery-point profiles, circuits, and ratings | Residual weights and parameter evidence |
| REN / REE | Transmission system and interconnection | Circuit, length, and capacity aggregates | Inventory, calibration, and context |
| GISCO | Continental Portugal | National boundary geometry | Spatial clipping and cartography |
| E-REDES auxiliary statistics | 14 datasets; multiple resolutions | National, municipal, injection, and seasonal load statistics | External comparison; excluded from nodal construction |

**Table note.** The common modelling window is 2025-05-01 to 2026-03-24. Source entry points, archived versions, and file fingerprints are listed in the data documentation and release manifest.

### 2.1.2 Archiving and Standardization

Raw files are archived immutably by source together with the source URL, download time, file size, and SHA-256 hash; processing reads the raw layer without modifying it. Coordinates are stored in World Geodetic System 1984 (WGS 84) and transformed to the Portuguese national projection for distance calculations. Voltage, power, energy, and distance are standardized to kV, MW/Mvar, kWh, and km. E-REDES 15-minute energy is converted to interval-average power according to Equation (1):

\[
P_{\mathrm{MW}}=\frac{E_{\mathrm{kWh}}}{250}. \tag{1}\label{eq:energy-to-power}
\]

Both Europe/Lisbon local time and UTC are retained. Stable identifiers are derived from source equipment codes, and every derived object retains source keys and processing status. Supplementary Table S1 maps raw source fields to standardized semantics.

## 2.2 Static Network Reconstruction

The static network is represented by Equation (2):

\[
G=(B,L,T), \tag{2}\label{eq:static-network}
\]

where \(B\), \(L\), and \(T\) denote buses, lines, and transformers. Connections are established in the order source relationship, voltage consistency, and constrained spatial matching. This hierarchy prevents geographic proximity or line crossings from being interpreted directly as electrical connectivity.

### 2.2.1 Buses and Lines

E-REDES is preferred for 60/130 kV lines, whereas OSM supplies most 150/220/400 kV lines and facilities; the latter are checked against REN length statistics. Duplicate E-REDES facilities, mobile facilities within 250 m of permanent stations, and colocated same-name stations are normalized while retaining a merge ledger.

Line endpoints are grouped by voltage and clustered within 75 m in EPSG:3763 coordinates. Different voltage levels are never merged. Each endpoint cluster is assigned to a same-voltage facility within 250 m; otherwise, a derived connection bus is created. OSM ways are split only at way endpoints or explicit shared nodes, so geometric crossings do not create connections. The OSM `circuit` and `line_section` relations restore circuit identity. Parallel circuits are retained, while self-loops and non-positive-length branches are blocked and recorded.

### 2.2.2 Transformers and Parameters

Transformers are created from three evidence classes. Explicit OSM transformers are matched to high- and low-voltage buses within 1 km. Adjacent voltage levels in the same OSM substation produce colocated transformers. RARI (Regulamento do Acesso às Redes e às Interligações do Setor Elétrico) boundary names are matched to facilities within 5 km, with a fallback to a 60 kV endpoint within 1 km of the high-voltage station. Public `rating` values take precedence; remaining capacities and impedances use voltage-pair proxies. A voltage-pair capacity is scaled upward when its aggregate falls below the corresponding REN total, and the calibration factor is retained ([REN, n.d.](#ref-RENDataHub2026)).

Line parameters comprise \(r\), \(x\), \(c\), and \(I_{\max}\). Each 60 kV branch is matched to a public PDIRD circuit inventory by a length-weighted shortest path, accepted only when the model-to-source length ratio lies between 0.65 and 1.60 ([E-REDES and ERSE, 2020](#ref-EREDESPDIRD2020AnnexB)). Remaining lines use voltage-class defaults and cable correction factors. Each parameter is labelled as directly published, derived from public documentation, aggregate-calibrated, or an engineering proxy. Some resistance support is transferred from conductor parameters reported in project documentation ([EDP Distribuição, 2019](#ref-Tocha2019)); this transfer remains an engineering assumption rather than a direct line measurement.

Post-construction checks cover equipment keys, endpoints, positive length, voltage consistency, connected components, and the Portugal--Spain boundary inventory. Lengths at 150, 220, and 400 kV are compared with REN statistics. Parameter completeness means that the model is computationally usable; it does not mean that all parameters are operator measurements.

Figure 1 illustrates the connection decisions, and Table 3 lists thresholds and unmatched-record handling.

![Figure 1](figures_final/fig01_network_reconstruction.png)

**Figure 1—Connection rules used in static-network reconstruction.** (a) Same-voltage endpoint clustering and facility matching; (b) distinction between geometric crossings and explicit shared nodes; (c) preservation of series segments and parallel-circuit identity; and (d) cross-voltage connection from explicit equipment, colocated facilities, and RARI boundary evidence. The diagrams are abstract electrical schematics rather than geographic device locations. Distance thresholds and fallback rules are listed in Table 3.

**Table 3—Topology rules and thresholds.**

| Object / step | Rule or threshold | Constraint / unmatched handling |
| --- | --- | --- |
| Distance coordinates | EPSG:3763; metres | Longitude and latitude stored in WGS 84 |
| Endpoint clustering | 75 m within the same voltage | Different voltages are not merged |
| Facility assignment | Same-voltage facility within 250 m | Create a derived connection bus if unmatched |
| Facility normalization | Duplicate or colocated same-name facilities; mobile facilities within 250 m of permanent stations | Retain merge records |
| OSM splitting | Way endpoints or explicit shared nodes | Geometric crossings do not create connections |
| Circuit identity | `circuit` / `line_section`; parallel circuits retained | N−1 removes series model segments by physical circuit |
| Explicit transformer | Match high- and low-voltage buses within 1 km | Preserve voltage-level consistency |
| Colocated transformer | Adjacent voltage levels in one OSM station | Derived connection with evidence level retained |
| RARI boundary | Name-to-facility match within 5 km | May fall back to a 60 kV endpoint within 1 km of the high-voltage station |
| PDIRD parameter path | Length-weighted shortest path; model/source length 0.65--1.60 | Use voltage-class proxy when rejected |
| Blocking checks | Self-loop, non-positive length, endpoint or voltage conflict | Record and block invalid branches |

**Table note.** Rules are defined in Section 2.2 and `model_config.json`. All distances are projected planar distances. They are reconstruction rules, not operator accuracy guarantees. Default electrical parameters and coverage rules are reported in Supplementary Table S2.

### 2.2.3 Evidence Applicability and Historical Availability

Static-source dates and operating-case dates are versioned separately. Only assets with an explicit commissioning date are switched by time. Ordinary lines, transformers, and interconnections retain the frozen static state when contemporaneous status records are unavailable. Project documents support only the stated equipment and fields and are not used to infer historical switching states. Cross-section scaling of conductor resistance and transfer to other conductor families remain engineering assumptions. Document pages, equipment identifiers, and field-level judgements are stored in `citation_evidence_ledger.csv`; Table 4 summarizes temporal handling.

**Table 4—Source versions, validity dates, and case treatment.**

| Object | Date or status evidence | Case treatment and limitation |
| --- | --- | --- |
| Ordinary lines and transformers | Historical status absent for most individual devices | Frozen static state; not a complete historical replay |
| New Minho--Galicia interconnection | REE announcement dated 2026-07-02 | Disabled in every study case |
| Other Portugal--Spain interconnections | Maintenance and switching histories unavailable | Fixed boundary equivalent; historical maps used only to check corridor names ([REE, 2012](#ref-REE2012)) |
| Generation and storage | 366 dated and 824 undated assets | Known dates gate availability; unknown dates are treated as available and flagged |
| PDIRT/PDIRD and project evidence | Planning inventories, seasonal profiles, or project records | Parameter, weight, and connection evidence; no inference of real-time commissioning |

**Table note.** In N1-3787, the capacity and voltage pair of the three Estoi parallel equivalents are supported by a planning inventory, but simultaneous availability and identical parameters remain model assumptions ([REN, 2015](#ref-REN2015Estoi)).

## 2.3 Asset Mapping and Time-Series Alignment

### 2.3.1 Asset Mapping and Calendar

The generation and storage inventory combines OSM, DGEG, and project evidence. Plant and generator representations within 3 km and with consistent capacity are merged. DGEG wind records replace incomplete OSM wind capacities, and non-duplicate DGEG photovoltaic records supplement OSM. Mapping priority is public connection evidence, declared voltage, and then constrained spatial proximity. An asset without declared voltage and with capacity of at least 100 MW searches only buses at 150 kV and above; smaller assets search buses at 60 kV and above. The maximum distance is 20 km. Assets beyond this distance remain in the inventory but are not injected. Assets commissioned after a case date are unavailable; missing dates are treated as available and explicitly flagged.

The common calendar uses `interval_start_utc` as its logical key. REN timestamps denote interval starts, whereas E-REDES timestamps denote interval ends, as formalized in Equation (3):

\[
t_{\mathrm{E\text{-}REDES}}=t_{\mathrm{REN}}+15\ \mathrm{min}. \tag{3}\label{eq:time-alignment}
\]

Calendar days follow Europe/Lisbon civil time, giving 92 and 100 intervals on the spring and autumn daylight-saving transitions. REN intra-day sequence numbers distinguish the repeated hour. E-REDES has no fold marker, so duplicate station timestamps are averaged and flagged as ambiguous. Missing intervals are not interpolated.

### 2.3.2 Load, Generation, and Storage

E-REDES energy is mapped to buses by facility code and converted to nodal load. Equation (4) defines the unobserved national residual:

\[
R_t=P^{\mathrm{REN}}_{\mathrm{consumption},t}-\sum_j P^{\mathrm{EREDES}}_{j,t}. \tag{4}\label{eq:national-residual}
\]

Equation (5) assigns the residual to mapped delivery points using the PDIRT public reference profile from the same season and closest national-load condition ([REN, 2024](#ref-ERSEPDIRT2024)):

\[
w_{j,t}=\frac{P^{\mathrm{PDIRT}}_{j,r(t)}}{\sum_{k\in\mathcal{M}}P^{\mathrm{PDIRT}}_{k,r(t)}},\qquad
P^{\mathrm{res}}_{j,t}=w_{j,t}R_t. \tag{5}\label{eq:residual-allocation}
\]

The profile provides only spatial weights and does not change the 15-minute resolution. E-REDES load uses a 0.97 power factor where reactive power is not observed; residual load retains the PDIRT \(Q/P\) relationship. Pumping and battery charging are distributed by mapped storage capacity, with any remainder assigned to an explicit national proxy. Nodal electric load must sum to REN `Consumption + Storage` within 0.11 MW.

REN generation by technology is distributed among mapped and commissioned assets of the same technology under \(0\le P_{g,t}\le P_g^{\mathrm{nameplate}}\). Hydro and natural gas use deterministic capacity-priority allocation, while other technologies use available-capacity shares. Generation beyond mapped capacity is injected at a designated 400 kV proxy bus as `UNMAPPED_NATIONAL_RESIDUAL_PROXY`; it must not be interpreted as the location of an omitted plant.

REN imports and exports are aligned to the common calendar but withheld from boundary-power enforcement. Before solution, the pipeline checks timestamps, required series, mapping completeness, load and generation conservation, and capacity limits. Table 5 summarizes the executable mapping and conservation rules; Supplementary Table S3 lists the associated audit fields.

**Table 5—Asset mapping and time-series transformation rules.**

| Process | Rule | Audit or conservative treatment |
| --- | --- | --- |
| Asset deduplication | Merge plant/generator records within 3 km and with consistent capacity | Preserve hierarchical deduplication status |
| Bus mapping | Public connection, declared voltage, then constrained nearest bus | Maximum 20 km; retain but do not inject out-of-range assets |
| Asset without declared voltage | Capacity ≥100 MW searches ≥150 kV; otherwise ≥60 kV | Spatial proximity is not treated as connection truth |
| Commissioning status | Disable when commissioning date is later than the case | Treat unknown dates as available and flag them |
| Time labels | REN start; E-REDES end shifted by −15 min | Common key is UTC interval start |
| Daylight saving / missing data | Lisbon civil days contain 92, 96, or 100 intervals | Average and flag repeated E-REDES station labels; no interpolation |
| Energy to power | \(P(\mathrm{MW})=E(\mathrm{kWh})/250\) | Interval-average power over 15 min |
| Direct load and residual | E-REDES observations plus PDIRT-weighted REN consumption residual | pf=0.97 when Q is missing; retain residual Q/P |
| Pumping and battery charging | Positive values are electric load; distribute by mapped storage capacity | Total load returns to Consumption + Storage |
| Generation allocation | Same technology, mapped, commissioned, and nameplate-constrained | Hydro/gas use capacity priority; others use capacity share |
| Unmapped generation residual | Explicit national proxy-bus injection | Must not be interpreted as a physical omitted-plant location |
| Cross-border exchange | REN imports/exports used only after solution | Initial model solution determines boundary injections |

**Table note.** The physical fields are named `timestamp_utc` in the calendar and monthly databases; `interval_start_utc` describes their temporal semantics.

## 2.4 Time-Indexed Case Generation and Alternating-Current Power Flow

Each 15-minute interval defines the steady-state case in Equation (6):

\[
C_t=(G,\theta,X_t,S_t,U_t,B_t), \tag{6}\label{eq:case-definition}
\]

where the static network \(G\) and parameters \(\theta\) are shared, while operating inputs \(X_t\), device status \(S_t\), controls \(U_t\), and boundary equivalent \(B_t\) vary with time. The principal dataset contains one case per interval; sensitivity and application experiments in Sections 4 and 5 use separately selected representative times.

### 2.4.1 Case Assembly and Solution

Each case updates load, generation, equipment availability, seasonal ratings, and boundary equivalents. An initial boundary solution determines model net exchange. One angular reference is then retained, and the other boundary nodes receive fixed active-power equivalents distributed by public voltage and circuit count. Contemporaneous REN exchange is used only for post-solution comparison.

AC power flow uses the pandapower Newton--Raphson implementation with DC initialization ([Thurner et al., 2018](#ref-Thurner2018)). The principal workflow enforces reactive-power limits, resolves once after boundary reconstruction, and performs a bounded tap search. Table 6 lists seasonal ratings, PV/PQ rules, compensation, reactors, boundary weights, and numerical settings. These common settings are computational proxies rather than operator control policies.

### 2.4.2 Monthly Execution

Cases are processed in parallel by civil month. Workers read inputs and solve cases independently, whereas the parent process writes summaries, state arrays, and audit bundles in one transaction. Re-runs skip complete cases and retry failed cases. A month is complete only when expected cases, completed cases, state arrays, and audit bundles agree.

**Table 6—Case and solver settings.**

| Item | Value or rule | Status |
| --- | --- | --- |
| Seasonal line rating | May--Sep ×1.00; Mar--Apr and Oct--Nov ×1.10; Dec--Feb ×1.15 | Engineering proxy relative to summer base |
| PV threshold | Non-battery; installed capacity ≥20 MW and voltage ≥60 kV | Remaining generation is PQ |
| PV voltage / reactive power | 1.0 p.u.; ±0.50 × installed MW in Mvar | Uniform proxy |
| Load compensation | Target pf=0.98; maximum 15 Mvar per bus | Engineering proxy |
| Shunt reactors | 5 × 150 Mvar at 400 kV | Explicitly flagged proxy equipment |
| Boundary | One angle reference; other nodes weighted by voltage × circuit count | Not calibrated to measured REN exchange |
| AC algorithm | Newton--Raphson; DC initialization; enforce Q limits | Maximum 50 iterations; tolerance \(10^{-6}\) MVA |
| Transformer taps | High-voltage side ±8 steps; 1.25% per step; maximum 16 rounds | Target 0.985--1.015 p.u. |
| General screening | Voltage 0.90--1.10 p.u.; loading 100% | Violations retained; N−1 criteria defined separately |

**Table note.** Settings come from the frozen `model_config.json` and monthly execution code.

## 2.5 Layered Storage, Provenance, and Quality Control

### 2.5.1 Database and Provenance

The main DuckDB stores sources in `raw_eredes`, `raw_dgeg`, `raw_osm`, `raw_reference`, `raw_documents`, and `raw_eredes_aux`. The `grid` and `geo` schemas store the static network and geometry, `scenario` stores asset operating points, `main` stores the common calendar and operating series, and `provenance` stores file- and entity-level lineage.

Each month has a separate result database. `monthly_model.cases` stores one row per timestamp with status, summary metrics, and complete result JSON. `bus_order`, `line_order`, and `state_arrays` store bus voltage and line-loading arrays in fixed equipment order. `audit_bundles` preserve load, generation, boundary, hotspot, and loss records. Views expand arrays to equipment-long tables. The static model is copied once per monthly database rather than repeated for every case.

Source records include URL, archive path, file size, and SHA-256. `table_lineage`, `raw_record_locator`, and `entity_evidence` provide table-, record-, and evidence-level traceability. Each monthly `run_manifest` records input fingerprints and case counts. Supplementary Table S5 provides the complete data-layer, grain, and join-key inventory.

### 2.5.2 Quality Control and Recovery

Quality control has four levels. Static checks cover keys, endpoints, voltage, length, connectivity, and the boundary inventory. Time-series checks cover temporal alignment, source completeness, load and generation conservation, and capacity limits. Solver checks cover convergence, voltage, loading, and the active-power-balance residual in Equation (7):

\[
\varepsilon_t=P_t^{\mathrm{gen}}+P_t^{\mathrm{model,net}}-P_t^{\mathrm{load}}-P_t^{\mathrm{loss}}. \tag{7}\label{eq:power-balance}
\]

External checks use E-REDES national, municipal, substation, production, and injection statistics that were excluded from nodal construction, together with REN exchange and monthly loss reports. The 0.90--1.10 p.u. voltage band and 100% loading threshold are screening criteria; violating cases remain in the release.

A case summary, state arrays, and audit bundle are committed in one transaction. Any failure rolls back the full case and retains an error record. Before formal monthly release, source and copy counts for completed cases, arrays, and audit bundles must match. Supplementary Table S4 lists thresholds, failure behavior, and record locations.

------

# 3. Overview of the SimPT60 Dataset

SimPT60 comprises one main database and 11 monthly power-flow result databases. The main database contains the static network, common time series, source records, and validation observations. Monthly databases contain the 15-minute cases and their solved states. The current static model version is `PT60-v2.0.0`.

## 3.1 Static Network Data

The static network contains 675 facilities, 3,783 buses, 4,943 lines, 228 transformers, 1,190 generation or storage units, 468 load points, and seven Portugal--Spain boundary endpoints. `grid.buses`, `grid.lines`, and `grid.transformers` provide connectivity and electrical parameters; `grid.generators`, `grid.load_points`, and `grid.interconnectors` link assets to buses. Line and facility geometry is stored separately in `geo.line_geometries` and `geo.facility_geometries` to avoid duplicating spatial objects in electrical tables.

Equipment tables retain `source`, `source_status`, `parameter_status`, and mapping-rule fields. Users can build the complete pandapower network, select only devices above an evidence threshold, or identify components whose parameters were derived from public records or engineering proxies.

Figure 2 shows the geographic network and one archived operating state. Projection and cartography use GeoPandas ([Fleischmann et al., 2026](#ref-GeoPandas2026)).

![Figure 2](figures_final/fig02_geographic_network_state.png)

**Figure 2—Geographic network and solved state at the annual peak.** (a) The 60--400 kV network containing 3,783 buses and 4,943 lines; (b) line loading; and (c) bus voltage. All panels use EPSG:3763 with the same scale and extent, retain original line geometry and Portugal--Spain boundary endpoints, and show a 100 km scale bar. The operating state is the archived annual-peak model at 2026-01-15 12:15 UTC (load 11,329.2 MW), joined by stable equipment identifiers. Missing solved states remain grey and are not imputed. The loading scale retains values above 100%. Values are modelled power flows, not telemetry.

## 3.2 Time-Series Data

`main.interval_calendar` is the common index for all time series. It covers 328 civil days from 2025-05-01 to 2026-03-24 and contains 31,492 local 15-minute intervals. Interval counts follow Europe/Lisbon daylight-saving transitions and therefore need not equal the number of days multiplied by 96.

`main.eredes_load` contains 12,360,502 load records for 397 substations. Raw records extend to 2026-03-25, but the joint modelling window ends on 2026-03-24. `main.ren_dispatch` contains 472,380 records across 15 national operating series for load, generation by technology, storage, and cross-border exchange. `main.weather_hourly` holds 23,616 hourly records for Lisbon, Porto, and Faro; `main.ren_rnt_balance` contains 660 monthly balance records over 11 reporting periods.

The database also archives 14 E-REDES auxiliary datasets with 2,239,064 records. They cover national and municipal consumption, contracted power, production, distribution-grid injection, distributed self-consumption, seasonal substation loading, power quality, and continuity of supply. These tables are excluded from nodal-power construction and serve as withheld public observations in Section 4.

## 3.3 Time-Indexed Power-Flow Cases

Interval results are partitioned by civil month to limit file size and support selective download. Eleven compact monthly databases map one-to-one to the common calendar and contain 31,492 cases. Every case in the present release completed AC power flow and is marked as converged. October 2025 contains 2,980 intervals because daylight saving time ends, whereas March 2026 covers only the first 24 days and contains 2,304 intervals.

Each case retains summary values, equipment-state arrays, and an allocation audit bundle. Fixed `bus_order` and `line_order` tables expand state arrays to equipment-long form; the static device order is stored once per month. Supplementary Table S5 lists fields and joins.

The main database also contains the N−1 application results from Section 5. `scenario.annual_nminus1_snapshots` records six representative times, `scenario.annual_nminus1_results` stores 9,894 contingency-level results, and `scenario.annual_nminus1_overloads` stores post-contingency overloaded circuits. `scenario.nminus1_control_policy` and `provenance.annual_nminus1_runs` record control-search bounds and experiment provenance. These application tables remain separate from the continuous 15-minute cases so that sampled security screening is not mistaken for continuous operating observation.

## 3.4 Dataset Coverage and Composition

Figure 3 shows interval availability, the peak-load week, and monthly case completeness.

![Figure 3](figures_final/fig03_temporal_coverage.png)

**Figure 3—Temporal coverage and case completeness.** (a) Daily fractions of available model cases, paired national-consumption observations, and paired wind observations. Denominators follow Lisbon civil days and daylight saving time; missing data are shown without interpolation. (b) The complete civil week containing maximum system load, with total electric load, generation, and REN net import. (c) Completed and converged 15-minute cases by month, totaling 31,492. The common window is 2025-05-01 to 2026-03-24; October contains the repeated daylight-saving hour, and March is partial through day 24.

Table 7 summarizes the principal static, time-series, validation, and application objects.

**Table 7—Principal dataset statistics.**

| Category | Object | Count | Basis |
| --- | --- | ---: | --- |
| Static | Facilities | 675 | Archived model inventory |
| Static | Buses | 3,783 | 2026-09-16 topology-validation snapshot |
| Static | Lines | 4,943 | Voltage-level coverage table |
| Static | Transformers | 228 | Topology-validation snapshot |
| Static | Generation / storage units | 1,190 | Asset inventory |
| Static | Load points | 468 | Load inventory |
| Static | Portugal--Spain boundary endpoints | 7 | Interconnection inventory |
| Active network | Buses / lines | 3,664 / 4,787 | Connectivity statistics |
| Time series | Common dates / intervals | 328 days / 31,492 | Common calendar |
| Time series | Substation energy | 397 stations / 12,360,502 records | Raw records through 2026-03-25 |
| Time series | REN national series | 15 types / 472,380 records | 15 min |
| Context | Weather | 3 locations / 23,616 records | Hourly |
| Context | REN monthly balance | 11 periods / 660 records | Monthly |
| Validation | E-REDES auxiliary products | 14 types / 2,239,064 records | Multiple temporal resolutions |
| Power flow | Compact monthly databases / cases | 11 / 31,492 | Completed and converged |
| Application | Full-element N−1 | 6 times / 9,894 cases | 1,649 elements per time |

**Table note.** Counts describe the released CORE-3783 variant. N−1 uses N1-3787. Section 3.6 and Supplementary Note S2 define their object-level differences, section applicability, and fixed hashes.

## 3.5 File Organization and Distribution

The frozen release comprises the analysis-ready main database, 11 monthly result databases, source and provenance records, and validation and application results. Stable model identifiers join the static network to the common calendar in the main database. Case identifiers and fixed equipment order join summaries, state arrays, and audit bundles in the monthly databases. Raw files are distributed when licensing permits; otherwise, the release provides source metadata and download scripts. `release.json`, `SHA256SUMS`, and the release README define file hashes, the environment, and reproduction entry points.

## 3.6 Frozen Release and Model-Version Reconciliation

The paper freezes release **SimPT60-2026.09.21-r1**. The 31,492 historical cases and validation in Sections 3--4 use CORE-3783: 3,783 buses, 4,943 lines, and 228 transformers. The Section 5.2 N−1 demonstration uses N1-3787. In that variant, four colocated connection nodes correct the Lanheses--Feitosa topology, and one merged Estoi transformer object is separated into three parallel equivalent units. The transformer table therefore contains 230 rows, although the three Estoi units form one symmetric contingency group. Results from the two variants cannot be interchanged and attributed to one model. Supplementary Note S2 lists the required differences; the release manifest records field-level differences and hashes.

# 4. Validation

No contemporaneous operator network model or branch-level state estimate was available at the spatial extent of SimPT60. Validation therefore does not target equipment-level error. It evaluates research plausibility and usability through network structure, computational consistency, sensitivity to modelling assumptions, and comparison with public observations.

## 4.1 Validation Design and Evidence Levels

Validation has three layers. Public structural records and sensitivity tests evaluate the network representation. E-REDES products withheld from nodal-power construction evaluate aggregate temporal and spatial behavior. Case completeness, convergence, and power balance evaluate internal computational consistency. Comparisons are paired by common timestamp or region without interpolation or removal of anomalous months. Ninety-five-percent confidence intervals use clustered resampling by civil day or spatial entity.

The evidence layers answer different questions. REN and E-REDES demand and generation products do not share identical accounting boundaries, and municipal or substation statistics are not line-flow truth. The target is therefore research-level plausibility and usability rather than equipment-level accuracy.

## 4.2 Structural and Computational Consistency

The full static network contains 3,783 buses, 4,943 lines, and 228 transformers. A single connected component contains 3,664 active buses and 4,787 active lines. The simple-graph cycle rank is 501; median and 95th-percentile node degrees are 2 and 4. Model-to-public-background route-km ratios at 60, 130, 150, 220, and 400 kV are 0.992, 1.090, 0.817, 0.819, and 1.033. REN aggregates provide the 150/220/400 kV backgrounds. The 60/130 kV comparisons use non-independent OSM context and cannot be interpreted as accuracy. The 130 kV ratio is based on only seven model lines and is descriptive only.

Line parameters are a principal uncertainty. Of 4,240 60 kV lines, 1,517 have partial support from PDIRD circuit paths and 2,723 use voltage-class engineering proxies. At 130--400 kV, every line is classified as proxy at the line-level `parameter_status`. This overall classification differs from field-level support, which Figure 4 reports separately.

The 11 compact monthly databases contain 31,492 completed and converged 15-minute cases. The mean absolute AC closure residual across generation, boundary exchange, load, and loss is \(1.77\times10^{-6}\) MW, showing numerical agreement among released states and audit quantities.

![Figure 4](figures_final/fig04_network_parameter_evidence.png)

**Figure 4—Structural coverage and field-level parameter evidence.** (a) Model route-km divided by public background length for each voltage class; the dashed line marks unity. Grey circles use non-independent OSM context and blue squares use REN background. Route-km and circuit length have different definitions, so the ratio is not an accuracy measure. (b) Field-level separation of project-based resistance transfer (orange; engineering assumption) and source-backed ratings (green); cells show proportions. Grey denotes wholly proxy fields. The 35.8% resistance support is not direct observation and must not be assigned the same evidence level as ratings. Reactance and capacitance are proxies throughout. The 130 kV result contains seven lines and is descriptive only. Table 8 reports line-level evidence counts.

**Table 8—Network structure, parameter evidence, and computational completeness.**

| kV | Buses | Lines | Route-km | Background km | Ratio | Line-level parameter evidence |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 60 | 3,236 | 4,240 | 9,668.4 | 9,742.0 | 0.992 | 1,517 partial / 2,723 proxy |
| 130 | 8 | 7 | 41.6 | 38.2 | 1.090 | 7 proxy |
| 150 | 132 | 156 | 2,054.1 | 2,514.0 | 0.817 | 156 proxy |
| 220 | 214 | 294 | 3,206.9 | 3,916.0 | 0.819 | 294 proxy |
| 400 | 193 | 246 | 3,579.5 | 3,465.0 | 1.033 | 246 proxy |

**Table note.** The 130 kV sample contains seven lines. Background length is non-independent OSM context at 60/130 kV and REN aggregate length at 150/220/400 kV. Route-km and circuit length differ, so ratios are not accuracy. The active network is one connected component with simple-graph cycle rank 501, median degree 2, and 95th-percentile degree 4. All 31,492 cases completed and converged; interval-weighted AC closure MAE is \(1.77\times10^{-6}\) MW. Partial line-level support does not mean that every field is observed; all \(x\) and \(c\) values are proxies.

## 4.3 Parameter and Spatial-Allocation Sensitivity

Sensitivity experiments perturb line \(R/X\), capacitance, current ratings, transformer capacity and impedance, and the spatial allocation of load, generation, and boundary exchange at 22 representative times. After shared baselines are removed, 264 cases remain and all converge. The minimum Spearman correlation is 0.998 for line-impedance perturbations and 0.993 for transformer perturbations. Current-rating assumptions change maximum loading by as much as 34.48 percentage points. System-wide rankings remain comparatively stable under spatial alternatives, but allocating residual load by transformer capacity reduces the Top-20 Jaccard index to 0.429. Specific hotspots are consequently more assumption-dependent than the global ranking.

Figure 5 summarizes ranking, hotspot-set, and maximum-loading responses. Supplementary Table S6 contains all paired results. Hotspot persistence shows that no line remains in the Top 20 across every annual time and configuration (Supplementary Figure S1). Supplementary Table S7 reports ablations of reactive-power limits, PV targets, compensation, reactors, and tap control.

![Figure 5](figures_final/fig05_parameter_spatial_sensitivity.png)

**Figure 5—Sensitivity to parameters and spatial allocation.** (a) Spearman correlation of line-loading ranks; (b) Top-20 Jaccard index; and (c) absolute change in maximum loading. Markers denote medians over 22 operating states and lines extend to the least favorable result. The 154 parameter cases and 132 spatial cases share 22 baselines.

## 4.4 External Temporal Agreement

National demand comparison contains 31,388 paired intervals. The ratio of mean SimPT60 consumption to public E-REDES consumption is 1.003, with a day-block bootstrap 95% confidence interval of [1.002, 1.005]. Fifteen-minute Pearson correlation is 0.997 [0.996, 0.999], and MAE is 30.9 MW. Across 327 paired daily means, Pearson correlation is 0.995 and normalized RMSE is 0.013. October 2025 contains E-REDES missingness and local deviations, but the month is retained ([E-REDES, n.d.-b](#ref-EREDESNationalConsumption)).

Wind comparison contains 31,484 paired intervals. The mean ratio between SimPT60 and E-REDES distribution-grid wind injection is 1.056 [1.055, 1.058], and Pearson correlation is 0.9998 [0.9997, 0.9998]. Daily-mean correlation is also 0.9998. Photovoltaic correlation is 0.982, but mean national photovoltaic production in SimPT60 is approximately 21.2 times distribution-grid injection. Hydro correlation is 0.536. The latter comparisons span different accounting scopes and are not absolute-scale validation ([E-REDES, n.d.-c](#ref-EREDESDistributionInjection); [E-REDES, n.d.-d](#ref-EREDESNationalProduction)).

![Figure 6](figures_final/fig07_temporal_validation.png)

**Figure 6—External temporal validation of aggregate inputs.** (a--b) Paired daily means for national consumption and wind; (c--d) density of the corresponding 31,388 and 31,484 15-minute pairs, with a shared logarithmic count scale, one-to-one line, and Pearson \(r\); and (e) consumption profiles grouped by Lisbon local time and normalized by each series' daily mean. Comparisons exclude only intervals missing a required field and use no anomaly deletion, smoothing, or interpolation. Consumption excludes pumping and battery charging and differs from total electric load in the cases. Wind compares national production with distribution-grid injection across different scopes. The figure validates aggregate inputs, not nodal allocation or branch flow. Table 9 gives bootstrap intervals.

## 4.5 External Spatial Agreement

At municipal level, E-REDES monthly billed consumption is the reference ([E-REDES, n.d.-e](#ref-EREDESMunicipalityConsumption)). The 2,183 municipality-month pairs yield a Spearman correlation of 0.881, with a 95% confidence interval of [0.841, 0.910] from 199 municipality clusters; they cover 94.8% of contemporaneous billed energy. At substation level, 792 seasonal pairs yield a Spearman correlation of 0.957 [0.946, 0.967] over 397 station clusters, a mean ratio of 1.044, and MAE of 2.45 MW.

Municipality comparisons use the municipality containing each substation as a proxy for its service area and therefore provide weak spatial evidence. Seasonal substation loads come from the E-REDES capacity and loading product ([E-REDES, n.d.-a](#ref-EREDESSubstationCapacity)). They test facility ranking and scale, but share the same operator as the 15-minute load data and are not fully independent nodal truth.

![Figure 7](figures_final/fig08_spatial_validation.png)

**Figure 7—Spatial agreement at municipality and substation levels.** (a) 2,183 municipality-month load-share pairs; (b) 792 seasonal substation-peak pairs. Axes have equal scale and dashed one-to-one lines. Labels report Spearman correlation and archived 1,000-replicate cluster-bootstrap 95% intervals using 199 municipality and 397 substation clusters; bands are not regression intervals. Municipality assignment is a weak service-area proxy, and cross-product evidence from the same operator is not fully independent nodal truth.

**Table 9—Summary of cross-source validation.**

| Validation target | Samples or independent blocks | Result (95% CI) | Evidence level |
| --- | ---: | --- | --- |
| National consumption | 31,388 intervals / 327 days | Mean ratio 1.003 [1.002, 1.005]; \(r=0.997\) [0.996, 0.999] | REN--E-REDES consumption; reference includes losses |
| Wind | 31,484 intervals / 328 days | Mean ratio 1.056 [1.055, 1.058]; \(r=0.9998\) [0.9997, 0.9998] | REN--E-REDES distribution-injection corroboration |
| Photovoltaic temporal variation | 31,484 intervals / 328 days | \(r=0.982\) [0.979, 0.984]; absolute scale not comparable | National production versus distribution injection |
| Municipal load share | 2,183 pairs / 199 municipalities | \(\rho=0.881\) [0.841, 0.910] | Same operator across datasets; weak spatial proxy |
| Seasonal substation peak | 792 pairs / 397 stations | \(\rho=0.957\) [0.946, 0.967] | Same operator across datasets |
| AC power closure | 31,492 cases | MAE \(1.77\times10^{-6}\) MW | Internal physical consistency |

### 4.5.1 Station-Held-Out Spatial Reconstruction Test

A station-held-out experiment divides 394 matched stations into five deterministic folds. At 22 times, it compares global capacity share, five geographically nearest stations, and five nearest stations along the reconstructed network, yielding 8,541 observation pairs. Table 10 shows that network-neighbor MAE is approximately 9.9% below global capacity share, but is not lower than geographic-neighbor MAE and has an overlapping confidence interval. Reconstructed connectivity therefore contains useful proximity information but does not independently recover precise nodal demand. Supplementary Note S3 specifies folds, distance weights, and bootstrap procedures.

**Table 10—Station-held-out spatial-allocation results.**

| Method | Stations / observation pairs | MAE (MW), 95% CI | RMSE (MW) | WAPE |
| --- | ---: | --- | ---: | ---: |
| Capacity share | 394 / 8,541 | 4.729 [4.412, 5.063] | 6.357 | 38.4% |
| Five geographic neighbors | 394 / 8,541 | 4.249 [3.972, 4.544] | 5.675 | 34.5% |
| Five network-path neighbors | 394 / 8,541 | 4.262 [3.971, 4.545] | 5.702 | 34.6% |

## 4.6 Generation Scope and Quantified Dependence on Spatial Proxies

Directly observed load accounts for 67.93--74.93% of monthly consumption; the remaining 25.07--32.07% is allocated with PDIRT reference profiles. National generation proxies account for only 0.010--0.063% of monthly generation, but this national average hides small technology classes. In May 2025, proxy shares reach 90.58% for batteries and 11.07% for other thermal generation. Aggregate balance therefore does not establish reliable local asset location.

All non-rounding generation proxies are injected at one 400 kV receiving bus, which represents a model balancing location only. Figure 8 shows consumption residuals, technology-level generation proxies, and their spatial distribution by latitude band. Monthly values for eight technologies, interval flags, and bus-level details are released as CSV.

![Figure 8](figures_final/fig10_proxy_provenance.png)

**Figure 8—Temporal and spatial distribution of observed and proxy power.** (a) Observed consumption and the PDIRT-allocated residual; (b) technology-level proxy shares for batteries and other thermal generation; and (c) load-residual share by latitude band of receiving buses. Panels use different denominators. Regions denote allocation locations in the model, not the true locations of missing assets.

## 4.7 Limitations

Validation supports aggregate temporal agreement, plausible spatial ranking, and numerical self-consistency. Parameter and allocation perturbations also show that system-wide trends are generally stable. Public information remains insufficient to validate device-by-device topology, branch flows, switching status, or generator capability. Correlation, convergence, and screening counts must therefore not be interpreted as equipment-level accuracy of an operator network. SimPT60 is intended for reproducible research cases, relative-risk comparison, and method testing. Real-time dispatch, protection settings, and formal compliance assessment require contemporaneous operator-approved models and operating records.

# 5. Application

Two compact experiments demonstrate deterministic scenario comparison and batch contingency computation. They illustrate model use and are neither operational forecasts nor formal compliance assessments.

## 5.1 Deterministic Grid-Stress Scenario

Starting from 2026-01-20 19:45 UTC, nodal load, generation by technology, and boundary exchange are scaled jointly from 0.90 to 1.15 and solved independently. All five scenarios converge. Maximum line loading is 96.10% at baseline, reaches 100.50% with three overloaded lines at 1.05, and reaches 109.41% at 1.15. Minimum voltage remains above 0.90 p.u. Figure 9 shows loading and voltage responses; exact scenario results are released as CSV.

![Figure 9](figures_final/fig11_grid_stress.png)

**Figure 9—Deterministic grid-stress response.** (a) Maximum line and transformer loading; (b) minimum and maximum bus voltage. Dashed lines mark 100% line loading and the 0.90--1.10 p.u. screening band. Connecting lines guide the eye only.

## 5.2 Representative-Time Full-Element N−1 Demonstration

Six times are selected in advance from the continuous series: maximum and minimum load, maximum wind, maximum photovoltaic generation, maximum net import, and maximum net export. The same 1,649 contingencies are used at each time in N1-3787: 1,421 line-circuit groups and 228 transformer groups. Series model segments are removed together, while one unit is removed from a parallel equivalent. Supplementary Table S8 specifies selection and exclusion rules.

The six times produce 9,894 cases, of which 9,888 converge under the principal solution. Absolute screening passes 6,048 cases, and 9,064 cases have neither a new violation relative to N−0 nor a material island. The latter is an auxiliary diagnostic; it does not identify every worsening of a pre-existing violation and does not replace absolute criteria. Figure 10 and Table 11 report the results; complete membership and case-level outputs are preserved in the repository.

![Figure 10](figures_final/fig12_annual_nminus1.png)

**Figure 10—Full-element N−1 screening at representative times.** (a) Absolute and incremental diagnostics; (b) material islands, new violations, and principal-solution failures; (c--d) elements associated with the highest post-contingency line loading and largest disconnected load. Supplementary Note S4 defines the complete criteria and quantifies worsening of pre-existing violations.

**Table 11—Full-element N−1 results at six representative times.**

| Snapshot | Cases | Principal solution converged | Absolute screen passed | No new violation and no material island | Material island |
| --- | ---: | ---: | ---: | ---: | ---: |
| Maximum net export | 1,649 | 1,649 | 1,527 | 1,527 | 114 |
| Maximum net import | 1,649 | 1,648 | 1,455 | 1,455 | 102 |
| Maximum photovoltaic | 1,649 | 1,649 | 1,526 | 1,526 | 109 |
| Maximum wind | 1,649 | 1,647 | 0 | 1,502 | 115 |
| Minimum load | 1,649 | 1,649 | 1,538 | 1,538 | 110 |
| Maximum load | 1,649 | 1,646 | 2 | 1,516 | 110 |
| Total | 9,894 | 9,888 | 6,048 | 9,064 | 660 |

**Table note.** Absolute pass, no-new-violation/no-material-island, and material-island columns are computed separately and are not mutually exclusive and exhaustive classes. At maximum net export, eight additional non-islanding cases have new violations: seven line-thermal and one voltage violation. Together with 1,527 no-new-violation cases and 114 material islands, they account for all 1,649 cases.

These applications support relative scenarios and diagnostic contingency screening. Equipment-level interpretation remains limited by the parameter, status, and control evidence summarized in Section 4.7.

# 6. Conclusions

SimPT60 links a public-data reconstruction of the continental Portuguese 60--400 kV network to 31,492 consecutive 15-minute AC power-flow states in one traceable, versioned product. CORE-3783 contains 3,783 buses, 4,943 lines, and 228 transformer objects, connected to source records, allocation rules, and solution outputs by stable identifiers.

Validation supports aggregate spatiotemporal agreement and numerical self-consistency. National load and wind correlations are approximately 0.997 and 0.9998. Spearman correlations for municipal and substation spatial rankings are 0.881 and 0.957. The station-held-out experiment shows that network proximity improves on global capacity allocation but not on geographic proximity. System-wide rankings are generally stable under parameter and allocation perturbations, whereas individual hotspots and absolute loading remain sensitive to proxy assumptions.

The grid-stress scenarios and 9,894 N−1 cases at six representative times demonstrate support for scenario analysis and batch contingency computation. These results compare model states and identify elements for further investigation; they do not constitute security certification.

SimPT60 is a public-data-derived research dataset. It is not an operator internal model, state estimate, or digital twin. Equipment-level validation will require contemporaneous device status, generator P--Q capability, switching configuration, and post-contingency actions.

------

# Data Availability

The frozen **SimPT60-2026.09.21-r1** release is available from the [project's versioned GitHub release](https://github.com/MingLeiZhou/PortugueseOPD/releases/tag/SimPT60-2026.09.21-r1). It contains the reproducibility core, 11 compressed monthly result databases, the monthly manifest, `release.json`, and `SHA256SUMS`. Raw third-party files are redistributed only when their licences permit; otherwise, the release retains source URLs, archived metadata, file sizes, fingerprints, and retrieval instructions. The release manifest identifies the CORE-3783 and N1-3787 model variants and their permitted uses.

# Code Availability

Reproduction code, the locked Python environment, and the minimal replay commands are included in the same [SimPT60-2026.09.21-r1 release](https://github.com/MingLeiZhou/PortugueseOPD/releases/tag/SimPT60-2026.09.21-r1). The immutable tag fixes the release state, while `release.json` and `CODE_VERSION.json` record the implementation commit and the limitation that the original historical monthly runs did not store a code commit. The replay workflow verifies selected released cases at a tolerance of \(10^{-5}\) in the units of each compared field.

------

# References

<a id="ref-APA3403"></a>

APA (2021). [Sobreequipamento do Parque Eólico de Trancoso: Parecer da Comissão de Avaliação, AIA 3403](https://siaia.apambiente.pt/AIADOC/AIA3403/parecerca_3403202192134944.pdf). July; PDF p. 5.

<a id="ref-APAPPA421"></a>

APA (n.d.-a). [PPA 421: Sub-Parque Eólico de Sernancelhe e ligação a Moimenta](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/421). AIA 2009; project-name and operation-date fields; archived 2026-09-21.

<a id="ref-APAPPA407"></a>

APA (n.d.-b). [PPA 407: Ligação do Douro Sul à Subestação de Armamar e Subestação de Moimenta](https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/407). AIA 2009; archived 2026-09-21.

<a id="ref-DGEGPNEC2030Revision2024"></a>

DGEG (2024). [Portugal: Plano Nacional Energia e Clima 2021–2030, atualização/revisão](https://www.dgeg.gov.pt/media/54fldci3/pnec2030_para_aprov_ar.pdf). DGEG.


<a id="ref-DGEGGeo2026"></a>

DGEG (n.d.). [Informação Geográfica de Energia Elétrica](https://www.dgeg.gov.pt/pt/servicos-online/setor-energetico/). Official geographic data portal. Accessed 2026-09-21.

<a id="ref-Tocha2019"></a>

EDP Distribuição (2019). [Memória Descritiva e Justificativa: Linha a 60 kV PE Tocha II–Tocha](https://siaia.apambiente.pt/AIADOC/AIA3274/projeto%20linha%20eletrica%20pe%20tocha%20ii2019729153214.pdf). 19 February; process 2800-19C007374; PDF pp. 3, 5.

<a id="ref-EREDESPDIRD2020AnnexB"></a>

E-REDES; Entidade Reguladora dos Serviços Energéticos (2020). [PDIRD-E 2020, Anexo B](https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf). ERSE. July version; northern circuit records: PDF pp. 93–95.

<a id="ref-EREDESSubstationCapacity"></a>

E-REDES (n.d.-a). [Carga na subestação](https://e-redes.opendatasoft.com/explore/dataset/carga-na-subestacao/). E-REDES Open Data Portal. Accessed 2026-09-21.

<a id="ref-EREDESNationalConsumption"></a>

E-REDES (n.d.-b). [Consumo total nacional](https://e-redes.opendatasoft.com/explore/dataset/consumo-total-nacional/). E-REDES Open Data Portal. Accessed 2026-09-21.

<a id="ref-EREDESDistributionInjection"></a>

E-REDES (n.d.-c). [Energia injetada na rede de distribuição](https://e-redes.opendatasoft.com/explore/dataset/energia-injetada-na-rede-de-distribuicao/). E-REDES Open Data Portal. Accessed 2026-09-21.

<a id="ref-EREDESNationalProduction"></a>

E-REDES (n.d.-d). [Energia produzida total nacional](https://e-redes.opendatasoft.com/explore/dataset/energia-produzida-total-nacional/). E-REDES Open Data Portal. Accessed 2026-09-21.

<a id="ref-EREDESMunicipalityConsumption"></a>

E-REDES (n.d.-e). [Monthly consumption by municipality](https://e-redes.opendatasoft.com/explore/dataset/3-consumos-faturados-por-municipio-ultimos-10-anos/). E-REDES Open Data Portal. Accessed 2026-09-21.

<a id="ref-EREDESRND2026"></a>

E-REDES (n.d.-f). [Rede Nacional de Distribuição: Dados da Rede AT e Cargas de Subestação](https://e-redes.opendatasoft.com/pages/rnd/). Open data portal. Accessed 2026-09-21.

<a id="ref-GISCO2024"></a>

Eurostat GISCO (2024). [Countries 2024: Portugal Boundary, 1:1 Million, EPSG:4326](https://gisco-services.ec.europa.eu/distribution/v2/countries/). Geospatial dataset. Accessed 2026-09-21.

<a id="ref-GeoPandas2026"></a>

Fleischmann, Martin; Van den Bossche, Joris; Jordahl, Kelsey; Richards, Matthew John; McBride, James; Wasserman, Jacob; Ward, Brendan; Wolf, Levi John (2026). [GeoPandas: Fundamental Data Structures for Vector Spatial Data in Python](https://doi.org/10.1016/j.compenvurbsys.2026.102495). *Computers, Environment and Urban Systems*, 130, 102495. Author/year/DOI verified against the official GeoPandas CITATION.md and Crossref on 2026-09-21; assigned issue December 2026, not asserted as the online publication date.

<a id="ref-GeofabrikPortugal2026"></a>

Geofabrik GmbH (n.d.). [OpenStreetMap Data Extract for Portugal](https://download.geofabrik.de/europe/portugal.html). Geospatial data extract. Accessed 2026-09-21.

<a id="ref-Horsch2018"></a>

Hörsch, Jonas; Hofmann, Fabian; Schlachtberger, David; Brown, Tom (2018). [PyPSA-Eur: An Open Optimisation Model of the European Transmission System](https://doi.org/10.1016/j.esr.2018.08.012). *Energy Strategy Reviews*, 22, 207-215.

<a id="ref-Meinecke2020"></a>

Meinecke, Steffen; Sarajlić, Džanan; Drauz, Simon Ruben; Klettke, Annika; Lauven, Lars-Peter; Rehtanz, Christian; Moser, Albert; Braun, Martin (2020). [SimBench—A Benchmark Dataset of Electric Power Systems to Compare Innovative Solutions Based on Power Flow Analysis](https://doi.org/10.3390/en13123290). *Energies*, 13(12), 3290.

<a id="ref-OpenInfraMap2026"></a>

OpenInfraMap contributors (n.d.). [OpenInfraMap](https://openinframap.org/about). Web map of infrastructure data derived from OpenStreetMap. Accessed 2026-09-21.

<a id="ref-OSM2026"></a>

OpenStreetMap contributors (n.d.). [OpenStreetMap](https://www.openstreetmap.org/copyright). Collaborative geospatial database. Accessed 2026-09-21.

<a id="ref-REE2012"></a>

Red Eléctrica de España (2012). [Interconexiones eléctricas: un paso para el mercado único de la energía en Europa](https://www.ree.es/sites/default/files/jgk4byy3ukct.pdf). September; PDF p. 10.

<a id="ref-REN2015Estoi"></a>

REN (2015). [PDIRT 2016–2025, Anexo 6: Equipamento em serviço previsto em finais de 2016, 2018, 2020 e 2025](https://www.erse.pt/media/b1edmm30/proposta_pdirt_e_2015_anexos.pdf). Proposal archive; PDF pp. 50, 52, 54, 56. Year follows archive label, not a newly inferred issue date.

<a id="ref-ERSEPDIRT2024"></a>

REN (2024). [PDIRT 2025–2034, Proposta Inicial, Volume I, Anexos 1 a 16](https://www.erse.pt/media/lx5n5kao/pdirt-2025-2034-proposta-inicial-vol-i-anexos-1-a-16.pdf). ERSE (public consultation archive). December 2024 proposal; archived by ERSE.

<a id="ref-RENDataHub2026"></a>

REN (n.d.). [Electrical Grid Data Hub](https://datahub.ren.pt/en/networks/electrical-grid/). Official data portal. Accessed 2026-09-21.

<a id="ref-Thurner2018"></a>

Thurner, Leon; Scheidler, Alexander; Schäfer, Florian; Menke, Jan-Hendrik; Dollichon, Julian; Meier, Friederike; Meinecke, Steffen; Braun, Martin (2018). [pandapower—An Open-Source Python Tool for Convenient Modeling, Analysis, and Optimization of Electric Power Systems](https://doi.org/10.1109/TPWRS.2018.2829021). *IEEE Transactions on Power Systems*, 33(6), 6510-6521.

<a id="ref-Xiong2025"></a>

Xiong, Bobby; Fioriti, Davide; Neumann, Fabian; Riepin, Iegor; Brown, Tom (2025). [Modelling the High-Voltage Grid Using Open Data for Europe and Beyond](https://doi.org/10.1038/s41597-025-04550-7). *Scientific Data*, 12(1), 277.
