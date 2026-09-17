"""Run after installing pt60-tools; set PT60_DATA outside the source checkout."""
from pt60 import Dataset

dataset = Dataset()
print(dataset.verify())
case = dataset.cases("SUMMER")[0]
fields = dataset.results(case["case_id"])
voltages = [v for v in fields["bus_voltage"].values() if v is not None]
print(case["case_id"], min(voltages), max(voltages))
