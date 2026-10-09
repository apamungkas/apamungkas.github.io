"""Build the eh_dt_factory KQL database: schema, dimensions, 7-day telemetry history, functions, MV, OneLake availability.

Usage: python build_eventhouse.py [--skip-history] [--fill-gap]
  --fill-gap  append synthetic history from the latest Timestamp in MachineTelemetry up to now (no duplicates).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import ids, kusto  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KQL_DIR = os.path.join(ROOT, "kql")
MD = json.load(open(os.path.join(ROOT, "data", "master_data.json"), encoding="utf-8"))

# Absolute scenario anchors shared with the live simulator (UTC).
SCENARIO = {
    "wearMachine": "M-CKR-L2-04", "wearStart": "2026-10-05T15:00:00Z", "wearDays": 3,
    "overheatMachine": "M-SBY-L1-01", "overheatStart": "2026-10-08T09:00:00Z", "overheatMinuteFrom": 8, "overheatMinuteTo": 24,
    "faultMachine": "M-KRW-L2-03", "blockedMachines": ["M-KRW-L2-01", "M-KRW-L2-02"],
    "faultStart": "2026-10-07T07:00:00Z", "faultEnd": "2026-10-07T07:45:00Z",
}


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def num(v):
    return "real(null)" if v is None else repr(float(v))


SCHEMA = r"""
.create-merge table MachineTelemetry (Timestamp: datetime, SiteId: string, LineId: string, MachineId: string, MachineType: string, Status: string, TemperatureC: real, VibrationMmS: real, PressureBar: real, PowerKw: real, Rpm: real, UnitsProduced: int, UnitsRejected: int)
.create-merge table DimSite (SiteId: string, SiteName: string, Area: string, City: string, Province: string, Country: string, Latitude: real, Longitude: real, Timezone: string, PlantManager: string, Employees: int, OpenedYear: int)
.create-merge table DimLine (LineId: string, SiteId: string, LineName: string, LineType: string, TargetUnitsPerHour: int)
.create-merge table DimMachine (MachineId: string, MachineName: string, MachineType: string, LineId: string, SiteId: string, Manufacturer: string, Model: string, SerialNumber: string, InstallDate: datetime, RatedPowerKw: real, NominalRpm: real, TempMaxC: real, VibMaxMmS: real, Criticality: string)
.create-or-alter table MachineTelemetry ingestion json mapping "MachineTelemetryJson" '[{"column":"Timestamp","path":"$.Timestamp","datatype":"datetime"},{"column":"SiteId","path":"$.SiteId","datatype":"string"},{"column":"LineId","path":"$.LineId","datatype":"string"},{"column":"MachineId","path":"$.MachineId","datatype":"string"},{"column":"MachineType","path":"$.MachineType","datatype":"string"},{"column":"Status","path":"$.Status","datatype":"string"},{"column":"TemperatureC","path":"$.TemperatureC","datatype":"real"},{"column":"VibrationMmS","path":"$.VibrationMmS","datatype":"real"},{"column":"PressureBar","path":"$.PressureBar","datatype":"real"},{"column":"PowerKw","path":"$.PowerKw","datatype":"real"},{"column":"Rpm","path":"$.Rpm","datatype":"real"},{"column":"UnitsProduced","path":"$.UnitsProduced","datatype":"int"},{"column":"UnitsRejected","path":"$.UnitsRejected","datatype":"int"}]'
.alter table MachineTelemetry policy streamingingestion enable
.alter-merge table MachineTelemetry policy retention softdelete = 365d recoverability = enabled
""".strip()


def dims_commands():
    s = MD["sites"]
    site_rows = ",\n".join(", ".join([q(x["SiteId"]), q(x["SiteName"]), q(x["Area"]), q(x["City"]), q(x["Province"]), q(x["Country"]),
                                     num(x["Latitude"]), num(x["Longitude"]), q(x["Timezone"]), q(x["PlantManager"]),
                                     str(int(x["Employees"])), str(int(x["OpenedYear"]))]) for x in s)
    line_rows = ",\n".join(", ".join([q(x["LineId"]), q(x["SiteId"]), q(x["LineName"]), q(x["LineType"]), str(int(x["TargetUnitsPerHour"]))]) for x in MD["lines"])
    mach_rows = ",\n".join(", ".join([q(m["MachineId"]), q(m["MachineName"]), q(m["MachineType"]), q(m["LineId"]), q(m["SiteId"]), q(m["Manufacturer"]),
                                     q(m["Model"]), q(m["SerialNumber"]), f"datetime({m['InstallDate']})", num(m["RatedPowerKw"]), num(m["NominalRpm"]),
                                     num(m["TempMaxC"]), num(m["VibMaxMmS"]), q(m["Criticality"])]) for m in MD["machines"])
    return [
        ".set-or-replace DimSite <| datatable(SiteId: string, SiteName: string, Area: string, City: string, Province: string, Country: string, Latitude: real, Longitude: real, Timezone: string, PlantManager: string, Employees: int, OpenedYear: int)[\n" + site_rows + "\n]",
        ".set-or-replace DimLine <| datatable(LineId: string, SiteId: string, LineName: string, LineType: string, TargetUnitsPerHour: int)[\n" + line_rows + "\n]",
        ".set-or-replace DimMachine <| datatable(MachineId: string, MachineName: string, MachineType: string, LineId: string, SiteId: string, Manufacturer: string, Model: string, SerialNumber: string, InstallDate: datetime, RatedPowerKw: real, NominalRpm: real, TempMaxC: real, VibMaxMmS: real, Criticality: string)[\n" + mach_rows + "\n]",
    ]


def history_command(start_expr, end_expr):
    P = MD["profiles"]
    rows = ",\n".join(", ".join([q(m["MachineId"]), q(m["SiteId"]), q(m["LineId"]), q(m["MachineType"])] +
                                [num(P[m["MachineType"]][k]) for k in ("BaseTempC", "TempLoadGainC", "BaseVibMmS", "BasePressureBar", "RatedPowerKw", "NominalRpm", "UnitsPerMin", "RejectRate")])
                      for m in MD["machines"])
    sc = SCENARIO
    blocked = ", ".join(q(x) for x in sc["blockedMachines"])
    return f""".set-or-append MachineTelemetry with (tags='["seed:history"]') <|
let startT = {start_expr};
let endT = {end_expr};
let wearStart = datetime({sc['wearStart']});
let overheatStart = datetime({sc['overheatStart']});
let faultStart = datetime({sc['faultStart']});
let faultEnd = datetime({sc['faultEnd']});
let M = datatable(MachineId: string, SiteId: string, LineId: string, MachineType: string, BaseTempC: real, TempLoadGainC: real, BaseVibMmS: real, BasePressureBar: real, RatedPowerKw: real, NominalRpm: real, UnitsPerMin: real, RejectRate: real)[
{rows}
];
range Timestamp from startT to endT - 1m step 1m
| extend _k = 1
| join kind=inner (M | extend _k = 1) on _k
| project-away _k, _k1
| extend LocalHour = hourofday(Timestamp + 7h), IsSunday = dayofweek(Timestamp + 7h) == 0d
| extend Load = iff(LocalHour >= 6 and LocalHour < 22, 0.88, 0.64) * iff(IsSunday, 0.6, 1.0) + (rand() - 0.5) * 0.06
| extend Status = iff(rand() < 0.004, "Idle", "Running")
| extend Wear = iff(MachineId == {q(sc['wearMachine'])} and Timestamp > wearStart, min_of(1.0, todouble((Timestamp - wearStart) / 1m) / ({sc['wearDays']} * 1440.0)), 0.0)
| extend Overheat = iff(MachineId == {q(sc['overheatMachine'])} and Timestamp >= overheatStart and datetime_part("minute", Timestamp) between ({sc['overheatMinuteFrom']} .. {sc['overheatMinuteTo']}), 1.0, 0.0)
| extend InFault = Timestamp >= faultStart and Timestamp < faultEnd
| extend Status = case(InFault and MachineId == {q(sc['faultMachine'])}, "Fault", InFault and MachineId in ({blocked}), "Blocked", Status)
| extend Active = iff(Status == "Running", 1.0, 0.0)
| extend TemperatureC = BaseTempC + TempLoadGainC * Load * Active + 6.0 * Wear + 27.0 * Overheat + (rand() - 0.5) * 2.0 - iff(Active == 0.0, 6.0, 0.0)
| extend VibrationMmS = iff(Active == 1.0, BaseVibMmS * (0.8 + 0.4 * Load) + 6.0 * pow(Wear, 2.0) + 0.6 * Overheat + (rand() - 0.5) * 0.35, 0.05 + rand() * 0.1)
| extend PressureBar = iff(Active == 1.0, BasePressureBar * (0.97 + 0.06 * rand()) - iff(MachineType == "AirCompressor", 0.4 * Wear, 0.0), BasePressureBar * 0.1)
| extend PowerKw = iff(Active == 1.0, RatedPowerKw * (0.35 + 0.55 * Load) * (1.0 + 0.08 * Wear) + (rand() - 0.5) * RatedPowerKw * 0.03, RatedPowerKw * 0.04)
| extend Rpm = iff(Active == 1.0, NominalRpm * (0.96 + 0.06 * Load) + (rand() - 0.5) * NominalRpm * 0.01, 0.0)
| extend Units = UnitsPerMin * Active * (0.75 + 0.3 * Load) * (1.0 - 0.15 * Overheat) + iff(UnitsPerMin > 0.0, (rand() - 0.5) * 2.0, 0.0)
| extend UnitsProduced = toint(round(max_of(Units, 0.0)))
| extend UnitsRejected = toint(round(UnitsProduced * (RejectRate + 0.11 * Overheat) * (0.5 + rand())))
| project Timestamp, SiteId, LineId, MachineId, MachineType, Status, TemperatureC = round(TemperatureC, 2), VibrationMmS = round(VibrationMmS, 3),
          PressureBar = round(PressureBar, 2), PowerKw = round(PowerKw, 2), Rpm = round(Rpm, 1), UnitsProduced, UnitsRejected"""


FUNCTIONS = [
    r""".create-or-alter function with (folder="DigitalTwin", docstring="Latest reading per machine with asset context and health state") MachineCurrentState() {
MachineLatestState
| join kind=leftouter DimMachine on MachineId
| join kind=leftouter (DimLine | project LineId, LineName, LineType) on LineId
| join kind=leftouter (DimSite | project SiteId, SiteName, City, Latitude, Longitude) on SiteId
| extend MinutesSinceLastReading = datetime_diff("minute", now(), Timestamp)
| extend HealthState = case(
    MinutesSinceLastReading > 15, "Offline",
    Status == "Fault", "Critical",
    TemperatureC > TempMaxC or VibrationMmS > VibMaxMmS, "Critical",
    Status == "Blocked", "Warning",
    TemperatureC > 0.9 * TempMaxC or VibrationMmS > 0.8 * VibMaxMmS, "Warning",
    Status == "Idle", "Idle",
    "Healthy")
| project Timestamp, SiteId, SiteName, City, Latitude, Longitude, LineId, LineName, LineType, MachineId, MachineName, MachineType, Criticality,
          Status, HealthState, TemperatureC, TempMaxC, VibrationMmS, VibMaxMmS, PressureBar, PowerKw, Rpm, MinutesSinceLastReading
}""",
    r""".create-or-alter function with (folder="DigitalTwin", docstring="Health score 0-100 per machine over a lookback window") MachineHealthScore(lookback: timespan = 1h) {
MachineTelemetry
| where Timestamp > ago(lookback)
| summarize AvgTemp = avg(TemperatureC), MaxTemp = max(TemperatureC), AvgVib = avg(VibrationMmS), MaxVib = max(VibrationMmS),
            FaultShare = countif(Status in ("Fault", "Blocked")) * 1.0 / count(), Readings = count() by MachineId
| join kind=inner DimMachine on MachineId
| extend TempRatio = MaxTemp / TempMaxC, VibRatio = MaxVib / VibMaxMmS
| extend HealthScore = round(max_of(0.0, 100.0
      - 60.0 * max_of(0.0, VibRatio - 0.5)
      - 80.0 * max_of(0.0, TempRatio - 0.8)
      - 100.0 * FaultShare), 1)
| project MachineId, MachineName, MachineType, LineId, SiteId, Criticality, HealthScore, MaxTemp, TempMaxC, MaxVib, VibMaxMmS, FaultShare, Readings
| order by HealthScore asc
}""",
    r""".create-or-alter function with (folder="DigitalTwin", docstring="Anomalies per machine on a metric using series_decompose_anomalies") MachineAnomalies(lookback: timespan = 2d, metric: string = "VibrationMmS", sensitivity: real = 2.5) {
MachineTelemetry
| where Timestamp > ago(lookback) and Status == "Running"
| extend Value = case(metric == "TemperatureC", TemperatureC, metric == "PressureBar", PressureBar, metric == "PowerKw", PowerKw, metric == "Rpm", Rpm, VibrationMmS)
| make-series Value = avg(Value) default = real(null) on Timestamp step 10m by MachineId
| extend Value = series_fill_linear(Value)
| extend (Anomaly, Score, Baseline) = series_decompose_anomalies(Value, sensitivity, -1, "linefit")
| mv-expand Timestamp to typeof(datetime), Value to typeof(real), Anomaly to typeof(int), Score to typeof(real), Baseline to typeof(real)
| where Anomaly != 0
| join kind=leftouter (DimMachine | project MachineId, MachineName, MachineType, LineId, SiteId) on MachineId
| project Timestamp, SiteId, LineId, MachineId, MachineName, MachineType, Metric = metric, Value = round(Value, 2), Baseline = round(Baseline, 2), Score = round(Score, 2), Direction = iff(Anomaly > 0, "Spike", "Dip")
| order by Timestamp desc
}""",
    r""".create-or-alter function with (folder="DigitalTwin", docstring="OEE (availability x performance x quality) per production line") LineOEE(lookback: timespan = 24h) {
let lines = DimLine;
MachineTelemetry
| where Timestamp > ago(lookback)
| summarize Minutes = dcount(bin(Timestamp, 1m)), RunningMinutes = dcountif(bin(Timestamp, 1m), Status == "Running"),
            Produced = sum(UnitsProduced), Rejected = sum(UnitsRejected),
            LineDownMinutes = dcountif(bin(Timestamp, 1m), Status in ("Fault", "Blocked")) by LineId
| join kind=inner lines on LineId
| extend Availability = round(1.0 - todouble(LineDownMinutes) / Minutes, 4)
| extend Performance = round(min_of(1.0, todouble(Produced) / (TargetUnitsPerHour * Minutes / 60.0)), 4)
| extend Quality = round(iff(Produced == 0, 1.0, 1.0 - todouble(Rejected) / Produced), 4)
| extend OEE = round(Availability * Performance * Quality, 4)
| project LineId, LineName, SiteId, LineType, Availability, Performance, Quality, OEE, Produced, Rejected, LineDownMinutes
| order by OEE asc
}""",
    r""".create-or-alter function with (folder="DigitalTwin", docstring="Site rollup for the map: machine counts by health state") SiteStatus() {
MachineCurrentState()
| summarize Machines = count(), Critical = countif(HealthState == "Critical"), Warning = countif(HealthState == "Warning"),
            Healthy = countif(HealthState in ("Healthy", "Idle")), Offline = countif(HealthState == "Offline") by SiteId, SiteName, City, Latitude, Longitude
| extend SiteHealth = case(Critical > 0, "Critical", Warning > 0, "Warning", "Healthy")
}""",
]

MV = r""".create-or-alter materialized-view with (backfill=true, docString="Latest telemetry per machine") MachineLatestState on table MachineTelemetry {
MachineTelemetry
| summarize arg_max(Timestamp, *) by MachineId
}"""

MIRROR = ".alter-merge table MachineTelemetry policy mirroring dataformat=parquet with (IsEnabled=true, TargetLatencyInMinutes=5, Backfill=true)"


def main():
    I = ids()
    uri, db = I["queryUri"], I["kqlDatabaseName"]
    args = set(sys.argv[1:])
    if "--fill-gap" in args:
        explicit = next((x.split("=", 1)[1] for x in args if x.startswith("--from=")), None)
        last = explicit or kusto(uri, db, "MachineTelemetry | summarize T = max(Timestamp)", mgmt=False)[0]["T"]
        start = f"datetime({explicit})" if explicit else f"bin(datetime({last}), 1m) + 1m"
        hist = history_command(start, "bin(now(), 1m)")
        r = kusto(uri, db, hist)
        print("gap filled from", start, "rows:", sum(int(x.get("RowCount", 0)) for x in r))
        return
    if "--functions-only" in args:
        for f in FUNCTIONS:
            kusto(uri, db, f)
        print("functions ok")
        return
    os.makedirs(KQL_DIR, exist_ok=True)
    open(os.path.join(KQL_DIR, "01_schema.kql"), "w", encoding="utf-8").write(SCHEMA + "\n")
    print("schema:", kusto(uri, db, ".execute database script with (ThrowOnErrors=true) <|\n" + SCHEMA)[-1].get("Result", "ok"))
    dims = dims_commands()
    open(os.path.join(KQL_DIR, "02_dims.kql"), "w", encoding="utf-8").write("\n\n".join(dims) + "\n")
    for c in dims:
        kusto(uri, db, c)
    print("dims loaded")
    if "--skip-history" not in args:
        hist = history_command("bin(now(), 1m) - 7d", "bin(now(), 1m)")
        open(os.path.join(KQL_DIR, "03_history.kql"), "w", encoding="utf-8").write(hist + "\n")
        r = kusto(uri, db, hist)
        print("history rows:", sum(int(x.get("RowCount", 0)) for x in r))
    kusto(uri, db, MV)
    print("materialized view ok")
    open(os.path.join(KQL_DIR, "04_functions.kql"), "w", encoding="utf-8").write(MV + "\n\n" + "\n\n".join(FUNCTIONS) + "\n\n" + MIRROR + "\n")
    for f in FUNCTIONS:
        kusto(uri, db, f)
    print("functions ok")
    kusto(uri, db, MIRROR)
    print("onelake availability ok")
    json.dump(SCENARIO, open(os.path.join(ROOT, "data", "scenario_anchors.json"), "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    main()
