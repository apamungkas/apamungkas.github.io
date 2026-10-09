"""Minimal Fabric / Kusto / OneLake REST helper for the Digital Twin demo (stdlib only).

Tokens come from the signed-in Azure CLI account. Every api.fabric.microsoft.com call carries
the x-ms-fabric-skill attribution header required by the Fabric skills.
"""
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

FABRIC = "https://api.fabric.microsoft.com"
KUSTO_RESOURCE = "https://kusto.kusto.windows.net"
STORAGE_RESOURCE = "https://storage.azure.com"
HERE = os.path.dirname(os.path.abspath(__file__))
IDS_FILE = os.path.join(os.path.dirname(HERE), "ids.json")
_tokens = {}


class FabricError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} for {url}: {body[:2000]}")
        self.status, self.body, self.url = status, body, url


def token(resource=FABRIC):
    cached = _tokens.get(resource)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    az = shutil.which("az") or "az"
    out = subprocess.run([az, "account", "get-access-token", "--resource", resource, "-o", "json"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    j = json.loads(out.stdout)
    exp = float(j.get("expires_on") or (time.time() + 3000))
    _tokens[resource] = (j["accessToken"], exp)
    return j["accessToken"]


def _url(path):
    if path.startswith("http"):
        return path
    return FABRIC + "/v1/" + path.lstrip("/")


def call(method, path, skill, body=None, resource=FABRIC, headers=None, lro=True, timeout=1800, raw_body=None):
    url = _url(path) if resource == FABRIC else path
    h = {"Authorization": f"Bearer {token(resource)}"}
    if url.startswith(FABRIC):
        h["x-ms-fabric-skill"] = skill
    data = None
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    elif method.upper() == "POST":
        data = b"{}" if url.startswith(FABRIC) else b""
        if url.startswith(FABRIC):
            h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    for attempt in range(10):
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                status, rh, content = r.status, r.headers, r.read()
            break
        except urllib.error.HTTPError as e:
            status, rh, content = e.code, e.headers, e.read()
            if (status == 429 or status >= 500) and attempt < 9:
                wait = int(rh.get("Retry-After") or 10)
                time.sleep(min(max(wait, 3), 60))
                continue
            raise FabricError(status, content.decode("utf-8", "ignore"), url)
    if status == 202 and lro and rh.get("Location"):
        return poll(rh, skill, timeout)
    if not content:
        return {"_status": status, "_location": rh.get("Location"), "_operation": rh.get("x-ms-operation-id")}
    try:
        return json.loads(content)
    except ValueError:
        return content.decode("utf-8", "ignore")


def poll(rh, skill, timeout=1800):
    loc = rh.get("Location")
    op_id = rh.get("x-ms-operation-id")
    if op_id:
        loc = f"{FABRIC}/v1/operations/{op_id}"
    wait = int(rh.get("Retry-After") or 5)
    t0 = time.time()
    while True:
        time.sleep(min(max(wait, 3), 20))
        st = call("GET", loc, skill, lro=False)
        status = (st.get("status") or "") if isinstance(st, dict) else ""
        if status in ("Succeeded", "Completed"):
            if op_id and "/operations/" in loc:
                try:
                    return call("GET", f"operations/{op_id}/result", skill, lro=False)
                except FabricError as e:
                    if e.status in (400, 404):
                        return st
                    raise
            return st
        if status in ("Failed", "Cancelled", "Canceled", "Deduped"):
            raise FabricError(0, json.dumps(st), loc)
        if time.time() - t0 > timeout:
            raise TimeoutError(f"LRO timeout: {loc}")


def b64(obj):
    raw = obj if isinstance(obj, (bytes, bytearray)) else (obj if isinstance(obj, str) else json.dumps(obj, indent=2)).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def unb64(s):
    return base64.b64decode(s).decode("utf-8")


def list_all(path, skill):
    items, url = [], path
    while url:
        r = call("GET", url, skill)
        items += r.get("value", [])
        url = r.get("continuationUri")
    return items


def find_item(ws_id, item_type, name, skill):
    for it in list_all(f"workspaces/{ws_id}/items?type={item_type}", skill):
        if it["displayName"] == name:
            return it
    return None


def ids(update=None):
    data = {}
    if os.path.exists(IDS_FILE):
        data = json.load(open(IDS_FILE, encoding="utf-8"))
    if update:
        data.update(update)
        with open(IDS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    return data


def kusto(query_uri, db, csl, mgmt=True, timeout=600):
    endpoint = "/v1/rest/mgmt" if mgmt else "/v2/rest/query"
    body = {"db": db, "csl": csl}
    if not mgmt:
        body["properties"] = json.dumps({"Options": {"servertimeout": "00:10:00"}})
    r = call("POST", query_uri.rstrip("/") + endpoint, "eventhouse-cli", body=body, resource=KUSTO_RESOURCE)
    return kusto_rows(r, mgmt)


def kusto_rows(r, mgmt=True):
    if mgmt:
        t = r["Tables"][0]
        cols = [c["ColumnName"] for c in t["Columns"]]
        return [dict(zip(cols, row)) for row in t["Rows"]]
    for frame in r:
        if frame.get("FrameType") == "DataTable" and frame.get("TableKind") == "PrimaryResult":
            cols = [c["ColumnName"] for c in frame["Columns"]]
            return [dict(zip(cols, row)) for row in frame["Rows"]]
    return []


def onelake_upload(ws_id, item_id, rel_path, data):
    """Upload bytes to OneLake (DFS create/append/flush)."""
    base = f"https://onelake.dfs.fabric.microsoft.com/{ws_id}/{item_id}/{urllib.parse.quote(rel_path)}"
    hdr = {"Authorization": f"Bearer {token(STORAGE_RESOURCE)}", "x-ms-version": "2023-11-03"}
    for url, method, payload in ((base + "?resource=file", "PUT", b""),
                                 (base + "?action=append&position=0", "PATCH", data),
                                 (base + f"?action=flush&position={len(data)}", "PATCH", b"")):
        req = urllib.request.Request(url, data=payload, method=method, headers=hdr)
        with urllib.request.urlopen(req, timeout=600) as r:
            r.read()
