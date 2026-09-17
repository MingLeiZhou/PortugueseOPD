# PT60 public-data modelling interface

This guide shows how to turn one timestamped public-data bundle into a solved
PT60 AC power-flow case. Run every command from the extracted candidate-package
root.

## 1. Install and copy the example

Use Python 3.13 and install the declared runtime dependencies:

```bash
python -m pip install -r requirements.txt
cp -R examples/public_case public_case
```

The copied directory is a working example. Replace its observations while
keeping the file contract described below.

## 2. Supply the inputs

The interface combines four data objects:

- `model/model_template.json` is the compiled network `N`: buses, branches,
  transformers, boundaries and fixed devices;
- `scenario/generators.csv` is the mapped generation and storage inventory `A`;
- `public_case/scenario.json` and `public_case/loads.csv` provide the timestamped
  operating bundle `I(t)`;
- the archived PDIRT delivery-point table inside `reproduction/` provides the
  spatial allocation reference `W` for national demand not covered by facility
  observations.

`I(t)` is divided by role. `O(t)` supplies national demand, storage demand,
generation by source and facility load observations. `kappa(t)` supplies the
case timestamp, load-observation timestamp, seasonal line-rating factor and the
state of the dated Ponte de Lima–Fontefría interconnector. Imports, exports and
the monthly RNT loss percentage form held-out `V(t)`: the solver does not use
them to set generation or boundary flows; it compares them with the result after
solving.

The input directory must contain:

- `scenario.json`: case identity, timestamps, national operating totals and
  source URLs;
- `loads.csv`: one facility-level active-power observation per public
  substation.

It may also contain:

- `generator_dispatch.csv`: observed unit-level active generation;
- `storage_loads.csv`: observed pumping or battery demand by mapped bus;
- `line_overrides.csv`: source-backed parameters for named lines.

The machine-readable JSON contract is in
[`config/public_input_schema.json`](config/public_input_schema.json).

### `scenario.json`

The included January example shows every required field. Its main structure is:

```json
{
  "schema_version": "1.0",
  "case_id": "PT60_PUBLIC_EXAMPLE",
  "timestamp_utc": "2026-01-20T19:45:00+00:00",
  "load_profile_timestamp_utc": "2026-01-20T19:45:00+00:00",
  "load_profile_mode": "SUPPLIED_PUBLIC_DATA",
  "rating_factor_relative_to_static_summer": 1.15,
  "new_interconnector_in_service": false,
  "rnt_loss_benchmark_date": "2026-01-31",
  "national": {
    "consumption_mw": 10268.8,
    "consumption_plus_storage_mw": 10268.8,
    "pumping_mw": 0.0,
    "battery_consumption_mw": 0.0,
    "import_mw": 406.6,
    "export_mw": 0.0,
    "generation_by_source_mw": {
      "Hydro": 5337.9,
      "Solar": 10.6,
      "Wind": 2896.3,
      "Natural Gas": 1234.0,
      "Other Thermal": 28.7,
      "Biomass": 355.6,
      "Wave": 0.0,
      "Battery Injection": 4.6
    },
    "rnt_monthly_loss_percent": 2.36
  },
  "sources": {
    "national_balance": "https://servicebus.ren.pt/...",
    "substation_load": "https://e-redes.opendatasoft.com/...",
    "rnt_monthly_loss": "https://datahub.ren.pt/..."
  }
}
```

All timestamps identify a 15-minute interval **start** and must include a time zone. The published cases use UTC. Source acquisition converts REN Europe/Lisbon start labels and E-REDES Europe/Lisbon end labels to the same interval; ambiguous autumn E-REDES repeated labels are rejected. National
values are MW, except `rnt_monthly_loss_percent`.

### `loads.csv`

| field | required | meaning |
|---|---:|---|
| `facility_code` | yes | E-REDES or other public facility identifier |
| `substation_name` | yes | human-readable facility name |
| `p_mw` | yes | mean active power for the observation interval |
| `observation_status` | no | `OBSERVED` (default), `MISSING`, or `PARTIAL_OBSERVATION`; missing `p_mw` must be blank |
| `q_mvar` | no | observed or derived reactive power; fixed 0.97 power factor is used when absent |

The archived validation JSON preserves E-REDES's original 15-minute `energia`
field in kWh for provenance. The archived-case replay converts it at the start
of case construction with `p_mw = energia_kwh / 250`. The public interface
expects that conversion to have been made in `loads.csv`.

Missing observations retain a blank raw power and an explicit audit flag. They add no local observed injection; their contribution remains inside the national residual allocated across PDIRT delivery points. A numeric zero is an observed value. This is not station-specific imputation. Counts of missing records and facilities are exported separately.

### Optional tables

`generator_dispatch.csv` uses `generator_id,p_mw,dispatch_mode`. `dispatch_mode` defaults to `FIXED`: the supplied value is preserved and the asset is excluded from further allocation. Use `SEED` explicitly when the value is only an initial allocation that may increase. A supplied unit value must
refer to an available mapped asset and stay within its nameplate. The remaining
public total for that source is allocated to unused capacity in the same source
category, excluding fixed assets. The source balance exports any remaining unallocated generation.

`storage_loads.csv` uses `storage_type,bus_id,p_mw`, where `storage_type` is
`PUMPING` or `BATTERY_CHARGING`. Partial observations are allowed; the remaining
national storage total follows the archived capacity-weighted rule.

`line_overrides.csv` uses a unique `line_id` and any of
`r_ohm_per_km,x_ohm_per_km,c_nf_per_km,static_summer_max_i_ka`, with a
recommended `source_url`.

## 3. Build and solve the case

```bash
python reproduction/portuguese_hv_network/src/pt60_public_model.py \
  --input-dir public_case \
  --output-dir output/public-case \
  --model model/model_template.json \
  --generators scenario/generators.csv
```

Archived validation replay follows Figure 2 from the retained 15-minute kWh
records. This public-case interface accepts facility mean power in MW, so its
input validation has already crossed the `kWh / 250` conversion shown in Figure
2. From the combination stage onward it uses the same sequence:

1. validate timestamps, units, totals, identifiers and optional overrides;
2. copy `N`, then apply the dated interconnector state and seasonal line ratings
   from `kappa(t)`;
3. select the assets in `A` that are available at the case date, rebuild the
   bus-level PV/PQ controls, and apply any supported line overrides;
4. map facility observations in `O(t)` to buses through `facility_code`;
5. allocate the uncovered national demand with `W`, add pumping and battery
   demand, and derive load reactive power and compensation;
6. allocate each national generation-source total to available assets under
   nameplate limits and write it to the rebuilt control elements, completing the
   dated bus `P/Q` and therefore `C(t)`;
7. verify load and generation conservation before the first power flow;
8. run Newton-Raphson, construct and check the boundary equivalent, repeat the
   solve and iterate transformer taps;
9. derive `Y(t)`, compare its aggregate exchange and losses with held-out
   `V(t)`, and write the solved model, result tables and audit records.

## 4. Read the outputs

The output directory contains:

- `<case_id>_solved.json`, which stores the solved case state and embedded
  pandapower result tables;
- `bus_results.csv`, `line_results.csv`, `transformer_results.csv` and
  `generator_results.csv`;
- `asset_allocation.csv`, retaining each asset’s supplied value, dispatch mode, final allocation, increment, availability and many-to-one `gen`/`sgen` mapping;
- `load_mapping_audit.csv`, `generation_source_balance.csv`,
  `line_loading_hotspots.csv`, `modeled_boundary_flows.csv`,
  `cross_border_evidence.csv` and `loss_comparison.csv`;
- `summary.json` for case-level results and `run_manifest.json` for input hashes
  and output inventory.

The modeled Portugal-Spain balance is derived from the solved case. Aggregate
public exchange is retained as comparison evidence, and per-circuit boundary
values remain model allocations.

## 5. Replay archived validation cases

To reproduce the archived case-level summaries:

```bash
python paper/scripts/run_seasonal_validation.py \
  --pilot --output-dir output/replay-pilot

python paper/scripts/run_seasonal_validation.py \
  --workers 3 --output-dir output/replay-full
```

To write the complete solved model and device tables for one of the 336 archived
inputs:

```bash
python paper/scripts/replay_archived_validation_case.py \
  --case-id PT60_2025_SUMMER_WEEK_JUL07_13_H000 \
  --output-dir output/case-H000
```

The separately packaged January example can be replayed with:

```bash
python paper/scripts/replay_pt60_january.py \
  --output-dir output/january-example
```
