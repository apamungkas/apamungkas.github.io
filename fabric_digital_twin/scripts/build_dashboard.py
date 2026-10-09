"""Build and deploy the PT Nusantara Manufaktur Real-Time Dashboard."""

import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from fabric_api import b64, call, find_item, ids, kusto, unb64  # noqa: E402


SKILL = "eventhouse-cli"
DISPLAY_NAME = "rtd_dt_factory"
DESCRIPTION = "Digital twin operations dashboard - PT Nusantara Manufaktur"
SCHEMA_VERSION = "52"
SCHEMA_URL = f"https://dataexplorer.azure.com/static/d/schema/{SCHEMA_VERSION}/dashboard.json"


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fabric-digital-twin-demo/{DISPLAY_NAME}/{name}"))


IDS = ids()
WORKSPACE_ID = IDS["workspaceId"]
DB_ID = IDS["kqlDatabaseId"]
DB_NAME = IDS["kqlDatabaseName"]
QUERY_URI = IDS["queryUri"]

DS_ID = stable_uuid("datasource/eh_dt_factory")
PAGE_OVERVIEW = stable_uuid("page/plant-overview")
PAGE_DRILLDOWN = stable_uuid("page/machine-drilldown")
PARAM_TIME = stable_uuid("parameter/time-range")
PARAM_SITES = stable_uuid("parameter/sites")
PARAM_MACHINE = stable_uuid("parameter/machine")


def common_xy(x_column=None, y_columns=None, series_columns=None, y_title="", horizontal=None):
    options = {
        "hideLegend": False,
        "xColumn": x_column,
        "xColumnTitle": "",
        "yColumns": y_columns,
        "yColumnTitle": y_title,
        "seriesColumns": series_columns,
        "yAxisRight": False,
        "yAxisMinimumValue": None,
        "yAxisMaximumValue": None,
        "xAxisScale": "linear",
        "yAxisScale": "linear",
        "multipleYAxes": {
            "base": {
                "id": "-1",
                "columns": [],
                "label": y_title,
                "yAxisMinimumValue": None,
                "yAxisMaximumValue": None,
                "yAxisScale": "linear",
                "horizontalLines": [],
            },
            "additional": [],
            "showMultiplePanels": False,
        },
        "selectedDataOnLoad": {"all": True, "limit": 20},
    }
    if horizontal is not None:
        options["multipleYAxes"]["base"]["horizontalLines"].append(
            {"id": stable_uuid(f"line/{y_title}/{horizontal}"), "value": horizontal}
        )
    return options


def table_options(color_by_health=False):
    opts = {
        "table__enableRenderLinks": True,
        "table__renderLinks": [],
        "colorRules": [],
        "colorRulesDisabled": not color_by_health,
        "crossFilterDisabled": False,
        "drillthroughDisabled": False,
        "crossFilter": [],
        "drillthrough": [],
        "selectedDataOnLoad": {"all": False, "limit": 50},
    }
    if color_by_health:
        for state, color in (("Critical", "red"), ("Warning", "yellow"), ("Offline", "red")):
            opts["colorRules"].append(
                {
                    "id": stable_uuid(f"color/health/{state}"),
                    "ruleName": f"{state} rows",
                    "applyTo": "rows",
                    "visualType": "table",
                    "ruleType": "colorByCondition",
                    "chainingOperator": "and",
                    "colorStyle": "light",
                    "applyToColumn": None,
                    "conditions": [{"column": "HealthState", "operator": "==", "values": [state]}],
                    "color": color,
                    "tag": state,
                    "icon": "critical" if state != "Warning" else "warning",
                    "hideText": False,
                }
            )
    return opts


queries = []


def add_query(name: str, text: str, used_variables=None):
    qid = stable_uuid(f"query/{name}")
    queries.append(
        {
            "id": qid,
            "dataSource": {"kind": "inline", "dataSourceId": DS_ID},
            "usedVariables": used_variables or [],
            "text": text.strip() + "\n",
        }
    )
    return qid


site_param_qid = add_query(
    "parameter-sites",
    """
DimSite
| order by SiteName asc
| project SiteId, SiteName
""",
)
machine_param_qid = add_query(
    "parameter-machine",
    """
DimMachine
| order by SiteId asc, LineId asc, MachineId asc
| project MachineId, MachineName = strcat(MachineId, " - ", MachineName)
""",
)

q_site_status = add_query(
    "site-status-map",
    """
SiteStatus()
| where SiteId in (_sites) or isempty(_sites)
| project SiteName, City, Latitude, Longitude, Machines, Critical, Warning, Healthy, Offline, SiteHealth
| order by SiteName asc
""",
    ["_sites"],
)

q_summary = add_query(
    "overview-summary",
    """
let current = materialize(MachineCurrentState() | where SiteId in (_sites) or isempty(_sites));
let counts = current
    | summarize Critical=countif(HealthState == "Critical"),
                Warning=countif(HealthState == "Warning"),
                Healthy=countif(HealthState == "Healthy");
let units = toscalar(
    MachineTelemetry
    | where Timestamp between (_startTime .. _endTime)
    | where SiteId in (_sites) or isempty(_sites)
    | summarize UnitsProduced=sum(UnitsProduced)
);
counts
| project Metric="Critical machines", Value=Critical
| union (counts | project Metric="Warning machines", Value=Warning)
| union (counts | project Metric="Healthy machines", Value=Healthy)
| union (print Metric="Units produced", Value=tolong(coalesce(units, 0)))
""",
    ["_startTime", "_endTime", "_sites"],
)

q_attention = add_query(
    "machines-needing-attention",
    """
MachineCurrentState()
| where SiteId in (_sites) or isempty(_sites)
| where HealthState !in ("Healthy", "Idle")
| project Timestamp, SiteName, LineName, MachineId, MachineName, MachineType, Criticality,
          Status, HealthState, TemperatureC, TempMaxC, VibrationMmS, VibMaxMmS, MinutesSinceLastReading
| order by case(HealthState == "Critical", 0, HealthState == "Warning", 1, HealthState == "Offline", 2, 3) asc,
           Criticality asc, MachineId asc
""",
    ["_sites"],
)

q_air = add_query(
    "air-compressor-vibration",
    """
let _grain = case(_endTime - _startTime <= 12h, 1m, _endTime - _startTime <= 2d, 5m, 30m);
MachineTelemetry
| where Timestamp between (_startTime .. _endTime)
| where SiteId in (_sites) or isempty(_sites)
| where MachineType == "AirCompressor"
| summarize AvgVibrationMmS=avg(VibrationMmS) by Timestamp=bin(Timestamp, _grain), MachineId
| order by Timestamp asc
""",
    ["_startTime", "_endTime", "_sites"],
)

q_injection = add_query(
    "injection-molder-temperature",
    """
let _grain = case(_endTime - _startTime <= 12h, 1m, _endTime - _startTime <= 2d, 5m, 30m);
MachineTelemetry
| where Timestamp between (_startTime .. _endTime)
| where SiteId in (_sites) or isempty(_sites)
| where MachineType == "InjectionMolder"
| summarize AvgTemperatureC=avg(TemperatureC), Rejects=sum(UnitsRejected) by Timestamp=bin(Timestamp, _grain), MachineId
| order by Timestamp asc
""",
    ["_startTime", "_endTime", "_sites"],
)

q_oee = add_query(
    "oee-by-line-24h",
    """
LineOEE(24h)
| where SiteId in (_sites) or isempty(_sites)
| project LineName, SiteId, LineType,
          OEE=round(OEE * 100.0, 1),
          Availability=round(Availability * 100.0, 1),
          Performance=round(Performance * 100.0, 1),
          Quality=round(Quality * 100.0, 1),
          Produced, Rejected, LineDownMinutes
| order by OEE asc
""",
    ["_sites"],
)

q_anomalies = add_query(
    "recent-anomalies",
    """
union
    (MachineAnomalies(1d, "VibrationMmS") | where Direction == "Spike"),
    (MachineAnomalies(1d, "TemperatureC") | where Direction == "Spike")
| where SiteId in (_sites) or isempty(_sites)
| project Timestamp, SiteId, LineId, MachineId, MachineName, MachineType, Metric,
          Value=round(Value, 3), Baseline=round(Baseline, 3), Score=round(Score, 2), Direction
| order by Timestamp desc
| take 50
""",
    ["_sites"],
)

q_health_rank = add_query(
    "health-score-ranking",
    """
MachineHealthScore(1h)
| where SiteId in (_sites) or isempty(_sites)
| project MachineId, MachineName, MachineType, SiteId, LineId, Criticality,
          HealthScore=round(HealthScore, 1), MaxTemp=round(MaxTemp, 1), TempMaxC,
          MaxVib=round(MaxVib, 3), VibMaxMmS, FaultShare=round(FaultShare, 3), Readings
| order by HealthScore asc, Criticality asc
""",
    ["_sites"],
)

q_machine_health = add_query(
    "machine-health-state",
    """
MachineCurrentState()
| where MachineId == _machine
| project Metric="Current health", Value=HealthState, Status, Timestamp, TemperatureC, VibrationMmS
""",
    ["_machine"],
)


def metric_query(metric_name, column, aggregate="avg"):
    return add_query(
        f"machine-{metric_name.lower()}",
        f"""
let _grain = case(_endTime - _startTime <= 12h, 1m, _endTime - _startTime <= 2d, 5m, 30m);
MachineTelemetry
| where Timestamp between (_startTime .. _endTime)
| where MachineId == _machine
| summarize {metric_name}={aggregate}({column}) by Timestamp=bin(Timestamp, _grain), MachineId
| order by Timestamp asc
""",
        ["_startTime", "_endTime", "_machine"],
    )


q_machine_temp = metric_query("TemperatureC", "TemperatureC")
q_machine_vib = metric_query("VibrationMmS", "VibrationMmS")
q_machine_power = metric_query("PowerKw", "PowerKw")
q_machine_rpm = metric_query("Rpm", "Rpm")


def tile(name, title, qid, page_id, visual_type, layout, visual_options):
    return {
        "id": stable_uuid(f"tile/{name}"),
        "title": title,
        "queryRef": {"kind": "query", "queryId": qid},
        "layout": layout,
        "pageId": page_id,
        "visualType": visual_type,
        "visualOptions": visual_options,
    }


tiles = [
    tile(
        "site-status-map",
        "Plant map - Site health",
        q_site_status,
        PAGE_OVERVIEW,
        "map",
        {"x": 0, "y": 0, "width": 4, "height": 4},
        {
            "map__latitudeColumn": "Latitude",
            "map__longitudeColumn": "Longitude",
            "map__bubbleFormat": "bubble",
            "map__minBubbleSizeColumn": "Machines",
            "seriesColumns": ["SiteHealth"],
            "selectedDataOnLoad": {"all": True, "limit": 10},
        },
    ),
    tile(
        "overview-summary",
        "Machine health and production",
        q_summary,
        PAGE_OVERVIEW,
        "multiStat",
        {"x": 4, "y": 0, "width": 8, "height": 4},
        {
            "multiStat__displayOrientation": "horizontal",
            "multiStat__labelColumn": "Metric",
            "multiStat__valueColumn": "Value",
            "multiStat__textSize": "large",
            "hideLegend": True,
            "selectedDataOnLoad": {"all": True, "limit": 10},
        },
    ),
    tile(
        "machines-needing-attention",
        "Machines needing attention",
        q_attention,
        PAGE_OVERVIEW,
        "table",
        {"x": 0, "y": 4, "width": 6, "height": 5},
        table_options(color_by_health=True),
    ),
    tile(
        "air-compressor-vibration",
        "Air compressor vibration (mm/s)",
        q_air,
        PAGE_OVERVIEW,
        "timechart",
        {"x": 6, "y": 4, "width": 6, "height": 5},
        common_xy("Timestamp", ["AvgVibrationMmS"], ["MachineId"], "Vibration (mm/s)", horizontal=7.1),
    ),
    tile(
        "injection-molder-temperature",
        "Injection molder temperature (°C)",
        q_injection,
        PAGE_OVERVIEW,
        "timechart",
        {"x": 0, "y": 9, "width": 6, "height": 5},
        common_xy("Timestamp", ["AvgTemperatureC"], ["MachineId"], "Temperature (°C)", horizontal=95.0),
    ),
    tile(
        "oee-by-line",
        "OEE by line (24h)",
        q_oee,
        PAGE_OVERVIEW,
        "column",
        {"x": 6, "y": 9, "width": 6, "height": 5},
        common_xy("LineName", ["OEE"], ["SiteId"], "OEE (%)"),
    ),
    tile(
        "recent-anomalies",
        "Recent anomalies",
        q_anomalies,
        PAGE_OVERVIEW,
        "table",
        {"x": 0, "y": 14, "width": 6, "height": 5},
        table_options(),
    ),
    tile(
        "health-score-ranking",
        "Health score ranking (last hour)",
        q_health_rank,
        PAGE_OVERVIEW,
        "table",
        {"x": 6, "y": 14, "width": 6, "height": 5},
        table_options(),
    ),
    tile(
        "machine-health",
        "Selected machine health",
        q_machine_health,
        PAGE_DRILLDOWN,
        "card",
        {"x": 0, "y": 0, "width": 3, "height": 4},
        {"multiStat__labelColumn": "Metric", "multiStat__valueColumn": "Value", "multiStat__textSize": "large"},
    ),
    tile(
        "machine-temperature",
        "Machine temperature (°C)",
        q_machine_temp,
        PAGE_DRILLDOWN,
        "timechart",
        {"x": 3, "y": 0, "width": 9, "height": 4},
        common_xy("Timestamp", ["TemperatureC"], ["MachineId"], "Temperature (°C)", horizontal=95.0),
    ),
    tile(
        "machine-vibration",
        "Machine vibration (mm/s)",
        q_machine_vib,
        PAGE_DRILLDOWN,
        "timechart",
        {"x": 0, "y": 4, "width": 6, "height": 4},
        common_xy("Timestamp", ["VibrationMmS"], ["MachineId"], "Vibration (mm/s)", horizontal=7.1),
    ),
    tile(
        "machine-power",
        "Machine power (kW)",
        q_machine_power,
        PAGE_DRILLDOWN,
        "timechart",
        {"x": 6, "y": 4, "width": 6, "height": 4},
        common_xy("Timestamp", ["PowerKw"], ["MachineId"], "Power (kW)"),
    ),
    tile(
        "machine-rpm",
        "Machine RPM",
        q_machine_rpm,
        PAGE_DRILLDOWN,
        "timechart",
        {"x": 0, "y": 8, "width": 12, "height": 4},
        common_xy("Timestamp", ["Rpm"], ["MachineId"], "RPM"),
    ),
]


dashboard = {
    "$schema": SCHEMA_URL,
    "autoRefresh": {"enabled": True, "defaultInterval": "1m", "minInterval": "30s"},
    "baseQueries": [],
    "tiles": tiles,
    "dataSources": [
        {
            "id": DS_ID,
            "name": DB_NAME,
            "scopeId": "kusto-trident",
            "kind": "kusto-trident",
            "clusterUri": QUERY_URI,
            "database": DB_ID,
            "workspace": WORKSPACE_ID,
        }
    ],
    "pages": [
        {"name": "Plant Overview", "id": PAGE_OVERVIEW},
        {"name": "Machine Drilldown", "id": PAGE_DRILLDOWN},
    ],
    "parameters": [
        {
            "kind": "duration",
            "id": PARAM_TIME,
            "displayName": "Time range",
            "description": "Dashboard time range",
            "beginVariableName": "_startTime",
            "endVariableName": "_endTime",
            "defaultValue": {"kind": "dynamic", "count": 6, "unit": "hours"},
            "showOnPages": {"kind": "all"},
        },
        {
            "kind": "string",
            "id": PARAM_SITES,
            "displayName": "Site",
            "description": "Plant site filter",
            "variableName": "_sites",
            "selectionType": "array",
            "includeAllOption": True,
            "allIsNull": False,
            "defaultValue": {"kind": "all"},
            "showOnPages": {"kind": "selection", "pageIds": [PAGE_OVERVIEW]},
            "dataSource": {
                "kind": "query",
                "queryRef": {"kind": "query", "queryId": site_param_qid},
                "columns": {"value": "SiteId", "label": "SiteName"},
            },
        },
        {
            "kind": "string",
            "id": PARAM_MACHINE,
            "displayName": "Machine",
            "description": "Machine drilldown selector",
            "variableName": "_machine",
            "selectionType": "scalar",
            "includeAllOption": False,
            "defaultValue": {"kind": "value", "value": "M-CKR-L2-04"},
            "showOnPages": {"kind": "selection", "pageIds": [PAGE_DRILLDOWN]},
            "dataSource": {
                "kind": "query",
                "queryRef": {"kind": "query", "queryId": machine_param_qid},
                "columns": {"value": "MachineId", "label": "MachineName"},
            },
        },
    ],
    "queries": queries,
    "schema_version": SCHEMA_VERSION,
    "title": DISPLAY_NAME,
}


def definition_payload():
    return {
        "parts": [
            {
                "path": "RealTimeDashboard.json",
                "payload": b64(dashboard),
                "payloadType": "InlineBase64",
            }
        ]
    }


def verification_query(text: str) -> str:
    return (
        'let _startTime = ago(6h);\n'
        'let _endTime = now();\n'
        'let _sites = dynamic(["PLT-CKR","PLT-KRW","PLT-SBY"]);\n'
        'let _machine = "M-CKR-L2-04";\n'
        + text
    )


def verify_queries():
    results = {}
    for q in queries:
        rows = kusto(QUERY_URI, DB_NAME, verification_query(q["text"]), mgmt=False)
        results[q["id"]] = {
            "name": next((k for k, v in globals().items() if k.startswith("q_") and v == q["id"]), q["id"]),
            "rows": len(rows),
        }
        if len(rows) == 0:
            raise RuntimeError(f"Query returned no rows: {results[q['id']]['name']}")
    return results


def save_definition():
    out_dir = os.path.join(ROOT, "definitions")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{DISPLAY_NAME}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(dashboard, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return out_path


def deploy():
    definition = definition_payload()
    item = find_item(WORKSPACE_ID, "KQLDashboard", DISPLAY_NAME, SKILL)
    if item:
        item_id = item["id"]
        call("PATCH", f"workspaces/{WORKSPACE_ID}/items/{item_id}", SKILL, {"displayName": DISPLAY_NAME, "description": DESCRIPTION})
        call("POST", f"workspaces/{WORKSPACE_ID}/items/{item_id}/updateDefinition", SKILL, {"definition": definition})
    else:
        item = call(
            "POST",
            f"workspaces/{WORKSPACE_ID}/items",
            SKILL,
            {"displayName": DISPLAY_NAME, "type": "KQLDashboard", "description": DESCRIPTION, "definition": definition},
        )
        item_id = item["id"]
    ids({"dashboardId": item_id})
    return item_id


def verify_round_trip(item_id: str):
    response = call("POST", f"workspaces/{WORKSPACE_ID}/items/{item_id}/getDefinition", SKILL)
    part = next(p for p in response["definition"]["parts"] if p["path"] == "RealTimeDashboard.json")
    rt = json.loads(unb64(part["payload"]))
    return {
        "pages": [p["name"] for p in rt.get("pages", [])],
        "tileCount": len(rt.get("tiles", [])),
        "queryCount": len(rt.get("queries", [])),
        "schema_version": rt.get("schema_version"),
        "autoRefresh": rt.get("autoRefresh"),
    }


def main():
    path = save_definition()
    query_results = verify_queries()
    item_id = deploy()
    round_trip = verify_round_trip(item_id)
    print(json.dumps({
        "definition": path,
        "dashboardId": item_id,
        "portalUrl": f"https://app.fabric.microsoft.com/groups/{WORKSPACE_ID}/kustodashboards/{item_id}",
        "schema_version": SCHEMA_VERSION,
        "tiles": [{"title": t["title"], "page": "Plant Overview" if t["pageId"] == PAGE_OVERVIEW else "Machine Drilldown", "visualType": t["visualType"]} for t in tiles],
        "queryVerification": query_results,
        "roundTrip": round_trip,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
