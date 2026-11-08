import argparse
import base64
import json
import time
from datetime import datetime, timedelta, timezone

from deploy import FABRIC, ROOT, SERVERS, CONNECTION_PREFIX, call, collection, read_state, save_state, secret


def workspace_url():
    return FABRIC + "/workspaces/" + read_state()["workspaceId"]


def expression(value):
    return {"value": value, "type": "Expression"}


def dataset(connection, table=None):
    result = {"type": "SqlServerTable", "schema": [], "externalReferences": {"connection": connection}}
    if table:
        result["typeProperties"] = {"schema": "dbo", "table": table}
    return result


def policy(retry=2):
    return {"timeout": "0.00:10:00", "retry": retry, "retryIntervalInSeconds": 30,
            "secureInput": False, "secureOutput": False}


def dependency(name):
    return [{"activity": name, "dependencyConditions": ["Succeeded"]}] if name else []


def procedure(name, connection, database, procedure_name, parameters=None, after=None):
    return {
        "name": name, "type": "SqlServerStoredProcedure", "dependsOn": dependency(after),
        "policy": policy(), "externalReferences": {"connection": connection},
        "typeProperties": {"storedProcedureName": procedure_name,
                           "storedProcedureParameters": parameters or {}, "database": database},
    }


def lookup(name, connection, query=None, after=None, stored_procedure=None, parameters=None):
    source = {"type": "SqlServerSource", "queryTimeout": "00:05:00"}
    if stored_procedure:
        source["sqlReaderStoredProcedureName"] = stored_procedure
        source["storedProcedureParameters"] = parameters or {}
    else:
        source["sqlReaderQuery"] = query
    return {
        "name": name, "type": "Lookup", "dependsOn": dependency(after), "policy": policy(),
        "typeProperties": {"source": source, "datasetSettings": dataset(connection),
                           "firstRowOnly": True},
    }


def create_connections():
    state = read_state()
    existing = collection(FABRIC + "/connections")
    ids = state.get("connections", {})
    for role, server in SERVERS.items():
        name = CONNECTION_PREFIX + role
        matches = [item for item in existing if item.get("displayName") == name]
        if len(matches) > 1:
            raise RuntimeError("Connection display name is ambiguous: " + name)
        if matches:
            result = matches[0]
            if result.get("gatewayId") != state["gatewayId"]:
                raise RuntimeError("Existing connection does not use this demo's gateway.")
        else:
            result = call("POST", FABRIC + "/connections", {
                "connectivityType": "VirtualNetworkGateway", "displayName": name,
                "gatewayId": state["gatewayId"],
                "connectionDetails": {"type": "SQL", "creationMethod": "Sql", "parameters": [
                    {"dataType": "Text", "name": "server", "value": server["ip"] + ",1433"},
                    {"dataType": "Text", "name": "database", "value": server["database"]},
                ]},
                "privacyLevel": "Private",
                "credentialDetails": {
                    "singleSignOnType": "None", "connectionEncryption": "NotEncrypted",
                    "skipTestConnection": False,
                    "credentials": {"credentialType": "Basic", "username": "fabric_cdc",
                                    "password": secret(role + "-sql-pipeline-password")},
                },
            }).json()
        # Both servers force TLS; the lab's private gateway accepts their self-signed certificates.
        ids[role] = result["id"]
        save_state(connections=ids)
        print(f"Connection {name}: {ids[role]}", flush=True)


def failure_injection(name, parameter, after):
    return {
        "name": name, "type": "IfCondition", "dependsOn": dependency(after),
        "typeProperties": {
            "expression": expression("@pipeline().parameters." + parameter),
            "ifTrueActivities": [{"name": "Fail_" + parameter, "type": "Fail", "typeProperties": {
                "message": "Intentional demonstration failure. Rerun with failure parameters false to recover the same durable batch.",
                "errorCode": "DEMO_INJECTED_FAILURE"}}],
            "ifFalseActivities": [],
        },
    }


def definitions():
    state = read_state()
    source, target = state["connections"]["source"], state["connections"]["target"]
    prepare = lookup("Prepare_CDC_batch", source, "EXEC dbo.PrepareCdcBatch;")
    copy = {
        "name": "Copy_CDC_events_to_destination", "type": "Copy",
        "dependsOn": dependency(prepare["name"]), "policy": policy(),
        "typeProperties": {
            "source": {"type": "SqlServerSource",
                "sqlReaderQuery": expression(
                    "@concat('SELECT BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson FROM dbo.CdcOutbox WHERE BatchId = ''', activity('Prepare_CDC_batch').output.firstRow.BatchId, ''';')"),
                "queryTimeout": "00:05:00", "datasetSettings": dataset(source, "CdcOutbox")},
            "sink": {"type": "SqlServerSink", "writeBehavior": "upsert",
                "upsertSettings": {"useTempDB": True, "keys": ["BatchId", "EventNo"]},
                "writeBatchSize": 1000, "writeBatchTimeout": "00:05:00",
                "datasetSettings": dataset(target, "CdcStage")},
            "enableStaging": False, "parallelCopies": 1,
            "translator": {"type": "TabularTranslator", "typeConversion": True,
                "typeConversionSettings": {"allowDataTruncation": False, "treatBooleanAsNumber": False}},
        },
    }
    parameters = {
        key: {"value": expression(f"@activity('Prepare_CDC_batch').output.firstRow.{key}"),
              "type": "Int32" if key == "EventCount" else "String"}
        for key in ("BatchId", "FromLsn", "ToLsn", "EventCount")
    }
    before = failure_injection("Optional_failure_before_apply", "SimulateFailureBeforeApply", copy["name"])
    apply = procedure("Apply_batch_atomically", target, SERVERS["target"]["database"],
                      "dbo.ApplyCdcBatch", parameters, before["name"])
    after = failure_injection("Optional_failure_after_apply", "SimulateFailureAfterApply", apply["name"])
    ack = procedure("Acknowledge_source_checkpoint", source, SERVERS["source"]["database"],
                    "dbo.AcknowledgeCdcBatch", {"BatchId": parameters["BatchId"]}, after["name"])
    sync = {"properties": {
        "description": "Two-server SQL Server Standard CDC replication, including keyless duplicate rows. Durable outbox -> Fabric Copy -> atomic destination apply -> source acknowledgement. One-minute micro-batch, not HA.",
        "parameters": {
            "SimulateFailureBeforeApply": {"type": "bool", "defaultValue": False},
            "SimulateFailureAfterApply": {"type": "bool", "defaultValue": False},
        },
        "concurrency": 1, "activities": [prepare, copy, before, apply, after, ack],
    }}
    generate = {"properties": {
        "description": "Generate idempotent synthetic insert/update/delete rounds on the SOURCE VM only.",
        "parameters": {"Round": {"type": "int", "defaultValue": 1}}, "concurrency": 1,
        "activities": [procedure("Generate_demo_changes", source, SERVERS["source"]["database"],
            "dbo.GenerateDemoChanges", {
                "Round": {"value": expression("@pipeline().parameters.Round"), "type": "Int32"}})],
    }}
    snapshot = lookup("Read_source_snapshot", source, "EXEC dbo.GetReplicaSnapshot;")
    validation = lookup(
        "Compare_destination_multisets", target, after=snapshot["name"],
        stored_procedure="dbo.ValidateReplica",
        parameters={"SourceSnapshot": {"type": "String", "value": expression(
            "@activity('Read_source_snapshot').output.firstRow.SnapshotJson")}},
    )
    validate = {"properties": {
        "description": "Reconcile exact business-row multisets and CDC checkpoints across TWO independent servers. Includes duplicate multiplicity, NULL, case and trailing spaces.",
        "concurrency": 1, "activities": [snapshot, validation],
    }}
    inspections = []
    for role, connection in (("source", source), ("target", target)):
        inspections.append(lookup("Inspect_" + role + "_server", connection,
            "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('ServerName')) AS ServerName,"
            "DB_NAME() AS DatabaseName, CONVERT(nvarchar(128),SERVERPROPERTY('Edition')) AS SqlEdition,"
            "CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')) AS SqlVersion,"
            "(SELECT COUNT(*) FROM sys.tables WHERE schema_id=SCHEMA_ID('dbo')"
            " AND name IN ('Customers','Products','LegacyBalances','EventLog')"
            " AND NOT EXISTS(SELECT 1 FROM sys.indexes i WHERE i.object_id=sys.tables.object_id"
            " AND i.is_primary_key=1)) AS BusinessTablesWithoutPrimaryKey;"))
        inspections.append(lookup("Inspect_" + role + "_checkpoint", connection,
                                  "EXEC dbo.GetReplicationStatus;", inspections[-1]["name"]))
    inspect = {"properties": {
        "description": "Inspect separate source/destination SQL Server Standard instances, keyless table count and checkpoints.",
        "activities": inspections,
    }}
    return {"pl_cdc_sync": sync, "pl_generate_demo_changes": generate,
            "pl_validate_replica": validate, "pl_inspect_sql_demo": inspect}


def wait_operation(response, result_required=False):
    location = response.headers["Location"]
    deadline = time.time() + 1200
    while time.time() < deadline:
        time.sleep(int(response.headers.get("Retry-After", "5")))
        response = call("GET", location)
        data = response.json()
        if data.get("status") == "Succeeded":
            return call("GET", location.rstrip("/") + "/result") if result_required else response
        if data.get("status") in ("Failed", "Canceled"):
            raise RuntimeError(json.dumps(data))
    raise TimeoutError("Fabric operation did not complete.")


def deploy_pipelines():
    base = workspace_url()
    items = collection(base + "/items?type=DataPipeline")
    ids = read_state().get("pipelines", {})
    for name, content in definitions().items():
        (ROOT / (name + ".json")).write_text(json.dumps(content, indent=2), encoding="utf-8")
        definition = {"parts": [{"path": "pipeline-content.json",
            "payload": base64.b64encode(json.dumps(content).encode()).decode(),
            "payloadType": "InlineBase64"}]}
        matches = [item for item in items if item["displayName"] == name]
        if len(matches) > 1:
            raise RuntimeError("Pipeline display name is ambiguous: " + name)
        if matches:
            item_id = matches[0]["id"]
            response = call("POST", base + f"/items/{item_id}/updateDefinition", {"definition": definition})
            if response.status_code == 202:
                wait_operation(response)
        else:
            response = call("POST", base + "/items", {
                "displayName": name, "type": "DataPipeline",
                "description": content["properties"]["description"], "definition": definition})
            if response.status_code == 202:
                response = wait_operation(response, result_required=True)
            item_id = response.json()["id"]
        ids[name] = item_id
        save_state(pipelines=ids)
        print(f"Pipeline {name}: {item_id}", flush=True)


def activity_runs(run_id):
    now = datetime.now(timezone.utc)
    result = call("POST", workspace_url() + f"/datapipelines/pipelineruns/{run_id}/queryactivityruns", {
        "filters": [], "orderBy": [{"orderBy": "ActivityRunStart", "order": "ASC"}],
        "lastUpdatedAfter": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastUpdatedBefore": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }).json()
    return result if isinstance(result, list) else result["value"]


def record_run(name, result, elapsed=None):
    if elapsed is not None:
        result["elapsedWallSeconds"] = round(elapsed, 1)
    activities = activity_runs(result["id"])
    directory = ROOT / "runs"
    directory.mkdir(exist_ok=True)
    prefix = directory / (name + "-" + result["id"])
    prefix.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    prefix.with_name(prefix.name + "-activities.json").write_text(
        json.dumps(activities, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    for item in activities:
        output = item.get("output") or {}
        first_row = output.get("firstRow") or {}
        first_row = {key: value for key, value in first_row.items() if key != "SnapshotJson"}
        print(json.dumps({
            "activity": item.get("activityName"), "status": item.get("status"),
            "durationMs": item.get("durationInMs"), "error": item.get("error"),
            "firstRow": first_row, "rowsCopied": output.get("rowsCopied"),
        }), flush=True)
    return activities


def run(name, round_number=None, failure=None, expect_failure=False):
    item_id = read_state()["pipelines"][name]
    parameters = {}
    if round_number is not None:
        parameters["Round"] = round_number
    if failure:
        parameters[failure] = True
    body = {"executionData": {"parameters": parameters}} if parameters else None
    response = call("POST", workspace_url() + f"/items/{item_id}/jobs/instances?jobType=Pipeline", body)
    location = response.headers["Location"]
    print("Run started: " + location, flush=True)
    start = time.time()
    while time.time() - start < 1200:
        result = call("GET", location).json()
        if result["status"] in ("Completed", "Failed", "Cancelled", "Deduped"):
            activities = record_run(name, result, time.time() - start)
            expected = "Failed" if expect_failure else "Completed"
            if result["status"] != expected:
                raise RuntimeError("Unexpected Fabric pipeline status.")
            if expect_failure and not any(
                item.get("activityName") == "Fail_" + failure and item.get("status") == "Failed"
                for item in activities
            ):
                raise RuntimeError("Pipeline failed for a reason other than the intended injection.")
            return result
        time.sleep(8)
    raise TimeoutError("Pipeline did not complete within 20 minutes.")


def schedule():
    state = read_state()
    base = workspace_url() + f"/items/{state['pipelines']['pl_cdc_sync']}/jobs/Pipeline/schedules"
    if state.get("schedule"):
        result = call("GET", base + "/" + state["schedule"]["id"]).json()
        if not result["enabled"] or result["configuration"]["interval"] != 1:
            raise RuntimeError("Existing schedule differs from the intended enabled one-minute interval.")
    else:
        now = datetime.now(timezone.utc)
        result = call("POST", base, {"enabled": True, "configuration": {
            "type": "Cron", "interval": 1, "localTimeZoneId": "UTC",
            "startDateTime": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }}).json()
        save_state(schedule=result)
    print(json.dumps(result, indent=2))
    return result


def set_schedule_enabled(enabled):
    state = read_state()
    endpoint = (workspace_url() + f"/items/{state['pipelines']['pl_cdc_sync']}"
                + "/jobs/Pipeline/schedules/" + state["schedule"]["id"])
    current = call("GET", endpoint).json()
    body = {"enabled": enabled, "configuration": current["configuration"]}
    if "executionData" in current:
        body["executionData"] = current["executionData"]
    result = call("PATCH", endpoint, body).json()
    save_state(schedule=result)
    print("Synchronization schedule enabled: " + str(result["enabled"]), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["connections", "deploy", "run", "schedule", "pause", "resume"])
    parser.add_argument("--pipeline", default="pl_cdc_sync")
    parser.add_argument("--round", type=int)
    parser.add_argument("--failure", choices=["SimulateFailureBeforeApply", "SimulateFailureAfterApply"])
    args = parser.parse_args()
    if args.action == "connections":
        create_connections()
    elif args.action == "deploy":
        deploy_pipelines()
    elif args.action == "run":
        run(args.pipeline, args.round, args.failure, bool(args.failure))
    elif args.action == "schedule":
        schedule()
    else:
        set_schedule_enabled(args.action == "resume")
