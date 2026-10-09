"""Create Eventstream es-dt-telemetry (CustomEndpoint -> Eventhouse MachineTelemetry) and store the
Custom Endpoint connection string in a private local secret file (never printed)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import b64, call, find_item, ids  # noqa: E402

SK = "eventstream-cli"
NAME = "es-dt-telemetry"
SECRET_FILE = os.path.join(os.path.expanduser("~"), ".secrets", "fabric-dt-demo.env")


def topology(I):
    return {
        "compatibilityLevel": "1.0",
        "sources": [{"name": "MachineGateway", "type": "CustomEndpoint", "properties": {}}],
        "streams": [{"name": f"{NAME}-stream", "type": "DefaultStream", "properties": {},
                     "inputNodes": [{"name": "MachineGateway"}]}],
        "operators": [],
        "destinations": [{
            "name": "TelemetryEventhouse", "type": "Eventhouse",
            "properties": {"dataIngestionMode": "ProcessedIngestion", "workspaceId": I["workspaceId"],
                           "itemId": I["kqlDatabaseId"], "databaseName": I["kqlDatabaseName"], "tableName": "MachineTelemetry",
                           "inputSerialization": {"type": "Json", "properties": {"encoding": "UTF8"}}},
            "inputNodes": [{"name": f"{NAME}-stream"}]}],
    }


def main():
    I = ids()
    ws = I["workspaceId"]
    parts = [{"path": "eventstream.json", "payload": b64(topology(I)), "payloadType": "InlineBase64"},
             {"path": "eventstreamProperties.json", "payload": b64({"retentionTimeInDays": 1, "eventThroughputLevel": "Low"}), "payloadType": "InlineBase64"}]
    es = find_item(ws, "Eventstream", NAME, SK)
    if es:
        call("POST", f"workspaces/{ws}/eventstreams/{es['id']}/updateDefinition", SK, body={"definition": {"parts": parts}})
        print("eventstream updated", es["id"])
    else:
        call("POST", f"workspaces/{ws}/items", SK, body={"displayName": NAME, "type": "Eventstream",
             "description": "Machine telemetry from plant gateways (Custom Endpoint) into eh_dt_factory.MachineTelemetry",
             "definition": {"parts": parts}})
        es = find_item(ws, "Eventstream", NAME, SK)
        print("eventstream created", es["id"])
    ids({"eventstreamId": es["id"]})
    topo = None
    for _ in range(20):
        topo = call("GET", f"workspaces/{ws}/eventstreams/{es['id']}/topology", SK)
        states = [(n.get("name"), n.get("status")) for k in ("sources", "destinations") for n in topo.get(k, [])]
        if all(s in ("Running", "Active") for _, s in states):
            break
        time.sleep(10)
    print("topology:", [(n.get("name"), n.get("type"), n.get("status"), n.get("error")) for k in ("sources", "streams", "destinations") for n in topo.get(k, [])])
    src = next(n for n in topo["sources"] if n["name"] == "MachineGateway")
    conn = call("GET", f"workspaces/{ws}/eventstreams/{es['id']}/sources/{src['id']}/connection", SK)
    keys = conn.get("accessKeys") or {}
    cs = keys.get("primaryConnectionString")
    if not cs:
        raise SystemExit("connection string not returned")
    os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
    with open(SECRET_FILE, "w", encoding="utf-8") as fh:
        fh.write(f"EVENTHUB_CONNECTION_STRING={cs}\nEVENTHUB_NAME={conn.get('eventHubName', '')}\n")
    ids({"eventstreamSourceId": src["id"], "eventHubNamespace": conn.get("fullyQualifiedNamespace")})
    print(f"custom endpoint connection saved to {SECRET_FILE} (hub name length {len(conn.get('eventHubName', ''))}, key not shown)")


if __name__ == "__main__":
    main()
