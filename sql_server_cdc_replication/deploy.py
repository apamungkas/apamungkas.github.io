import argparse
import base64
import ipaddress
import json
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "deployment.json"
LAB_SUFFIX = "replace_me"
SUB = "REPLACE_WITH_YOUR_SUB"
TENANT = "REPLACE_WITH_YOUR_TENANT"
USER_ID = "REPLACE_WITH_YOUR_USER_ID"
UPN = "REPLACE_WITH_YOUR_UPN"
RG = f"rg-sqlsync-{LAB_SUFFIX}"
REGION = "southeastasia"
VAULT = f"kv-sqlsync-{LAB_SUFFIX}"
CAPACITY = f"sqlsync{LAB_SUFFIX}"
WS_NAME = f"ws_demo_sqlsync_{LAB_SUFFIX}"
GATEWAY_NAME = f"gw_sqlsync_{LAB_SUFFIX}"
CONNECTION_PREFIX = f"cn_sqlsync_{LAB_SUFFIX}_"









BASTION_NAME = "bastion-sqlsync"
BASTION_PREFIX = "10.74.30.64/26"
BASTION_PUBLIC_IP = "pip-sqlsync-bastion"
FABRIC = "https://api.fabric.microsoft.com/v1"
ARM = f"https://management.azure.com/subscriptions/{SUB}/resourceGroups/{RG}"
SERVERS = {
    "source": {"vm": "vm-sqlsync-src", "computer": "sqlsyncsrc",
               "ip": "10.74.30.4", "database": "SqlSyncSource"},
    "target": {"vm": "vm-sqlsync-dst", "computer": "sqlsyncdst",
               "ip": "10.74.30.5", "database": "SqlSyncDestination"},
}
TOKENS = {}
HTTP = requests.Session()


def token(audience):
    cached = TOKENS.get(audience)
    if cached and time.time() - cached[0] < 2400:
        return cached[1]
    result = subprocess.run(
        ["az.cmd", "account", "get-access-token", "--resource", audience,
         "--query", "accessToken", "--output", "tsv", "--only-show-errors"],
        text=True, capture_output=True, check=True, timeout=120,
    )
    value = result.stdout.strip()
    if not value:
        raise RuntimeError("Azure CLI did not return an access token.")
    TOKENS[audience] = (time.time(), value)
    return value


def call(method, url, body=None, audience=None):
    is_fabric = url.startswith("https://api.fabric.microsoft.com/")
    audience = audience or ("https://api.fabric.microsoft.com" if is_fabric
                            else "https://management.azure.com/")
    for attempt in range(5):
        headers = {"Authorization": "Bearer " + token(audience)}
        if is_fabric:
            headers["x-ms-fabric-skill"] = "pipeline-migration"
        try:
            response = HTTP.request(method, url, headers=headers, json=body, timeout=(20, 120))
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
            if method != "GET" or attempt == 4:
                raise
            print(f"Transient GET timeout; retrying {url}", flush=True)
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code == 429 and attempt < 4:
            time.sleep(int(response.headers.get("Retry-After", "15")))
            continue
        if method == "GET" and response.status_code in (502, 503, 504) and attempt < 4:
            time.sleep(10 * (attempt + 1))
            continue
        if not response.ok:
            raise RuntimeError(f"{method} {url}: {response.status_code} {response.text[:6000]}")
        return response
    raise RuntimeError("HTTP retry limit exceeded.")


def read_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(**updates):
    state = read_state()
    state.update(updates)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def collection(url):
    values = []
    while url:
        data = call("GET", url).json()
        values.extend(data["value"])
        url = data.get("continuationUri")
    return values


def resource_id(kind, name):
    return f"/subscriptions/{SUB}/resourceGroups/{RG}/providers/{kind}/{name}"


def secret(name):
    return call("GET", f"https://{VAULT}.vault.azure.net/secrets/{name}?api-version=7.4",
                audience="https://vault.azure.net").json()["value"]


def check_identity():
    result = subprocess.run(
        ["az.cmd", "account", "show", "--output", "json"],
        text=True, capture_output=True, check=True, timeout=60,
    )
    account = json.loads(result.stdout)
    if (account["id"] != SUB or account["tenantId"] != TENANT
            or account["user"]["name"].lower() != UPN.lower()):
        raise RuntimeError("Azure CLI identity/subscription differs from the requested deployment.")


def bastion_rdp_rule():
    return {"name": "RDP-from-Azure-Bastion", "properties": {
        "priority": 110, "access": "Allow", "direction": "Inbound",
        "protocol": "Tcp", "sourcePortRange": "*", "destinationPortRange": "3389",
        "sourceAddressPrefix": BASTION_PREFIX, "destinationAddressPrefix": "*"}}


def bastion_resources(tags, subnet_dependency):
    pip = resource_id("Microsoft.Network/publicIPAddresses", BASTION_PUBLIC_IP)
    subnet = resource_id("Microsoft.Network/virtualNetworks", "vnet-sqlsync") + "/subnets/AzureBastionSubnet"
    return [
        {
            "type": "Microsoft.Network/publicIPAddresses", "apiVersion": "2023-11-01",
            "name": BASTION_PUBLIC_IP, "location": REGION, "tags": tags,
            "sku": {"name": "Standard"},
            "properties": {"publicIPAllocationMethod": "Static", "publicIPAddressVersion": "IPv4"},
        },
        {
            "type": "Microsoft.Network/bastionHosts", "apiVersion": "2023-11-01",
            "name": BASTION_NAME, "location": REGION, "tags": tags,
            "sku": {"name": "Basic"}, "dependsOn": [pip, subnet_dependency],
            "properties": {"ipConfigurations": [{
                "name": "bastion-ipconfig", "properties": {
                   "subnet": {"id": subnet}, "publicIPAddress": {"id": pip},
                   "privateIPAllocationMethod": "Dynamic",
                },
            }]},
        },
    ]


def template():
    tags = {"purpose": "FabricPipeline-SQLServer-CDC-demo", "owner": UPN,
            "environment": "demonstration"}
    vnet = resource_id("Microsoft.Network/virtualNetworks", "vnet-sqlsync")
    nsg = resource_id("Microsoft.Network/networkSecurityGroups", "nsg-sqlsync")
    pip = resource_id("Microsoft.Network/publicIPAddresses", "pip-sqlsync-egress")
    nat = resource_id("Microsoft.Network/natGateways", "nat-sqlsync")
    vault = resource_id("Microsoft.KeyVault/vaults", VAULT)
    parameters = {}
    resources = [
        {
            "type": "Microsoft.Network/networkSecurityGroups", "apiVersion": "2023-11-01",
            "name": "nsg-sqlsync", "location": REGION, "tags": tags,
            "properties": {"securityRules": [
                {"name": "SQL-from-Fabric-private-gateway", "properties": {
                    "priority": 100, "access": "Allow", "direction": "Inbound",
                    "protocol": "Tcp", "sourcePortRange": "*", "destinationPortRange": "1433",
                    "sourceAddressPrefix": "10.74.30.32/27", "destinationAddressPrefix": "*"}},
                bastion_rdp_rule(),
                {"name": "Deny-other-SQL", "properties": {
                    "priority": 120, "access": "Deny", "direction": "Inbound",
                    "protocol": "Tcp", "sourcePortRange": "*", "destinationPortRange": "1433",
                    "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}},
                {"name": "Deny-RDP", "properties": {
                    "priority": 130, "access": "Deny", "direction": "Inbound",
                    "protocol": "Tcp", "sourcePortRange": "*", "destinationPortRange": "3389",
                    "sourceAddressPrefix": "*", "destinationAddressPrefix": "*"}},
            ]},
        },
        {
            "type": "Microsoft.Network/publicIPAddresses", "apiVersion": "2023-11-01",
            "name": "pip-sqlsync-egress", "location": REGION, "tags": tags,
            "sku": {"name": "Standard"},
            "properties": {"publicIPAllocationMethod": "Static"},
        },
        {
            "type": "Microsoft.Network/natGateways", "apiVersion": "2023-11-01",
            "name": "nat-sqlsync", "location": REGION, "tags": tags,
            "dependsOn": [pip], "sku": {"name": "Standard"},
            "properties": {"idleTimeoutInMinutes": 10, "publicIpAddresses": [{"id": pip}]},
        },
        {
            "type": "Microsoft.Network/virtualNetworks", "apiVersion": "2023-11-01",
            "name": "vnet-sqlsync", "location": REGION, "tags": tags, "dependsOn": [nat],
            "properties": {
                "addressSpace": {"addressPrefixes": ["10.74.30.0/24"]},
                "subnets": [
                    {"name": "sql", "properties": {
                        "addressPrefix": "10.74.30.0/27", "natGateway": {"id": nat}}},
                    {"name": "fabric-gateway", "properties": {
                        "addressPrefix": "10.74.30.32/27",
                        "delegations": [{"name": "powerplatform", "properties": {
                            "serviceName": "Microsoft.PowerPlatform/vnetaccesslinks"}}]}},
                    {"name": "AzureBastionSubnet", "properties": {
                        "addressPrefix": BASTION_PREFIX}},
                ],
            },
        },
        {
            "type": "Microsoft.Fabric/capacities", "apiVersion": "2023-11-01",
            "name": CAPACITY, "location": REGION, "tags": tags,
            "sku": {"name": "F8", "tier": "Fabric"},
            "properties": {"administration": {"members": [UPN]}},
        },
    ]
    resources.extend(bastion_resources(tags, vnet))
    policies = [{"tenantId": TENANT, "objectId": USER_ID,
                 "permissions": {"secrets": ["get", "list", "set"]}}]
    vm_ids = []
    for role, server in SERVERS.items():
        vm = resource_id("Microsoft.Compute/virtualMachines", server["vm"])
        vm_ids.append(vm)
        nic_name = "nic-sqlsync-" + role
        nic = resource_id("Microsoft.Network/networkInterfaces", nic_name)
        for suffix in ("Admin", "Sql"):
            parameters[role + suffix] = {"type": "securestring"}
        resources.extend([
            {
                "type": "Microsoft.Network/networkInterfaces", "apiVersion": "2023-11-01",
                "name": nic_name, "location": REGION, "tags": tags, "dependsOn": [vnet, nsg],
                "properties": {"networkSecurityGroup": {"id": nsg}, "ipConfigurations": [{
                    "name": "ipconfig1", "properties": {
                        "privateIPAllocationMethod": "Static", "privateIPAddress": server["ip"],
                        "subnet": {"id": vnet + "/subnets/sql"}}}]},
            },
            {
                "type": "Microsoft.Compute/virtualMachines", "apiVersion": "2024-07-01",
                "name": server["vm"], "location": REGION, "tags": {**tags, "role": role},
                "dependsOn": [nic], "identity": {"type": "SystemAssigned"},
                "properties": {
                    "hardwareProfile": {"vmSize": "Standard_D2s_v5"},
                    "storageProfile": {
                        "imageReference": {"publisher": "MicrosoftSQLServer", "offer": "sql2022-ws2022",
                                           "sku": "standard-gen2", "version": "latest"},
                        "osDisk": {"name": "osdisk-sqlsync-" + role, "createOption": "FromImage",
                                   "managedDisk": {"storageAccountType": "Premium_LRS"}},
                    },
                    "osProfile": {
                        "computerName": server["computer"], "adminUsername": "sqlsyncadmin",
                        "adminPassword": "[parameters('" + role + "Admin')]",
                        "windowsConfiguration": {"provisionVMAgent": True, "enableAutomaticUpdates": True,
                                                 "patchSettings": {"patchMode": "AutomaticByOS"}},
                    },
                    "networkProfile": {"networkInterfaces": [{"id": nic}]},
                    "securityProfile": {"securityType": "TrustedLaunch",
                                        "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True}},
                },
            },
            {
                "type": "Microsoft.SqlVirtualMachine/sqlVirtualMachines", "apiVersion": "2023-10-01",
                "name": server["vm"], "location": REGION, "tags": tags, "dependsOn": [vm],
                "properties": {"virtualMachineResourceId": vm, "sqlServerLicenseType": "PAYG",
                               "sqlManagement": "Full", "sqlImageOffer": "SQL2022-WS2022",
                               "sqlImageSku": "Standard"},
            },
        ])
    resources.append({
        "type": "Microsoft.KeyVault/vaults", "apiVersion": "2023-07-01",
        "name": VAULT, "location": REGION, "tags": tags, "dependsOn": vm_ids,
        "properties": {
            "tenantId": TENANT, "sku": {"family": "A", "name": "standard"},
            "enableRbacAuthorization": False, "enableSoftDelete": True,
            "softDeleteRetentionInDays": 7, "accessPolicies": policies,
        },
    })
    for role in SERVERS:
        for suffix, name in (("Admin", role + "-vm-admin-password"),
                             ("Sql", role + "-sql-pipeline-password")):
            resources.append({
                "type": "Microsoft.KeyVault/vaults/secrets", "apiVersion": "2023-07-01",
                "name": VAULT + "/" + name, "dependsOn": [vault],
                "properties": {"value": "[parameters('" + role + suffix + "')]"},
            })
    return {"$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0", "parameters": parameters, "resources": resources}


def provision():
    if STATE.exists():
        raise RuntimeError("Deployment state exists; inspect status instead of replacing credentials.")
    check_identity()
    call("PUT", ARM + "?api-version=2022-09-01", {
        "location": REGION, "tags": {"purpose": "FabricPipeline-SQLServer-CDC-demo", "owner": UPN}})
    provider = f"https://management.azure.com/subscriptions/{SUB}/providers/Microsoft.PowerPlatform"
    call("POST", provider + "/register?api-version=2021-04-01")
    for _ in range(60):
        if call("GET", provider + "?api-version=2021-04-01").json()["registrationState"] == "Registered":
            break
        time.sleep(5)
    else:
        raise TimeoutError("PowerPlatform provider registration did not finish.")
    infrastructure = template()
    (ROOT / "infra.json").write_text(json.dumps(infrastructure, indent=2), encoding="utf-8")
    parameters = {name: {"value": secrets.token_urlsafe(32) + "!aA1"}
                  for name in infrastructure["parameters"]}
    url = ARM + "/providers/Microsoft.Resources/deployments/sqlsync-demo?api-version=2022-09-01"
    call("PUT", url, {"properties": {
        "mode": "Incremental", "template": infrastructure, "parameters": parameters}})
    save_state(subscriptionId=SUB, tenantId=TENANT, account=UPN, resourceGroup=RG,
               region=REGION, keyVault=VAULT, fabricCapacity=CAPACITY, fabricSku="F8",
               workspaceName=WS_NAME, servers=SERVERS, deploymentUrl=url,
               licensing="SQL Server 2022 Standard PAYG; Azure Hybrid Benefit not assumed")
    print("Deployment submitted: two private SQL Server Standard VMs, Bastion Basic and dedicated Fabric F8.")


def bastion():
    check_identity()
    state = read_state()
    if state.get("subscriptionId") != SUB or state.get("resourceGroup") != RG:
        raise RuntimeError("Existing deployment state is required for the additive Bastion upgrade.")
    vnet = resource_id("Microsoft.Network/virtualNetworks", "vnet-sqlsync")
    subnet = vnet + "/subnets/AzureBastionSubnet"
    properties = call("GET", "https://management.azure.com" + vnet + "?api-version=2023-11-01").json()["properties"]
    network = ipaddress.ip_network(BASTION_PREFIX)
    if not any(network.subnet_of(ipaddress.ip_network(prefix))
               for prefix in properties["addressSpace"]["addressPrefixes"]):
        raise RuntimeError("The Bastion subnet is outside the existing VNet address space.")
    for existing in properties.get("subnets", []):
        prefixes = existing["properties"].get("addressPrefixes", [])
        if existing["properties"].get("addressPrefix"):
            prefixes = [existing["properties"]["addressPrefix"]]
        if existing["name"] == "AzureBastionSubnet":
            if prefixes != [BASTION_PREFIX]:
                raise RuntimeError("Existing AzureBastionSubnet has a different address range; refusing to replace it.")
            if existing["properties"].get("networkSecurityGroup"):
                raise RuntimeError("Existing AzureBastionSubnet has an NSG; review its required Bastion rules before upgrading.")
        elif any(network.overlaps(ipaddress.ip_network(prefix)) for prefix in prefixes):
            raise RuntimeError("The requested Bastion subnet overlaps an existing subnet.")
    tags = {"purpose": "FabricPipeline-SQLServer-CDC-demo", "owner": UPN,
            "environment": "demonstration"}
    rule = bastion_rdp_rule()
    resources = [
        {"type": "Microsoft.Network/virtualNetworks/subnets", "apiVersion": "2023-11-01",
         "name": "vnet-sqlsync/AzureBastionSubnet", "properties": {"addressPrefix": BASTION_PREFIX}},
        {"type": "Microsoft.Network/networkSecurityGroups/securityRules", "apiVersion": "2023-11-01",
         "name": "nsg-sqlsync/" + rule["name"], "properties": rule["properties"]},
        *bastion_resources(tags, subnet),
    ]
    infrastructure = {"$schema": template()["$schema"], "contentVersion": "1.0.0.0",
                      "resources": resources}
    url = ARM + "/providers/Microsoft.Resources/deployments/sqlsync-bastion?api-version=2022-09-01"
    call("PUT", url, {"properties": {"mode": "Incremental", "template": infrastructure}})
    save_state(bastionDeploymentUrl=url)
    print("Additive Bastion deployment submitted; existing VMs, credentials and Fabric gateway are unchanged.", flush=True)
    deadline = time.time() + 2400
    while time.time() < deadline:
        result = call("GET", url).json()["properties"]
        current = result["provisioningState"]
        if current == "Succeeded":
            host = resource_id("Microsoft.Network/bastionHosts", BASTION_NAME)
            save_state(bastion={
                "name": BASTION_NAME, "sku": "Basic", "resourceId": host,
                "subnet": "AzureBastionSubnet", "subnetPrefix": BASTION_PREFIX,
                "publicIpName": BASTION_PUBLIC_IP, "provisioningState": current,
                "portalUrl": f"https://portal.azure.com/#@{TENANT}/resource{host}/overview",
            }, networking="Private Fabric gateway and Bastion-only RDP; VM public IPs absent; SQL remains private")
            (ROOT / "infra.json").write_text(json.dumps(template(), indent=2), encoding="utf-8")
            print("Bastion Basic is ready. Configure each VM desktop with the ssms action.", flush=True)
            return
        if current in ("Failed", "Canceled"):
            raise RuntimeError("Bastion deployment failed: " + json.dumps(result.get("error")))
        time.sleep(20)
    raise TimeoutError("Bastion deployment did not finish within 40 minutes; inspect sqlsync-bastion in Azure.")


def status(wait=False):
    deadline = time.time() + (2400 if wait else 1)
    while True:
        properties = call("GET", read_state()["deploymentUrl"]).json()["properties"]
        current = properties["provisioningState"]
        print(json.dumps({"state": current, "error": properties.get("error")}), flush=True)
        if current == "Succeeded":
            return
        if current in ("Failed", "Canceled"):
            raise RuntimeError("Azure deployment failed.")
        if not wait:
            return
        if time.time() >= deadline:
            raise TimeoutError("Azure infrastructure did not finish within 40 minutes.")
        time.sleep(30)


def workspace():
    state = read_state()
    capacities = collection(FABRIC + "/capacities")
    matches = [item for item in capacities if item["displayName"] == CAPACITY]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one dedicated Fabric capacity.")
    capacity = matches[0]
    workspaces = [item for item in collection(FABRIC + "/workspaces")
                  if item["displayName"] == WS_NAME]
    if len(workspaces) > 1:
        raise RuntimeError("Workspace name is ambiguous.")
    if workspaces:
        result = workspaces[0]
        if result.get("capacityId") != capacity["id"]:
            if collection(FABRIC + "/workspaces/" + result["id"] + "/items"):
                raise RuntimeError("Existing nonempty workspace is on a different capacity; refusing reassignment.")
            endpoint = FABRIC + "/workspaces/" + result["id"]
            call("POST", endpoint + "/assignToCapacity", {"capacityId": capacity["id"]})
            for _ in range(120):
                result = call("GET", endpoint).json()
                if (result.get("capacityId") == capacity["id"]
                        and result.get("capacityAssignmentProgress") == "Completed"):
                    break
                if result.get("capacityAssignmentProgress") == "Failed":
                    raise RuntimeError("Workspace capacity assignment failed.")
                time.sleep(5)
            else:
                raise TimeoutError("Workspace capacity assignment did not complete.")
    else:
        result = call("POST", FABRIC + "/workspaces", {
            "displayName": WS_NAME, "capacityId": capacity["id"],
            "description": "Two-server SQL Server Standard CDC synchronization using Fabric Pipeline. Synthetic data; keyless tables and retry-safe micro-batches.",
        }).json()
    save_state(capacityId=capacity["id"], workspaceId=result["id"],
               workspaceUrl=f"https://app.fabric.microsoft.com/groups/{result['id']}/list")
    print(json.dumps({"workspaceId": result["id"], "capacityId": capacity["id"]}))


def gateway():
    state = read_state()
    if state.get("gatewayId"):
        print("Gateway already recorded: " + state["gatewayId"])
        return
    existing = [item for item in collection(FABRIC + "/gateways")
                if item.get("displayName") == GATEWAY_NAME]
    if len(existing) > 1:
        raise RuntimeError("Gateway display name is ambiguous.")
    result = existing[0] if existing else call("POST", FABRIC + "/gateways", {
        "type": "VirtualNetwork", "displayName": GATEWAY_NAME,
        "capacityId": state["capacityId"], "inactivityMinutesBeforeSleep": 30,
        "numberOfMemberGateways": 1,
        "virtualNetworkAzureResource": {"subscriptionId": SUB, "resourceGroupName": RG,
            "virtualNetworkName": "vnet-sqlsync", "subnetName": "fabric-gateway"},
    }).json()
    if result["virtualNetworkAzureResource"]["resourceGroupName"] != RG:
        raise RuntimeError("Existing gateway belongs to a different resource group.")
    save_state(gatewayId=result["id"],
               networking="Private Fabric gateway and Bastion-only RDP; VM public IPs absent; SQL remains private")
    print(json.dumps(result, indent=2))


def run_command(role, script, name="sqlsync", as_admin=True, pipeline_password=None,
                timeout_seconds=1800):
    server = SERVERS[role]
    name = name[:40] + "-" + uuid.uuid4().hex[:8]
    script = "try {\n" + script + "\n} catch { Write-Output ('RUN_COMMAND_ERROR: ' + $_.Exception.ToString()); throw }\n"
    if pipeline_password is not None:
        script = "param([string]$PipelinePassword)\n" + script
    url = ARM + f"/providers/Microsoft.Compute/virtualMachines/{server['vm']}/runCommands/{name}?api-version=2024-07-01"
    properties = {"source": {"script": script}, "timeoutInSeconds": timeout_seconds,
                  "asyncExecution": False, "treatFailureAsDeploymentFailure": True}
    if pipeline_password is not None:
        properties["protectedParameters"] = [{"name": "PipelinePassword", "value": pipeline_password}]
    if as_admin:
        properties.update(runAsUser=server["computer"] + "\\sqlsyncadmin",
                          runAsPassword=secret(role + "-vm-admin-password"))
    call("PUT", url, {"location": REGION, "properties": properties})
    deadline = time.time() + timeout_seconds + 300
    while time.time() < deadline:
        result = call("GET", url + "&$expand=instanceView").json()["properties"]
        view = result.get("instanceView", {})
        current = view.get("executionState")
        if current in ("Succeeded", "Failed", "TimedOut", "Canceled"):
            output = {"serverRole": role, "state": current, "exitCode": view.get("exitCode"),
                      "output": view.get("output"), "error": view.get("error")}
            print(json.dumps(output, indent=2), flush=True)
            if current != "Succeeded" or view.get("exitCode", 0) != 0:
                raise RuntimeError(f"VM RunCommand failed on {role}.")
            return output
        time.sleep(10)
    raise TimeoutError(f"VM RunCommand did not complete on {role}.")


def sql_text(role, content, name="query"):
    encoded = base64.b64encode(content.encode("utf-16le")).decode()
    script = (ROOT / "invoke-sql.ps1").read_text(encoding="utf-8")
    return run_command(role, script.replace("__SQL_BASE64__", encoded), "sql-" + name)


def configure(role):
    script = (ROOT / "configure-sql.ps1").read_text(encoding="utf-8")
    return run_command(role, script, "configure-sql",
                       pipeline_password=secret(role + "-sql-pipeline-password"))


def ssms(role):
    check_identity()
    script = (ROOT / "configure-desktop.ps1").read_text(encoding="utf-8")
    query = (ROOT / ("inspect-" + role + ".sql")).read_text(encoding="utf-8")
    script = script.replace("__BASTION_PREFIX__", BASTION_PREFIX).replace(
        "__INSPECT_BASE64__", base64.b64encode(query.encode("utf-8")).decode())
    result = run_command(role, script, "configure-desktop", timeout_seconds=3600)
    evidence = ROOT / ("desktop-" + role + ".json")
    evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
    desktops = read_state().get("desktops", {})
    desktops[role] = {"configured": True, "evidenceFile": evidence.name,
                      "authentication": "Windows", "sqlServer": "localhost",
                      "inspectionFile": r"C:\SQLSyncDemo\inspect.sql"}
    save_state(desktops=desktops)


def harden_vault():
    endpoint = ARM + f"/providers/Microsoft.KeyVault/vaults/{VAULT}?api-version=2023-07-01"
    policy = {"tenantId": TENANT, "objectId": USER_ID,
              "permissions": {"secrets": ["get", "list", "set"]}}
    call("PATCH", endpoint, {"properties": {"accessPolicies": [policy]}})
    policies = call("GET", endpoint).json()["properties"]["accessPolicies"]
    if len(policies) != 1 or policies[0]["objectId"] != USER_ID:
        raise RuntimeError("Key Vault still grants unexpected secret access.")
    (ROOT / "infra.json").write_text(json.dumps(template(), indent=2), encoding="utf-8")
    save_state(credentialIsolation="VM identities have no Key Vault secret access; per-server credentials use protected Run Command parameters")
    print("Vault access restricted to the deploying administrator; VM identities cannot retrieve each other's credentials.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["provision", "status", "workspace", "gateway",
                                         "configure", "sql", "transport", "harden-vault",
                                         "bastion", "ssms"])
    parser.add_argument("--role", choices=list(SERVERS), default="source")
    parser.add_argument("--file")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if args.action == "provision":
        provision()
    elif args.action == "status":
        status(args.wait)
    elif args.action == "workspace":
        workspace()
    elif args.action == "gateway":
        gateway()
    elif args.action == "configure":
        configure(args.role)
    elif args.action == "bastion":
        bastion()
    elif args.action == "ssms":
        ssms(args.role)
    elif args.action == "harden-vault":
        harden_vault()
    elif args.action == "sql":
        if not args.file:
            parser.error("--file is required for sql")
        sql_text(args.role, Path(args.file).read_text(encoding="utf-8-sig"), Path(args.file).stem)
    else:
        script = (ROOT / "verify-transport.ps1").read_text(encoding="utf-8")
        script = script.replace("__DATABASE__", SERVERS[args.role]["database"])
        result = run_command(args.role, script, "verify-transport",
                             pipeline_password=secret(args.role + "-sql-pipeline-password"))
        (ROOT / ("transport-" + args.role + ".json")).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
