"""Poll the DTB on-demand flow until the newest job finishes; print Spark batch states along the way."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import call, find_item, ids  # noqa: E402

I = ids()
ws = I["workspaceId"]
flow = find_item(ws, "DigitalTwinBuilderFlow", "dtb_dt_factoryOnDemand", "eventhouse-cli")["id"]
since = sys.argv[1] if len(sys.argv) > 1 else ""
t0 = time.time()
while time.time() - t0 < 1500:
    jobs = call("GET", f"workspaces/{ws}/items/{flow}/jobs/instances", "eventhouse-cli").get("value", [])
    jobs = [j for j in jobs if (j.get("startTimeUtc") or "") >= since]
    sess = call("GET", f"workspaces/{ws}/spark/livySessions", "spark-cli").get("value", [])
    sess = [s for s in sess if (s.get("submittedDateTime") or "") >= since[:19]]
    print(time.strftime("%H:%M:%S"), [(j["status"], j.get("startTimeUtc", "")[11:19]) for j in jobs[:2]],
          [(s["state"], s.get("submittedDateTime", "")[11:19]) for s in sess[:4]], flush=True)
    if jobs and jobs[0]["status"] in ("Completed", "Failed", "Cancelled", "Deduped"):
        print("FINAL", jobs[0]["status"], json.dumps(jobs[0].get("failureReason")))
        break
    time.sleep(30)
