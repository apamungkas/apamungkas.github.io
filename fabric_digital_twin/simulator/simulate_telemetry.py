"""Live machine telemetry simulator for the PT Nusantara Manufaktur digital twin demo.

Sends one JSON event per machine every `intervalSeconds` to the Eventstream Custom Endpoint
(es-dt-telemetry -> eh_dt_factory.MachineTelemetry). The connection string is read from
%USERPROFILE%\\.secrets\\fabric-dt-demo.env and is never printed.

Edit simulator/scenario.json while it runs to trigger demo situations (re-read every tick):
  bearingWear     compressor M-CKR-L2-04 keeps its high vibration (bearing wear)
  overheatSchedule injection molder M-SBY-L1-01 overheats at minutes 08-24 of every hour
  forceOverheat   injection molder M-SBY-L1-01 overheats now (live alert demo)
  conveyorFault   conveyor M-KRW-L2-03 faults and both packers on the line become Blocked
  vibrationSpikeMachine  any MachineId gets +6.5 mm/s vibration (live alert demo), "" to disable

Usage: python simulate_telemetry.py [--minutes 480] [--once]
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import time

from azure.eventhub import EventData, EventHubProducerClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECRET = os.path.join(os.path.expanduser("~"), ".secrets", "fabric-dt-demo.env")
SCENARIO_FILE = os.path.join(HERE, "scenario.json")
PID_FILE = os.path.join(HERE, "simulator.pid")
LOG_FILE = os.path.join(HERE, "simulator.log")
DEFAULT_SCENARIO = {"intervalSeconds": 10, "bearingWear": True, "overheatSchedule": True, "forceOverheat": False,
                    "conveyorFault": False, "vibrationSpikeMachine": ""}


def log(msg):
    line = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def load_env():
    env = {}
    with open(SECRET, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


def scenario():
    s = dict(DEFAULT_SCENARIO)
    if os.path.exists(SCENARIO_FILE):
        try:
            s.update(json.load(open(SCENARIO_FILE, encoding="utf-8")))
        except (ValueError, OSError):
            pass
    else:
        json.dump(s, open(SCENARIO_FILE, "w", encoding="utf-8"), indent=2)
    return s


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def stochastic_round(x):
    x = max(0.0, x)
    return int(math.floor(x + random.random()))


def build_events(now, md, anchors, sc, interval):
    P = md["profiles"]
    local = now + dt.timedelta(hours=7)
    load_base = (0.88 if 6 <= local.hour < 22 else 0.64) * (0.6 if local.weekday() == 6 else 1.0)
    wear_start = dt.datetime.fromisoformat(anchors["wearStart"].replace("Z", "+00:00"))
    wear = min(1.0, (now - wear_start).total_seconds() / (anchors["wearDays"] * 86400.0)) if sc["bearingWear"] else 0.0
    scheduled_overheat = sc["overheatSchedule"] and anchors["overheatMinuteFrom"] <= now.minute <= anchors["overheatMinuteTo"]
    events = []
    for m in md["machines"]:
        p = P[m["MachineType"]]
        mid = m["MachineId"]
        load = load_base + (random.random() - 0.5) * 0.06
        status = "Idle" if random.random() < 0.004 * interval / 60.0 else "Running"
        if sc["conveyorFault"] and mid == anchors["faultMachine"]:
            status = "Fault"
        elif sc["conveyorFault"] and mid in anchors["blockedMachines"]:
            status = "Blocked"
        w = wear if mid == anchors["wearMachine"] else 0.0
        oh = 1.0 if mid == anchors["overheatMachine"] and (scheduled_overheat or sc["forceOverheat"]) else 0.0
        spike = 6.5 if sc.get("vibrationSpikeMachine") == mid else 0.0
        active = 1.0 if status == "Running" else 0.0
        temp = p["BaseTempC"] + p["TempLoadGainC"] * load * active + 6.0 * w + 27.0 * oh + (random.random() - 0.5) * 2.0 - (6.0 if not active else 0.0)
        vib = (p["BaseVibMmS"] * (0.8 + 0.4 * load) + 6.0 * w * w + 0.6 * oh + spike + (random.random() - 0.5) * 0.35) if active else 0.05 + random.random() * 0.1
        pres = (p["BasePressureBar"] * (0.97 + 0.06 * random.random()) - (0.4 * w if m["MachineType"] == "AirCompressor" else 0.0)) if active else p["BasePressureBar"] * 0.1
        power = (p["RatedPowerKw"] * (0.35 + 0.55 * load) * (1.0 + 0.08 * w) + (random.random() - 0.5) * p["RatedPowerKw"] * 0.03) if active else p["RatedPowerKw"] * 0.04
        rpm = (p["NominalRpm"] * (0.96 + 0.06 * load) + (random.random() - 0.5) * p["NominalRpm"] * 0.01) if active else 0.0
        units = stochastic_round(p["UnitsPerMin"] * interval / 60.0 * active * (0.75 + 0.3 * load) * (1.0 - 0.15 * oh))
        rejected = stochastic_round(units * (p["RejectRate"] + 0.11 * oh) * (0.5 + random.random()))
        events.append({"Timestamp": iso(now), "SiteId": m["SiteId"], "LineId": m["LineId"], "MachineId": mid,
                       "MachineType": m["MachineType"], "Status": status, "TemperatureC": round(temp, 2),
                       "VibrationMmS": round(vib, 3), "PressureBar": round(pres, 2), "PowerKw": round(power, 2),
                       "Rpm": round(rpm, 1), "UnitsProduced": units, "UnitsRejected": rejected})
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=480)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    md = json.load(open(os.path.join(ROOT, "data", "master_data.json"), encoding="utf-8"))
    anchors = json.load(open(os.path.join(ROOT, "data", "scenario_anchors.json"), encoding="utf-8"))
    env = load_env()
    cs, hub = env["EVENTHUB_CONNECTION_STRING"], env.get("EVENTHUB_NAME") or None
    kwargs = {} if "EntityPath=" in cs else {"eventhub_name": hub}
    producer = EventHubProducerClient.from_connection_string(cs, **kwargs)
    with open(PID_FILE, "w") as fh:
        fh.write(str(os.getpid()))
    deadline = time.time() + a.minutes * 60
    sent = 0
    log(f"simulator started (pid {os.getpid()}, {len(md['machines'])} machines, runs {a.minutes:.0f} min)")
    try:
        with producer:
            while True:
                sc = scenario()
                interval = max(2, int(sc.get("intervalSeconds", 10)))
                now = dt.datetime.now(dt.timezone.utc)
                batch = producer.create_batch()
                for e in build_events(now, md, anchors, sc, interval):
                    batch.add(EventData(json.dumps(e)))
                producer.send_batch(batch)
                sent += len(batch)
                if a.once:
                    log(f"sent {sent} events (once)")
                    break
                if sent % (len(md["machines"]) * 30) == 0:
                    active = [k for k in ("forceOverheat", "conveyorFault") if sc.get(k)] + ([f"spike:{sc['vibrationSpikeMachine']}"] if sc.get("vibrationSpikeMachine") else [])
                    log(f"sent {sent} events; active triggers: {active or 'none'}")
                if time.time() > deadline:
                    log("duration reached, stopping")
                    break
                time.sleep(max(0.5, interval - (dt.datetime.now(dt.timezone.utc) - now).total_seconds()))
    except KeyboardInterrupt:
        log("stopped by user")
    finally:
        if os.path.exists(PID_FILE) and open(PID_FILE).read().strip() == str(os.getpid()):
            os.remove(PID_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
