"""Build the ont_dt_factory ontology (Fabric IQ, new TMDL experience) for the digital twin demo.

Entities: Site -> ProductionLine -> Machine -> WorkOrder, backed by lh_dt_factory Delta tables through
Direct Lake on the lakehouse SQL endpoint. Machine telemetry time series come from the
machine_telemetry shortcut (Eventhouse MachineTelemetry via OneLake availability).
Lineage tags are persisted in definitions/ontology_lineage.json so re-runs update in place.
Usage: python build_ontology.py [--dry-run]
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import b64, call, find_item, ids  # noqa: E402

SK = "fabriciq-ontology-cli"
NAME = "ont_dt_factory"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF_DIR = os.path.join(ROOT, "definitions", "ont_dt_factory")
TAG_FILE = os.path.join(ROOT, "definitions", "ontology_lineage.json")
TAGS = json.load(open(TAG_FILE, encoding="utf-8")) if os.path.exists(TAG_FILE) else {}


def tag(key):
    if key not in TAGS:
        TAGS[key] = str(uuid.uuid4())
    return TAGS[key]


# table name -> list of (column, tmdl dataType)
TABLES = {
    "dim_site": [("SiteId", "string"), ("SiteName", "string"), ("Area", "string"), ("City", "string"), ("Province", "string"),
                 ("Country", "string"), ("Latitude", "double"), ("Longitude", "double"), ("Timezone", "string"),
                 ("PlantManager", "string"), ("Employees", "int64"), ("OpenedYear", "int64")],
    "dim_production_line": [("LineId", "string"), ("SiteId", "string"), ("LineName", "string"), ("LineType", "string"),
                            ("TargetUnitsPerHour", "int64")],
    "dim_machine": [("MachineId", "string"), ("MachineName", "string"), ("MachineType", "string"), ("LineId", "string"),
                    ("SiteId", "string"), ("Manufacturer", "string"), ("Model", "string"), ("SerialNumber", "string"),
                    ("InstallDate", "dateTime"), ("RatedPowerKw", "double"), ("NominalRpm", "double"), ("TempMaxC", "double"),
                    ("VibMaxMmS", "double"), ("Criticality", "string")],
    "maintenance_work_order": [("WorkOrderId", "string"), ("MachineId", "string"), ("WorkOrderType", "string"), ("Priority", "string"),
                               ("Status", "string"), ("Description", "string"), ("OpenedAt", "dateTime"), ("ClosedAt", "dateTime"),
                               ("Technician", "string"), ("DowntimeMinutes", "int64"), ("CostIdr", "double")],
    "machine_telemetry": [("Timestamp", "dateTime"), ("MachineId", "string"),
                          ("Status", "string"), ("TemperatureC", "double"), ("VibrationMmS", "double"),
                          ("PressureBar", "double"), ("PowerKw", "double"), ("Rpm", "double"), ("UnitsProduced", "int64"),
                          ("UnitsRejected", "int64")],
}

RELATIONSHIPS = [  # name, from (many side), to (one side)
    ("rel_line_site", "dim_production_line.SiteId", "dim_site.SiteId"),
    ("rel_machine_line", "dim_machine.LineId", "dim_production_line.LineId"),
    ("rel_workorder_machine", "maintenance_work_order.MachineId", "dim_machine.MachineId"),
    ("rel_telemetry_machine", "machine_telemetry.MachineId", "dim_machine.MachineId"),
]

# entity -> (description, backing table, key, synonyms, [(property, column, dataType, description)], timeseries, extra tables)
ENTITIES = {
    "Site": ("A manufacturing plant of PT Nusantara Manufaktur in Indonesia.", "dim_site", "SiteId", ["plant", "factory", "pabrik"], [
        ("SiteId", "SiteId", "string", "Unique plant identifier."),
        ("SiteName", "SiteName", "string", "Plant name."),
        ("Area", "Area", "string", "Industrial estate where the plant is located."),
        ("City", "City", "string", "City or regency."),
        ("Province", "Province", "string", "Province."),
        ("Country", "Country", "string", "Country."),
        ("Latitude", "Latitude", "double", "Plant latitude."),
        ("Longitude", "Longitude", "double", "Plant longitude."),
        ("Timezone", "Timezone", "string", "IANA time zone of the plant (WIB)."),
        ("PlantManager", "PlantManager", "string", "Plant manager."),
        ("Employees", "Employees", "int64", "Number of employees."),
        ("OpenedYear", "OpenedYear", "int64", "Year the plant opened."),
    ], [], []),
    "ProductionLine": ("A production line inside a plant.", "dim_production_line", "LineId", ["line", "lini"], [
        ("LineId", "LineId", "string", "Unique production line identifier."),
        ("LineSiteId", "SiteId", "string", "Plant that owns the line."),
        ("LineName", "LineName", "string", "Production line name."),
        ("LineType", "LineType", "string", "Line type: InjectionMolding, Assembly, Machining or Packaging."),
        ("TargetUnitsPerHour", "TargetUnitsPerHour", "int64", "Planned output of the line in units per hour."),
    ], [], []),
    "Machine": ("A production machine (equipment) on a line, with live telemetry streamed from the plant gateway every 10 seconds.",
                "dim_machine", "MachineId", ["equipment", "asset", "mesin"], [
        ("MachineId", "MachineId", "string", "Unique machine identifier."),
        ("MachineName", "MachineName", "string", "Machine display name."),
        ("MachineType", "MachineType", "string", "InjectionMolder, AirCompressor, Chiller, RobotArm, Conveyor, CNCMill or Packer."),
        ("MachineLineId", "LineId", "string", "Production line the machine belongs to."),
        ("MachineSiteId", "SiteId", "string", "Plant the machine belongs to."),
        ("Manufacturer", "Manufacturer", "string", "Machine manufacturer."),
        ("Model", "Model", "string", "Machine model."),
        ("SerialNumber", "SerialNumber", "string", "Machine serial number."),
        ("InstallDate", "InstallDate", "dateTime", "Installation date."),
        ("RatedPowerKw", "RatedPowerKw", "double", "Rated electrical power (kW)."),
        ("NominalRpm", "NominalRpm", "double", "Nominal speed (rpm)."),
        ("TempMaxC", "TempMaxC", "double", "Maximum allowed temperature (degrees C)."),
        ("VibMaxMmS", "VibMaxMmS", "double", "Maximum allowed vibration velocity (mm/s, ISO 10816)."),
        ("Criticality", "Criticality", "string", "Asset criticality class A (critical) or B."),
    ], [
        ("TemperatureC", "TemperatureC", "double", "Machine temperature (degrees C)."),
        ("VibrationMmS", "VibrationMmS", "double", "Vibration velocity RMS (mm/s)."),
        ("PressureBar", "PressureBar", "double", "Process or pneumatic pressure (bar)."),
        ("PowerKw", "PowerKw", "double", "Electrical power draw (kW)."),
        ("Rpm", "Rpm", "double", "Shaft or spindle speed (rpm)."),
        ("OperatingStatus", "Status", "string", "Operating status: Running, Idle, Fault or Blocked."),
        ("UnitsProduced", "UnitsProduced", "int64", "Units produced in the reading interval."),
        ("UnitsRejected", "UnitsRejected", "int64", "Units rejected in the reading interval."),
        ("TelemetryTimestamp", "Timestamp", "dateTime", "Timestamp of the telemetry reading (UTC)."),
    ], [("machine_telemetry", "rel_telemetry_machine")]),
    "WorkOrder": ("A maintenance work order raised against a machine.", "maintenance_work_order", "WorkOrderId", ["maintenance", "workorder"], [
        ("WorkOrderId", "WorkOrderId", "string", "Unique work order identifier."),
        ("WorkOrderMachineId", "MachineId", "string", "Machine the work order is raised against."),
        ("WorkOrderType", "WorkOrderType", "string", "Preventive, Inspection or Corrective."),
        ("Priority", "Priority", "string", "Priority P1 (urgent) to P3."),
        ("WorkOrderStatus", "Status", "string", "Open, InProgress or Closed."),
        ("Description", "Description", "string", "Work description."),
        ("OpenedAt", "OpenedAt", "dateTime", "When the work order was opened (UTC)."),
        ("ClosedAt", "ClosedAt", "dateTime", "When the work order was closed (UTC)."),
        ("Technician", "Technician", "string", "Assigned technician."),
        ("DowntimeMinutes", "DowntimeMinutes", "int64", "Machine downtime caused by the work (minutes)."),
        ("CostIdr", "CostIdr", "double", "Cost of the work in Indonesian rupiah."),
    ], [], []),
}

ENTITY_RELATIONSHIPS = [  # name, from entity, to entity, TOM relationship, description
    ("Line In Site", "ProductionLine", "Site", "rel_line_site", "A production line belongs to a plant."),
    ("Machine On Line", "Machine", "ProductionLine", "rel_machine_line", "A machine is installed on a production line."),
    ("Work Order For Machine", "WorkOrder", "Machine", "rel_workorder_machine", "A maintenance work order is raised against a machine."),
]

RULES = [
    ("VibrationLimit", "A machine whose vibration exceeds its VibMaxMmS limit is in critical condition and needs a corrective work order; "
     "rising vibration on an air compressor usually indicates bearing wear.",
     [("Machine", ["VibrationMmS", "VibMaxMmS", "MachineType"]), ("WorkOrder", None)], ["Work Order For Machine"]),
    ("TemperatureLimit", "A machine whose temperature exceeds its TempMaxC limit is overheating; an overheating injection molder "
     "also produces more rejected units.", [("Machine", ["TemperatureC", "TempMaxC", "UnitsRejected"])], []),
    ("LineStoppage", "When a conveyor on a production line has OperatingStatus Fault, the other machines on that line become Blocked "
     "and the line stops producing.", [("Machine", ["OperatingStatus", "MachineType"]), ("ProductionLine", None)], ["Machine On Line"]),
]


def q(name):
    return f"'{name}'" if " " in name else name


def table_tmdl(name, cols):
    out = [f"table {name}", f"\tlineageTag: {tag('table:' + name)}", ""]
    for c, t in cols:
        out += [f"\tcolumn {c}", f"\t\tdataType: {t}"]
        if t == "int64":
            out.append("\t\tformatString: 0")
        if t == "dateTime":
            out.append("\t\tformatString: General Date")
        out += [f"\t\tlineageTag: {tag(f'column:{name}.{c}')}", f"\t\tsourceColumn: {c}", ""]
    out += [f"\tpartition {name} = entity", "\t\tmode: directLake", "\t\tsource", f"\t\t\tentityName: {name}",
            "\t\t\tschemaName: dbo", "\t\t\texpressionSource: DatabaseQuery", ""]
    return "\n".join(out) + "\n"


def entity_tmdl(name, spec, links):
    desc, table, key, synonyms, props, ts_props, extra = spec
    out = [f"/// {desc}", f"entity {name}", f"\tlineageTag: {tag('entity:' + name)}", f"\tbackingTable: {table}", f"\tkeyProperty: {key}"]
    out += [f"\tsynonym {s}" for s in synonyms]
    out.append("")
    for p, col, t, d in props:
        out += [f"\t/// {d}", f"\tproperty {p}", f"\t\tdataType: {t}", f"\t\tlineageTag: {tag(f'property:{name}.{p}')}", "",
                "\t\tbackingConfiguration", f"\t\t\tvalueColumn: {table}.{col}", ""]
    for p, col, t, d in ts_props:
        tt = extra[0][0]
        out += [f"\t/// {d}", f"\tproperty {p}", f"\t\tdataType: TimeSeries<{t}>", f"\t\tlineageTag: {tag(f'property:{name}.{p}')}", "",
                "\t\tbackingConfiguration", "\t\t\ttype: timeSeries", f"\t\t\tvalueColumn: {tt}.{col}",
                f"\t\t\torderingColumn: {tt}.Timestamp", ""]
    for t, rel in extra:
        out += ["\tadditionalBackingTable", f"\t\ttable: {t}", f"\t\trelationship: {rel}", ""]
    for ldesc, ws, item in links:
        out += [f"\t/// {ldesc}", "\tresourceLink", "\t\titem", f"\t\t\tworkspaceId: {ws}", f"\t\t\titemId: {item}", ""]
    return "\n".join(out) + "\n"


def build_parts(I):
    host, sql_id = I["sqlEndpointHost"], I["sqlEndpointId"]
    files = {}
    files[".platform"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Ontology", "displayName": NAME,
                     "description": "Digital twin ontology for PT Nusantara Manufaktur: plants, production lines, machines with live telemetry, and maintenance work orders"},
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}}, indent=2)
    files["database.tmdl"] = "/// Digital twin ontology for PT Nusantara Manufaktur (Cikarang, Karawang, Surabaya).\ndatabase\n\tcompatibilityLevel: 1000000\n"
    files["expressions.tmdl"] = (f"expression DatabaseQuery =\n\t\tlet\n\t\t    database = Sql.Database(\"{host}\", \"{sql_id}\")\n"
                                 f"\t\tin\n\t\t    database\n\tlineageTag: {tag('expression:DatabaseQuery')}\n")
    for t, cols in TABLES.items():
        files[f"tables/{t}.tmdl"] = table_tmdl(t, cols)
    files["relationships.tmdl"] = "\n".join(f"relationship {n}\n\tfromColumn: {f}\n\ttoColumn: {t}\n" for n, f, t in RELATIONSHIPS)
    links = {}
    if I.get("dashboardId"):
        for e in ("Site", "Machine"):
            links.setdefault(e, []).append(("Live operations dashboard (Real-Time Dashboard rtd_dt_factory).", I["workspaceId"], I["dashboardId"]))
    if I.get("dtbId"):
        links.setdefault("Machine", []).append(("Digital Twin Builder model of the plants (dtb_dt_factory).", I["workspaceId"], I["dtbId"]))
    for e, spec in ENTITIES.items():
        files[f"entities/{e}.tmdl"] = entity_tmdl(e, spec, links.get(e, []))
    files["entityRelationships.tmdl"] = "\n".join(
        f"/// {d}\nentityRelationship {q(n)}\n\tlineageTag: {tag('rel:' + n)}\n\tfromEntity: {f}\n\ttoEntity: {t}\n\n\tbackingConfiguration\n\t\trelationship: {r}\n"
        for n, f, t, r, d in ENTITY_RELATIONSHIPS)
    files["namespaces/default.tmdl"] = "namespace default\n\tlineageTag: default\n"
    for rname, stmt, ents, rels in RULES:
        lines = [f"rule {rname}", f"\tstatement: {stmt}", f"\tlineageTag: {tag('rule:' + rname)}", ""]
        for ent, props in ents:
            lines.append(f"\truleReferencedEntity {ent}")
            if props:
                lines += ["\t\tpropertyScope: specific", ""] + [f"\t\truleReferencedProperty {p}\n" for p in props]
            else:
                lines.append("")
        lines += [f"\truleReferencedRelationship {q(r)}" for r in rels]
        files[f"rules/{rname}.tmdl"] = "\n".join(lines).rstrip() + "\n"
    model = ["model Model", ""] + [f"ref table {t}" for t in TABLES] + [""] + [f"ref entity {e}" for e in ENTITIES] + \
            ["", "ref namespace default", ""] + [f"ref rule {r[0]}" for r in RULES]
    files["model.tmdl"] = "\n".join(model) + "\n"
    return files


def main():
    I = ids()
    files = build_parts(I)
    for path, content in files.items():
        fp = os.path.join(DEF_DIR, *path.split("/"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
    json.dump(TAGS, open(TAG_FILE, "w", encoding="utf-8"), indent=2)
    if "--dry-run" in sys.argv:
        print(f"wrote {len(files)} parts to {DEF_DIR}")
        return
    ws = I["workspaceId"]
    parts = [{"path": p, "payload": b64(c), "payloadType": "InlineBase64"} for p, c in files.items()]
    item = find_item(ws, "Ontology", NAME, SK)
    if item:
        call("POST", f"workspaces/{ws}/items/{item['id']}/updateDefinition?updateMetadata=true", SK, body={"definition": {"parts": parts}})
        print("ontology updated", item["id"])
    else:
        call("POST", f"workspaces/{ws}/items", SK, body={"displayName": NAME, "type": "Ontology",
             "description": "Digital twin ontology for PT Nusantara Manufaktur", "definition": {"parts": parts}})
        item = find_item(ws, "Ontology", NAME, SK)
        print("ontology created", item["id"])
    ids({"ontologyId": item["id"]})
    got = call("POST", f"workspaces/{ws}/items/{item['id']}/getDefinition", SK, body={})
    paths = sorted(p["path"] for p in got.get("definition", {}).get("parts", []))
    print(f"readback parts ({len(paths)}):", paths)


if __name__ == "__main__":
    main()
