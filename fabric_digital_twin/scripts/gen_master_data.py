"""Generate deterministic master data for the PT Nusantara Manufaktur digital twin demo."""
import csv
import datetime as dt
import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NOW = dt.datetime(2026, 10, 8, 14, 40, tzinfo=dt.timezone.utc)
rng = random.Random(20261008)

SITES = [
    dict(SiteId="PLT-CKR", SiteName="Cikarang Plant", Area="Jababeka Industrial Estate", City="Bekasi Regency",
         Province="West Java", Country="Indonesia", Latitude=-6.2950, Longitude=107.1530, Timezone="Asia/Jakarta",
         PlantManager="Hendra Gunawan", Employees=820, OpenedYear=2012),
    dict(SiteId="PLT-KRW", SiteName="Karawang Plant", Area="KIIC Industrial Estate", City="Karawang",
         Province="West Java", Country="Indonesia", Latitude=-6.3570, Longitude=107.2930, Timezone="Asia/Jakarta",
         PlantManager="Dewi Lestari", Employees=640, OpenedYear=2016),
    dict(SiteId="PLT-SBY", SiteName="Surabaya Plant", Area="SIER Rungkut Industrial Estate", City="Surabaya",
         Province="East Java", Country="Indonesia", Latitude=-7.3340, Longitude=112.7590, Timezone="Asia/Jakarta",
         PlantManager="Rizky Hidayat", Employees=710, OpenedYear=2014),
]

LINES = [
    dict(LineId="LN-CKR-01", SiteId="PLT-CKR", LineName="Cikarang Injection Molding Line 1", LineType="InjectionMolding", TargetUnitsPerHour=1200),
    dict(LineId="LN-CKR-02", SiteId="PLT-CKR", LineName="Cikarang Assembly Line 2", LineType="Assembly", TargetUnitsPerHour=720),
    dict(LineId="LN-KRW-01", SiteId="PLT-KRW", LineName="Karawang CNC Machining Line 1", LineType="Machining", TargetUnitsPerHour=240),
    dict(LineId="LN-KRW-02", SiteId="PLT-KRW", LineName="Karawang Packaging Line 2", LineType="Packaging", TargetUnitsPerHour=2400),
    dict(LineId="LN-SBY-01", SiteId="PLT-SBY", LineName="Surabaya Injection Molding Line 1", LineType="InjectionMolding", TargetUnitsPerHour=1200),
    dict(LineId="LN-SBY-02", SiteId="PLT-SBY", LineName="Surabaya Assembly Line 2", LineType="Assembly", TargetUnitsPerHour=720),
]

LINE_LAYOUT = {
    "InjectionMolding": ["InjectionMolder", "InjectionMolder", "Chiller", "AirCompressor"],
    "Assembly": ["RobotArm", "RobotArm", "Conveyor", "AirCompressor"],
    "Machining": ["CNCMill", "CNCMill", "Conveyor", "AirCompressor"],
    "Packaging": ["Packer", "Packer", "Conveyor", "AirCompressor"],
}

PROFILES = {
    "InjectionMolder": dict(Label="Injection Molder", BaseTempC=70, TempLoadGainC=8, BaseVibMmS=2.0, BasePressureBar=140, RatedPowerKw=45, NominalRpm=1450, UnitsPerMin=10, RejectRate=0.012, TempMaxC=95, VibMaxMmS=7.1, Manufacturer="Haitian", Model="Mars MA3800", Criticality="A"),
    "Chiller": dict(Label="Chiller", BaseTempC=46, TempLoadGainC=5, BaseVibMmS=1.4, BasePressureBar=12, RatedPowerKw=30, NominalRpm=2900, UnitsPerMin=0, RejectRate=0, TempMaxC=70, VibMaxMmS=7.1, Manufacturer="Daikin", Model="EWAD-TZ 300", Criticality="B"),
    "AirCompressor": dict(Label="Air Compressor", BaseTempC=74, TempLoadGainC=7, BaseVibMmS=2.3, BasePressureBar=7.5, RatedPowerKw=55, NominalRpm=2950, UnitsPerMin=0, RejectRate=0, TempMaxC=95, VibMaxMmS=7.1, Manufacturer="Atlas Copco", Model="GA55 VSD+", Criticality="A"),
    "RobotArm": dict(Label="Robot Arm", BaseTempC=42, TempLoadGainC=6, BaseVibMmS=1.1, BasePressureBar=6.0, RatedPowerKw=7.5, NominalRpm=3000, UnitsPerMin=6, RejectRate=0.006, TempMaxC=70, VibMaxMmS=4.5, Manufacturer="FANUC", Model="M-20iD/25", Criticality="B"),
    "Conveyor": dict(Label="Conveyor", BaseTempC=38, TempLoadGainC=5, BaseVibMmS=1.5, BasePressureBar=5.5, RatedPowerKw=5.5, NominalRpm=1450, UnitsPerMin=0, RejectRate=0, TempMaxC=70, VibMaxMmS=4.5, Manufacturer="Interroll", Model="RollerDrive EC5000", Criticality="B"),
    "CNCMill": dict(Label="CNC Mill", BaseTempC=55, TempLoadGainC=7, BaseVibMmS=1.7, BasePressureBar=6.5, RatedPowerKw=22, NominalRpm=8000, UnitsPerMin=2, RejectRate=0.01, TempMaxC=80, VibMaxMmS=4.5, Manufacturer="DMG MORI", Model="DMU 50", Criticality="A"),
    "Packer": dict(Label="Packer", BaseTempC=40, TempLoadGainC=5, BaseVibMmS=1.8, BasePressureBar=6.0, RatedPowerKw=11, NominalRpm=1450, UnitsPerMin=20, RejectRate=0.004, TempMaxC=70, VibMaxMmS=4.5, Manufacturer="Bosch", Model="Pack 403", Criticality="B"),
}

TECHS = ["Budi Santoso", "Siti Rahmawati", "Agus Pratama", "Dewi Anggraini", "Andi Wijaya", "Putri Maharani", "Fajar Nugroho", "Rina Kusuma"]

machines = []
for line in LINES:
    code = line["SiteId"].split("-")[1]
    ln = int(line["LineId"][-2:])
    types = LINE_LAYOUT[line["LineType"]]
    for i, mtype in enumerate(types, start=1):
        p = PROFILES[mtype]
        dup = types.count(mtype) > 1
        suffix = f" {'AB'[types[:i].count(mtype) - 1]}" if dup else ""
        install = dt.date(rng.randint(2013, 2023), rng.randint(1, 12), rng.randint(1, 28))
        machines.append(dict(
            MachineId=f"M-{code}-L{ln}-{i:02d}", MachineName=f"{code}-L{ln} {p['Label']}{suffix}", MachineType=mtype,
            LineId=line["LineId"], SiteId=line["SiteId"], Manufacturer=p["Manufacturer"], Model=p["Model"],
            SerialNumber=f"{mtype[:3].upper()}-{install.year % 100:02d}-{rng.randint(1000, 9999)}",
            InstallDate=install.isoformat(), RatedPowerKw=float(p["RatedPowerKw"]), NominalRpm=float(p["NominalRpm"]),
            TempMaxC=float(p["TempMaxC"]), VibMaxMmS=float(p["VibMaxMmS"]), Criticality=p["Criticality"]))

wo, seq = [], 1001
kinds = [("Preventive", "Scheduled preventive maintenance", "P3"), ("Inspection", "Routine vibration and thermal inspection", "P3"),
         ("Corrective", "Replace worn seal and recalibrate sensor", "P2"), ("Preventive", "Lubrication and filter replacement", "P3")]
for m in machines:
    for k in range(rng.randint(2, 4)):
        kind, desc, prio = kinds[rng.randrange(len(kinds))]
        opened = NOW - dt.timedelta(days=rng.randint(10, 360), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        dur = rng.randint(30, 240)
        wo.append(dict(WorkOrderId=f"WO-2026-{seq:05d}", MachineId=m["MachineId"], WorkOrderType=kind, Priority=prio,
                       Status="Closed", Description=desc, OpenedAt=opened.isoformat().replace("+00:00", "Z"),
                       ClosedAt=(opened + dt.timedelta(minutes=dur + rng.randint(30, 600))).isoformat().replace("+00:00", "Z"),
                       Technician=rng.choice(TECHS), DowntimeMinutes=dur if kind == "Corrective" else rng.randint(0, 60),
                       CostIdr=float(rng.randint(5, 60) * 250000)))
        seq += 1

wo += [
    dict(WorkOrderId=f"WO-2026-{seq:05d}", MachineId="M-KRW-L2-03", WorkOrderType="Corrective", Priority="P1", Status="Closed",
         Description="Conveyor drive fault - motor encoder replaced, line restarted",
         OpenedAt="2026-10-07T07:02:00Z", ClosedAt="2026-10-07T07:44:00Z", Technician="Fajar Nugroho",
         DowntimeMinutes=45, CostIdr=18500000.0),
    dict(WorkOrderId=f"WO-2026-{seq + 1:05d}", MachineId="M-CKR-L2-04", WorkOrderType="Corrective", Priority="P1", Status="Open",
         Description="Rising vibration on air compressor - suspected bearing wear, inspect and plan bearing replacement",
         OpenedAt="2026-10-08T12:15:00Z", ClosedAt=None, Technician="Agus Pratama", DowntimeMinutes=0, CostIdr=0.0),
    dict(WorkOrderId=f"WO-2026-{seq + 2:05d}", MachineId="M-SBY-L1-01", WorkOrderType="Corrective", Priority="P2", Status="InProgress",
         Description="Injection molder barrel overheating - check heater band and mold cooling circuit",
         OpenedAt="2026-10-08T13:30:00Z", ClosedAt=None, Technician="Siti Rahmawati", DowntimeMinutes=0, CostIdr=0.0),
]
wo.sort(key=lambda w: w["OpenedAt"])

profiles = {k: {kk: vv for kk, vv in v.items()} for k, v in PROFILES.items()}
out = dict(generatedAt=NOW.isoformat(), sites=SITES, lines=LINES, machines=machines, work_orders=wo, profiles=profiles)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "master_data.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
for name, rows in (("dim_site", SITES), ("dim_production_line", LINES), ("dim_machine", machines), ("maintenance_work_order", wo)):
    with open(os.path.join(DATA, f"{name}.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
print(f"sites={len(SITES)} lines={len(LINES)} machines={len(machines)} work_orders={len(wo)}")
for m in machines:
    print(m["MachineId"], m["MachineType"], m["MachineName"])
