import argparse
import json
import time
from datetime import datetime, timezone

from deploy import ROOT, call, collection
from fabric import activity_runs, record_run, run, schedule, set_schedule_enabled, workspace_url

RESULT = ROOT / "exercise-results.json"


def save(results):
    RESULT.write_text(json.dumps(results, indent=2), encoding="utf-8")


def job_step(results, key, pipeline, **kwargs):
    if key not in results:
        print("Executing " + key, flush=True)
        results[key] = run(pipeline, **kwargs)
        save(results)
    return results[key]


def first_row(job, name):
    rows = [item["output"]["firstRow"] for item in activity_runs(job["id"])
            if item.get("activityName") == name and item["status"] == "Succeeded"]
    if len(rows) != 1:
        raise RuntimeError("Expected one successful activity: " + name)
    return rows[0]


def validate(results, key):
    job = job_step(results, key, "pl_validate_replica")
    row = first_row(job, "Compare_destination_multisets")
    if row["IsValid"] not in (True, 1):
        raise RuntimeError("Replica reconciliation did not succeed.")
    return row


def manual():
    results = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    job_step(results, "baseline", "pl_cdc_sync")
    results["baselineSummary"] = validate(results, "baselineValidation")
    inspection = job_step(results, "serverInspection", "pl_inspect_sql_demo")
    source = first_row(inspection, "Inspect_source_server")
    destination = first_row(inspection, "Inspect_target_server")
    if source["ServerName"] == destination["ServerName"]:
        raise RuntimeError("Source and destination are not independent SQL Server instances.")
    for row in (source, destination):
        if "Standard" not in row["SqlEdition"] or row["BusinessTablesWithoutPrimaryKey"] != 3:
            raise RuntimeError("SQL edition or keyless business-table count differs from the intended demo.")
    results["servers"] = {"source": source, "destination": destination}
    save(results)
    job_step(results, "generateRound1", "pl_generate_demo_changes", round_number=1)
    time.sleep(8)
    failed = job_step(results, "failedBeforeApply", "pl_cdc_sync",
                      failure="SimulateFailureBeforeApply", expect_failure=True)
    manifest = first_row(failed, "Prepare_CDC_batch")
    if manifest["EventCount"] <= 0:
        raise RuntimeError("Round 1 changes were not captured before the recovery demonstration.")
    recovered = job_step(results, "recoverBeforeApply", "pl_cdc_sync")
    if manifest != first_row(recovered, "Prepare_CDC_batch"):
        raise RuntimeError("Recovery did not reuse the immutable pending source batch.")
    results["round1Summary"] = validate(results, "round1Validation")
    job_step(results, "generateRound2", "pl_generate_demo_changes", round_number=2)
    time.sleep(8)
    failed = job_step(results, "failedAfterApply", "pl_cdc_sync",
                      failure="SimulateFailureAfterApply", expect_failure=True)
    manifest = first_row(failed, "Prepare_CDC_batch")
    if manifest["EventCount"] <= 0:
        raise RuntimeError("Round 2 changes were not captured before the acknowledgement recovery demonstration.")
    recovered = job_step(results, "recoverAfterApply", "pl_cdc_sync")
    if manifest != first_row(recovered, "Prepare_CDC_batch"):
        raise RuntimeError("Post-commit recovery did not replay the exact committed destination batch.")
    results["round2Summary"] = validate(results, "round2Validation")
    job_step(results, "repeatGeneratorRound2", "pl_generate_demo_changes", round_number=2)
    empty = job_step(results, "emptyBatch", "pl_cdc_sync")
    if first_row(empty, "Prepare_CDC_batch")["EventCount"] != 0:
        raise RuntimeError("Repeating a generator round unexpectedly produced business changes.")
    results["finalManualSummary"] = validate(results, "finalManualValidation")
    results["manualComplete"] = True
    save(results)
    print(json.dumps({"manualComplete": True, "servers": results["servers"],
                      "summary": results["finalManualSummary"]}, indent=2), flush=True)


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def normal_writes():
    results = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    if not results.get("manualComplete"):
        raise RuntimeError("Complete the manual exercise and normal-writes.sql first.")
    synced = job_step(results, "normalSourceWritesSync", "pl_cdc_sync")
    manifest = first_row(synced, "Prepare_CDC_batch")
    if manifest["EventCount"] != 5:
        raise RuntimeError("Expected five committed CDC images from ordinary keyless source DML.")
    summary = validate(results, "normalSourceWritesValidation")
    baseline = results["finalManualSummary"]
    for table in ("Customers", "Products", "LegacyBalances", "EventLog"):
        expected = baseline[table] + (1 if table == "LegacyBalances" else 0)
        if summary[table] != expected:
            raise RuntimeError("Normal source SQL did not preserve the expected duplicate multiplicity.")
    results["normalSourceWritesSummary"] = summary
    save(results)
    print(json.dumps({"normalSourceWritesManifest": manifest, "summary": summary}, indent=2))


def scheduled():
    results = json.loads(RESULT.read_text()) if RESULT.exists() else {}
    if not results.get("manualComplete"):
        raise RuntimeError("Complete the manual CDC and recovery exercise before scheduling.")
    path = ROOT / "schedule-results.json"
    progress = json.loads(path.read_text()) if path.exists() else {}
    if progress.get("complete"):
        print(json.dumps(progress, indent=2))
        return
    if "schedule" not in progress:
        progress["schedule"] = schedule()
        path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
    if "generatorJob" not in progress:
        progress["generatorJob"] = run("pl_generate_demo_changes", round_number=3)
        path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
    from deploy import read_state
    item_id = read_state()["pipelines"]["pl_cdc_sync"]
    after = parse_time(progress["generatorJob"]["endTimeUtc"])
    deadline = time.time() + 900
    seen = set()
    print("Waiting for an actual scheduled run to transfer round 3.", flush=True)
    while time.time() < deadline:
        for job in collection(workspace_url() + f"/items/{item_id}/jobs/instances"):
            if (job["id"] in seen or job["status"] != "Completed"
                    or job.get("invokeType") == "Manual"
                    or parse_time(job["endTimeUtc"]) < after):
                continue
            seen.add(job["id"])
            manifest = first_row(job, "Prepare_CDC_batch")
            if manifest["EventCount"] <= 0:
                continue
            record_run("pl_cdc_sync", job)
            set_schedule_enabled(False)
            drain_deadline = time.time() + 600
            while True:
                active = [item for item in collection(
                    workspace_url() + f"/items/{item_id}/jobs/instances")
                    if item["status"] not in ("Completed", "Failed", "Cancelled", "Deduped")]
                if not active:
                    break
                if time.time() > drain_deadline:
                    raise TimeoutError("In-flight scheduled runs did not drain before reconciliation.")
                time.sleep(8)
            validation = run("pl_validate_replica")
            summary = first_row(validation, "Compare_destination_multisets")
            if summary["IsValid"] not in (True, 1):
                raise RuntimeError("Scheduled synchronization did not reconcile.")
            progress["schedule"] = set_schedule_enabled(True)
            progress.update(
                complete=True, scheduledJob=job, manifest=manifest,
                validationJob=validation, summary=summary,
                secondsFromGeneratorCompletionToSyncCompletion=round(
                    (parse_time(job["endTimeUtc"]) - after).total_seconds(), 1),
            )
            path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
            print(json.dumps(progress, indent=2), flush=True)
            return
        time.sleep(10)
    raise TimeoutError("No completed scheduled CDC batch containing round 3 appeared within 15 minutes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["manual", "normal", "scheduled"])
    args = parser.parse_args()
    if args.phase == "manual":
        manual()
    elif args.phase == "normal":
        normal_writes()
    else:
        scheduled()
