-- DESTINATION ONLY. The parent transports source SnapshotJson as one nvarchar(max) parameter.
USE SqlSyncDestination;
GO
CREATE OR ALTER PROCEDURE dbo.ValidateReplica @SourceSnapshot nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    IF @@TRANCOUNT<>0 THROW 51410, 'Validation requires no ambient transaction.', 1;
    IF @SourceSnapshot IS NULL OR DATALENGTH(@SourceSnapshot)>8388608 OR ISJSON(@SourceSnapshot,OBJECT)<>1
        THROW 51411, 'Expected a bounded source snapshot JSON object (max 8 MiB).', 1;
    DECLARE @Version int,@SourceLsn varchar(100),@SourceBatch varchar(100),@SourceSchema nvarchar(max),@SourceRows nvarchar(max),
            @Checkpoint varbinary(10),@LastBatch uniqueidentifier,@LocalSchema nvarchar(max),@LocalRows nvarchar(max),@Lock int,
            @Customers bigint,@Products bigint,@Balances bigint,@Events bigint;
    SELECT @Version=ContractVersion,@SourceLsn=CheckpointLsn,@SourceBatch=LastBatchId,@SourceSchema=TableSchema,@SourceRows=RowMultisets
    FROM OPENJSON(@SourceSnapshot) WITH
        (ContractVersion int,CheckpointLsn varchar(100),LastBatchId varchar(100),TableSchema nvarchar(max) AS JSON,RowMultisets nvarchar(max) AS JSON);
    SET @Checkpoint=TRY_CONVERT(varbinary(10),@SourceLsn,1);
    SET @LastBatch=TRY_CONVERT(uniqueidentifier,@SourceBatch);
    IF @Version IS NULL OR @Version<>1 OR @SourceLsn IS NULL OR DATALENGTH(@SourceLsn)<>22
       OR @Checkpoint IS NULL OR DATALENGTH(@Checkpoint)<>10 OR @LastBatch IS NULL OR DATALENGTH(@SourceBatch)<>36
       OR @SourceSchema IS NULL OR @SourceRows IS NULL OR ISJSON(@SourceSchema,ARRAY)<>1 OR ISJSON(@SourceRows,ARRAY)<>1
        THROW 51412, 'Snapshot manifest/schema/rows are malformed or unsupported.', 1;
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Destination',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=60000;
        IF @Lock<0 THROW 51413, 'Could not serialize destination validation.', 1;
        IF NOT EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1 AND IsBootstrapped=1
            AND LastAppliedLsn=@Checkpoint AND LastBatchId=@LastBatch)
            THROW 51414, 'Source/destination checkpoint or last acknowledged/applied batch differs.', 1;
        EXEC dbo.ReadReplicaEnvelope @SchemaJson=@LocalSchema OUTPUT,@RowsJson=@LocalRows OUTPUT;
        IF CONVERT(varbinary(max),@LocalSchema)<>CONVERT(varbinary(max),@SourceSchema)
            THROW 51415, 'Source/destination table schemas or key metadata differ.', 1;
        CREATE TABLE #SourceRows(TableBytes varbinary(256) NOT NULL,RowBytes varbinary(max) NOT NULL,Multiplicity bigint NOT NULL);
        CREATE TABLE #TargetRows(TableBytes varbinary(256) NOT NULL,RowBytes varbinary(max) NOT NULL,Multiplicity bigint NOT NULL);
        IF EXISTS
        (
            SELECT 1 FROM OPENJSON(@SourceRows) WITH(TableName nvarchar(4000),RowHex varchar(max),Multiplicity bigint)
            WHERE TableName IS NULL OR RowHex IS NULL OR Multiplicity IS NULL OR Multiplicity<=0
               OR TRY_CONVERT(varbinary(max),RowHex,1) IS NULL
               OR CONVERT(varbinary(max),TableName) NOT IN
                    (CONVERT(varbinary(max),N'Customers'),CONVERT(varbinary(max),N'Products'),
                     CONVERT(varbinary(max),N'LegacyBalances'),CONVERT(varbinary(max),N'EventLog'))
        )
            THROW 51416, 'Snapshot contains malformed full-row multiset entries.', 1;
        INSERT #SourceRows SELECT CONVERT(varbinary(256),TableName),CONVERT(varbinary(max),RowHex,1),Multiplicity
            FROM OPENJSON(@SourceRows) WITH(TableName nvarchar(128),RowHex varchar(max),Multiplicity bigint);
        INSERT #TargetRows SELECT CONVERT(varbinary(256),TableName),CONVERT(varbinary(max),RowHex,1),Multiplicity
            FROM OPENJSON(@LocalRows) WITH(TableName nvarchar(128),RowHex varchar(max),Multiplicity bigint);
        IF EXISTS(SELECT 1 FROM #SourceRows GROUP BY TableBytes,RowBytes HAVING COUNT(*)<>1)
           OR (SELECT COALESCE(SUM(Multiplicity),0) FROM #SourceRows)>2000
            THROW 51416, 'Snapshot groups are not unique or exceed the bounded row limit.', 1;
        IF EXISTS(SELECT * FROM #SourceRows EXCEPT SELECT * FROM #TargetRows)
           OR EXISTS(SELECT * FROM #TargetRows EXCEPT SELECT * FROM #SourceRows)
            THROW 51417, 'Full business-row multisets differ (including NULL/case/spaces/duplicate multiplicity).', 1;
        SELECT @Customers=(SELECT COUNT_BIG(*) FROM dbo.Customers),@Products=(SELECT COUNT_BIG(*) FROM dbo.Products),
               @Balances=(SELECT COUNT_BIG(*) FROM dbo.LegacyBalances),@Events=(SELECT COUNT_BIG(*) FROM dbo.EventLog);
        COMMIT;
        SELECT CAST(1 AS bit) AS IsValid,N'All four full-row multisets, schemas, keys and checkpoints match.' AS Validation,
            CONVERT(varchar(22),@Checkpoint,1) AS CheckpointLsn,SYSUTCDATETIME() AS ValidatedAtUtc,
            @Customers AS Customers,@Products AS Products,@Balances AS LegacyBalances,@Events AS EventLog;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO
