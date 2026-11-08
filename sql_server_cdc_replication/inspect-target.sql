USE [SqlSyncDestination];
GO
SELECT @@SERVERNAME AS ServerName, DB_NAME() AS DatabaseName,
       ORIGINAL_LOGIN() AS LoginName, SERVERPROPERTY('Edition') AS Edition;
SELECT name AS DatabaseName, is_cdc_enabled AS CdcEnabled
FROM sys.databases WHERE name = DB_NAME();
SELECT t.name AS TableName, t.is_tracked_by_cdc AS CdcTracked,
       (SELECT SUM(p.rows) FROM sys.partitions p
        WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)) AS [RowCount],
       CASE WHEN EXISTS (SELECT 1 FROM sys.indexes i
                         WHERE i.object_id = t.object_id AND i.is_primary_key = 1)
            THEN 1 ELSE 0 END AS HasPrimaryKey,
       CASE WHEN EXISTS (SELECT 1 FROM sys.indexes i
                         WHERE i.object_id = t.object_id AND i.is_unique = 1)
            THEN 1 ELSE 0 END AS HasUniqueIndex
FROM sys.tables t
WHERE t.schema_id = SCHEMA_ID('dbo')
  AND t.name IN ('Customers', 'Products', 'LegacyBalances', 'EventLog')
ORDER BY t.name;
SELECT TOP (1000) * FROM dbo.Customers ORDER BY CustomerId;
SELECT TOP (1000) * FROM dbo.Products;
SELECT TOP (1000) * FROM dbo.LegacyBalances;
SELECT TOP (1000) * FROM dbo.EventLog;
EXEC dbo.GetReplicationStatus;
