import argparse

from deploy import ROOT, SERVERS, save_state, sql_text


def permissions(role):
    database = SERVERS[role]["database"]
    content = f"""
USE [{database}];
GO
IF USER_ID(N'fabric_cdc') IS NULL CREATE USER [fabric_cdc] FOR LOGIN [fabric_cdc];
GO
GRANT EXECUTE ON OBJECT::dbo.GetReplicationStatus TO [fabric_cdc];
GRANT VIEW DEFINITION ON OBJECT::dbo.Customers TO [fabric_cdc];
GRANT VIEW DEFINITION ON OBJECT::dbo.Products TO [fabric_cdc];
GRANT VIEW DEFINITION ON OBJECT::dbo.LegacyBalances TO [fabric_cdc];
GRANT VIEW DEFINITION ON OBJECT::dbo.EventLog TO [fabric_cdc];
"""
    if role == "source":
        content += """
GRANT SELECT, VIEW DEFINITION ON OBJECT::dbo.CdcOutbox TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.PrepareCdcBatch TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.AcknowledgeCdcBatch TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.GetReplicaSnapshot TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.GenerateDemoChanges TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.WaitForCdcCapture TO [fabric_cdc];
EXEC dbo.AssertCdcConfiguration;
"""
    else:
        content += """
GRANT SELECT, INSERT, UPDATE, VIEW DEFINITION ON OBJECT::dbo.CdcStage TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.ApplyCdcBatch TO [fabric_cdc];
GRANT EXECUTE ON OBJECT::dbo.ValidateReplica TO [fabric_cdc];
EXEC dbo.AssertBusinessSchema;
"""
    return content


def install(role):
    files = ("source.sql", "schema.sql", "initialize.sql", "demo-changes.sql") if role == "source" else (
        "target.sql", "schema.sql", "validate.sql")
    parts = [(ROOT / "sql" / name).read_text(encoding="utf-8-sig") for name in files]
    parts.append(permissions(role))
    result = sql_text(role, "\nGO\n".join(parts), "install-cdc")
    print(f"Installed CDC demonstration SQL on {role}.", flush=True)
    return result


def bootstrap():
    result = sql_text("source", "USE SqlSyncSource;\nEXEC dbo.InitializeSource;\nEXEC dbo.GetReplicationStatus;",
                      "prepare-baseline")
    save_state(sourceDatabase=SERVERS["source"]["database"],
               targetDatabase=SERVERS["target"]["database"],
               cdcMode="Native SQL all-changes with update-old images; duplicate-safe full-row apply",
               baseline="Durable source outbox copied and applied by the same cross-server Fabric pipeline")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["source", "target", "bootstrap"])
    args = parser.parse_args()
    bootstrap() if args.action == "bootstrap" else install(args.action)
