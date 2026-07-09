import azure.functions as func
import logging
import os, json, requests, tempfile
from datetime import datetime, timezone

from azure.eventhub import EventHubProducerClient, EventData

app = func.FunctionApp()

# Create producer once (module scope)
producer = EventHubProducerClient.from_connection_string(
    conn_str=os.environ["EH_CONN_STR"],
    eventhub_name=os.environ["EH_NAME"]
)

@app.route(route="ingest", auth_level=func.AuthLevel.FUNCTION, methods=["POST"])
def ingest(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        deviceId = body.get("deviceId", "m5fire-01")
        tempC = float(body["tempC"])
        humidity = float(body["humidity"])
        ts = body.get("ts") or datetime.now(timezone.utc).isoformat()

        payload = {"deviceId": deviceId, "ts": ts, "tempC": tempC, "humidity": humidity}

        batch = producer.create_batch()
        batch.add(EventData(json.dumps(payload)))
        producer.send_batch(batch)

        return func.HttpResponse("ok", status_code=200)

    except Exception as e:
        logging.exception("Ingest failed")
        return func.HttpResponse(f"error: {str(e)}", status_code=400)
