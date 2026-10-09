"""Patch rtd_dt_factory: full-width 24-column layout (fixes multiStat minimum size and map fit) and a clearer OEE chart."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fabric_api import b64, call, ids  # noqa: E402

SK = "eventhouse-cli"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEF = os.path.join(ROOT, "definitions", "rtd_dt_factory.json")

LAYOUT = {
    "Plant map - Site health": (0, 0, 8, 8),
    "Machine health and production": (8, 0, 16, 8),
    "Machines needing attention": (0, 8, 12, 7),
    "Air compressor vibration (mm/s)": (12, 8, 12, 7),
    "Injection molder temperature (°C)": (0, 15, 12, 7),
    "OEE by line (24h)": (12, 15, 12, 7),
    "Recent anomalies": (0, 22, 12, 7),
    "Health score ranking (last hour)": (12, 22, 12, 7),
    "Selected machine health": (0, 0, 9, 7),
    "Machine temperature (°C)": (9, 0, 15, 7),
    "Machine vibration (mm/s)": (0, 7, 12, 7),
    "Machine power (kW)": (12, 7, 12, 7),
    "Machine RPM": (0, 14, 24, 7),
}


def main():
    d = json.load(open(DEF, encoding="utf-8"))
    qs = {q["id"]: q for q in d["queries"]}
    for t in d["tiles"]:
        if t["title"] in LAYOUT:
            x, y, w, h = LAYOUT[t["title"]]
            t["layout"] = {"x": x, "y": y, "width": w, "height": h}
        else:
            print("no layout for", t["title"])
        if t["title"].startswith("OEE"):
            q = qs[t["queryRef"]["queryId"]]
            q["text"] = q["text"].replace("| project LineName, SiteId, LineType,", "| project LineId, LineName, SiteId, LineType,")
            vo = t["visualOptions"]
            vo["xColumn"] = "LineId"
            vo["yAxisMinimumValue"] = 90
            vo["yAxisMaximumValue"] = 100
            vo["multipleYAxes"]["base"]["yAxisMinimumValue"] = 90
            vo["multipleYAxes"]["base"]["yAxisMaximumValue"] = 100
    json.dump(d, open(DEF, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    I = ids()
    ws, item = I["workspaceId"], I["dashboardId"]
    call("POST", f"workspaces/{ws}/items/{item}/updateDefinition", SK,
         body={"definition": {"parts": [{"path": "RealTimeDashboard.json", "payload": b64(json.dumps(d, ensure_ascii=False)), "payloadType": "InlineBase64"}]}})
    got = call("POST", f"workspaces/{ws}/items/{item}/getDefinition", SK, body={})
    part = next(p for p in got["definition"]["parts"] if p["path"] == "RealTimeDashboard.json")
    import base64
    back = json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
    print("tiles:", [(t["title"], t["layout"]["width"], t["layout"]["height"]) for t in back["tiles"]][:4], "...")


if __name__ == "__main__":
    main()
