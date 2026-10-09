"""Create Activator act_dt_alerts: KQL source on eh_dt_factory.MachineTelemetry, per-machine object,
rules 'vibration becomes > 7.1 mm/s' and 'temperature becomes > 95 C', email to the demo admin."""
import base64
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import call, find_item, ids, kusto  # noqa: E402

SK = "activator-cli"
NAME = "act_dt_alerts"
RECIPIENT = "admin@YourTenantName.onmicrosoft.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KQL = ("declare query_parameters(startTime:datetime, endTime:datetime);\n"
       "MachineTelemetry\n| where Timestamp between (startTime .. endTime)\n"
       "| project Timestamp, MachineId, SiteId, LineId, MachineType, Status, TemperatureC, VibrationMmS")


def gid():
    return str(uuid.uuid4())


def s(obj):
    return json.dumps(obj, separators=(",", ":"))


def ref(entity_id, complex_ref=False, name=None):
    r = {"kind": "AttributeReference", "type": "complexReference" if complex_ref else "complex",
         "arguments": [{"name": "entityId", "type": "string", "value": entity_id}]}
    if name:
        r["name"] = name
    return r


def text(v):
    return {"name": "string", "type": "string", "value": v}


def build(I):
    ws, kdb = I["workspaceId"], I["kqlDatabaseId"]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    C, SRC, EV, OBJ, IDA, SPLIT = (gid() for _ in range(6))
    attrs = {n: gid() for n in ("Machine", "Site", "Line", "Machine type", "Status", "Temperature (C)", "Vibration (mm/s)")}
    fields = {"Machine": ("MachineId", "Text"), "Site": ("SiteId", "Text"), "Line": ("LineId", "Text"),
              "Machine type": ("MachineType", "Text"), "Status": ("Status", "Text"),
              "Temperature (C)": ("TemperatureC", "Number"), "Vibration (mm/s)": ("VibrationMmS", "Number")}
    pc = {"targetUniqueIdentifier": C}
    po = {"targetUniqueIdentifier": OBJ}
    ents = [
        {"uniqueIdentifier": C, "payload": {"name": "Digital twin machine alerts", "type": "kqlQueries"}, "type": "container-v1"},
        {"uniqueIdentifier": SRC, "payload": {
            "name": "Machine telemetry (eh_dt_factory)", "runSettings": {"executionIntervalInSeconds": 60},
            "query": {"queryString": KQL},
            "eventhouseItem": {"itemId": kdb, "workspaceId": ws, "itemType": "KustoDatabase"},
            "queryParameters": [{"name": "startTime", "type": "DURATION_START", "value": (now - dt.timedelta(minutes=10)).isoformat().replace("+00:00", "Z")},
                                {"name": "endTime", "type": "DURATION_END", "value": now.isoformat().replace("+00:00", "Z")}],
            "eventTimeSettings": {"timeFieldName": "Timestamp", "ingestionDelayInSeconds": 120, "timeZone": "UTC"},
            "metadata": {"workspaceId": ws, "measureName": "", "querySetId": "", "queryId": ""},
            "parentContainer": pc}, "type": "kqlSource-v1"},
        {"uniqueIdentifier": EV, "payload": {"name": "Machine telemetry events", "parentContainer": pc, "definition": {"type": "Event", "instance": s({
            "templateId": "SourceEvent", "templateVersion": "1.2.4", "steps": [{"name": "SourceEventStep", "id": gid(), "rows": [
                {"name": "SourceSelector", "kind": "SourceReference", "arguments": [{"name": "entityId", "type": "string", "value": SRC}]}]}]})}},
         "type": "timeSeriesView-v1"},
        {"uniqueIdentifier": OBJ, "payload": {"name": "Machine", "parentContainer": pc, "definition": {"type": "Object"}}, "type": "timeSeriesView-v1"},
        {"uniqueIdentifier": IDA, "payload": {"name": "MachineId", "parentObject": po, "parentContainer": pc, "definition": {"type": "Attribute", "instance": s({
            "templateId": "IdentityPartAttribute", "templateVersion": "1.2.4", "steps": [{"name": "IdPartStep", "id": gid(), "rows": [
                {"name": "TypeAssertion", "kind": "TypeAssertion", "arguments": [{"name": "op", "type": "string", "value": "Text"},
                                                                                  {"name": "format", "type": "string", "value": ""}]}]}]})}},
         "type": "timeSeriesView-v1"},
        {"uniqueIdentifier": SPLIT, "payload": {"name": "Machine events", "parentObject": po, "parentContainer": pc, "definition": {"type": "Event", "instance": s({
            "templateId": "SplitEvent", "templateVersion": "1.2.4", "steps": [{"name": "SplitEventStep", "id": gid(), "rows": [
                {"name": "EventSelector", "kind": "Event", "arguments": [{"kind": "EventReference", "type": "complex", "name": "event",
                                                                          "arguments": [{"name": "entityId", "type": "string", "value": EV}]}]},
                {"name": "FieldIdMapping", "kind": "FieldIdMapping", "arguments": [{"name": "fieldName", "type": "string", "value": "MachineId"},
                                                                                    {**ref(IDA), "name": "idPart"}]},
                {"name": "SplitEventOptions", "kind": "EventOptions", "arguments": [{"name": "isAuthoritative", "type": "boolean", "value": True}]}]}]})}},
         "type": "timeSeriesView-v1"},
    ]
    for name, aid in attrs.items():
        field, op = fields[name]
        ents.append({"uniqueIdentifier": aid, "payload": {"name": name, "parentObject": po, "parentContainer": pc, "definition": {"type": "Attribute", "instance": s({
            "templateId": "BasicEventAttribute", "templateVersion": "1.2.4", "steps": [
                {"name": "EventSelectStep", "id": gid(), "rows": [
                    {"name": "EventSelector", "kind": "Event", "arguments": [{"kind": "EventReference", "type": "complex", "name": "event",
                                                                              "arguments": [{"name": "entityId", "type": "string", "value": SPLIT}]}]},
                    {"name": "EventFieldSelector", "kind": "EventField", "arguments": [{"name": "fieldName", "type": "string", "value": field}]}]},
                {"name": "EventComputeStep", "id": gid(), "rows": [
                    {"name": "TypeAssertion", "kind": "TypeAssertion", "arguments": [{"name": "op", "type": "string", "value": op},
                                                                                      {"name": "format", "type": "string", "value": ""}]}]}]})}},
            "type": "timeSeriesView-v1"})

    def info():
        return [{"kind": "NameReferencePair", "type": "complex", "arguments": [{"name": "name", "type": "string", "value": n},
                ref(attrs[n], complex_ref=True, name="reference")]} for n in attrs]

    def rule(rname, attr, threshold, subject, headline, unit):
        email = {"name": "EmailBinding", "kind": "EmailMessage", "arguments": [
            {"name": "messageLocale", "type": "string", "value": "en-us"},
            {"name": "sentTo", "type": "array", "values": [{"type": "string", "value": RECIPIENT}]},
            {"name": "copyTo", "type": "array", "values": []},
            {"name": "bCCTo", "type": "array", "values": []},
            {"name": "subject", "type": "array", "values": [text(subject), ref(attrs["Machine"])]},
            {"name": "headline", "type": "array", "values": [text(headline)]},
            {"name": "optionalMessage", "type": "array", "values": [
                text("Machine "), ref(attrs["Machine"]), text(" ("), ref(attrs["Machine type"]), text(") at "), ref(attrs["Site"]),
                text(f" reported {unit} "), ref(attrs[attr]),
                text(". Open the rtd_dt_factory dashboard and the ont_dt_factory ontology in ws_demo_digital_twin to investigate.")]},
            {"name": "additionalInformation", "type": "array", "values": info()}]}
        inst = {"templateId": "AttributeTrigger", "templateVersion": "1.2.4", "steps": [
            {"name": "ScalarSelectStep", "id": gid(), "rows": [{"name": "AttributeSelector", "kind": "Attribute", "arguments": [{**ref(attrs[attr]), "name": "attribute"}]}]},
            {"name": "ScalarDetectStep", "id": gid(), "rows": [{"name": "NumberBecomes", "kind": "NumberBecomes", "arguments": [
                {"name": "op", "type": "string", "value": "BecomesGreaterThan"}, {"name": "value", "type": "number", "value": float(threshold)}]}]},
            {"name": "ActStep", "id": gid(), "rows": [email]}]}
        return {"uniqueIdentifier": gid(), "payload": {"name": rname, "description": "Created by: skills-for-fabric", "parentObject": po,
                "parentContainer": pc, "definition": {"type": "Rule", "instance": s(inst), "settings": {"shouldRun": True, "shouldApplyRuleOnUpdate": False}}},
                "type": "timeSeriesView-v1"}

    ents.append(rule("High vibration (> 7.1 mm/s)", "Vibration (mm/s)", 7.1, "[Digital Twin] High vibration on ",
                     "Machine vibration crossed 7.1 mm/s (ISO 10816 zone D) - possible bearing wear", "vibration (mm/s)"))
    ents.append(rule("Overheating (> 95 C)", "Temperature (C)", 95, "[Digital Twin] Overheating on ",
                     "Machine temperature crossed 95 C - check heater bands and cooling circuit", "temperature (C)"))
    return ents


def preflight(ents):
    ids_ = {e["uniqueIdentifier"]: e["type"] for e in ents}
    blob = json.dumps(ents)
    refs = set()
    for e in ents:
        p = e["payload"]
        for k in ("parentContainer", "parentObject"):
            if k in p:
                refs.add(p[k]["targetUniqueIdentifier"])
        inst = p.get("definition", {}).get("instance")
        if inst:
            for v in json.loads(inst).get("steps", []):
                for row in v["rows"]:
                    stack = [row]
                    while stack:
                        x = stack.pop()
                        if isinstance(x, dict):
                            if x.get("name") == "entityId":
                                refs.add(x["value"])
                            stack += x.get("arguments", []) + x.get("values", [])
    missing = [r for r in refs if r not in ids_]
    assert not missing, f"unresolved references: {missing}"
    return len(refs), len(blob)


def main():
    I = ids()
    ws = I["workspaceId"]
    sample = kusto(I["queryUri"], I["kqlDatabaseName"],
                   KQL.split("\n", 1)[1].replace("between (startTime .. endTime)", "> ago(5m)") + " | take 3", mgmt=False)
    assert sample, "source validation failed: no rows"
    print("source validated, sample:", {k: sample[0][k] for k in ("Timestamp", "MachineId", "TemperatureC", "VibrationMmS")})
    ents = build(I)
    print("preflight refs/bytes:", preflight(ents))
    json.dump(ents, open(os.path.join(ROOT, "definitions", "act_dt_alerts.ReflexEntities.json"), "w", encoding="utf-8"), indent=1)
    item = find_item(ws, "Reflex", NAME, SK)
    if not item:
        item = call("POST", f"workspaces/{ws}/reflexes", SK, body={"displayName": NAME,
                    "description": "Digital twin alerts: machine vibration and overheating (emails the demo admin)"})
        if "id" not in item:
            item = find_item(ws, "Reflex", NAME, SK)
        print("activator created", item["id"])
    ids({"activatorId": item["id"]})
    payload = base64.b64encode(json.dumps(ents).encode("utf-8")).decode("ascii")
    r = call("POST", f"workspaces/{ws}/reflexes/{item['id']}/updateDefinition", SK,
             body={"definition": {"parts": [{"path": "ReflexEntities.json", "payload": payload, "payloadType": "InlineBase64"}]}})
    print("updateDefinition:", r if isinstance(r, dict) and r.get("_status") else "ok")
    got = call("POST", f"workspaces/{ws}/reflexes/{item['id']}/getDefinition", SK, body={})
    part = next(p for p in got["definition"]["parts"] if p["path"] == "ReflexEntities.json")
    back = json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
    rules = [e["payload"]["name"] for e in back if e["type"] == "timeSeriesView-v1" and e["payload"].get("definition", {}).get("type") == "Rule"]
    print(f"readback entities: {len(back)}, rules: {rules}")


if __name__ == "__main__":
    main()
