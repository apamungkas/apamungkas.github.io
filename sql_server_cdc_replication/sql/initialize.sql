-- SOURCE ONLY. Installs the bootstrap procedure; installation does not seed or execute it.
-- Operator must pause ALL external writers; the internal application lock cannot stop them.
USE SqlSyncSource;
GO
CREATE OR ALTER PROCEDURE dbo.InitializeSource
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    IF @@TRANCOUNT<>0 THROW 51200, 'Initialization requires no ambient transaction.', 1;
    DECLARE @Lock int,@Boundary varbinary(10),@Batch uniqueidentifier,@Schema nvarchar(max),@Rows nvarchar(max),
            @Count int,@Bytes bigint,@SeedTime datetime2(3)='2026-01-01T00:00:00.000';
    BEGIN TRY
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=60000;
        IF @Lock<0 THROW 51201, 'Could not serialize source initialization/procedures.', 1;
        EXEC dbo.AssertCdcConfiguration;
        IF EXISTS(SELECT 1 FROM dbo.CdcBatches WHERE FromLsn=0x00000000000000000000)
        BEGIN
            -- An immutable baseline already exists, pending or acknowledged; never recreate it.
            EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
            RETURN;
        END;
        IF EXISTS(SELECT 1 FROM dbo.CdcBatches) OR EXISTS(SELECT 1 FROM dbo.CdcOutbox)
           OR EXISTS(SELECT 1 FROM dbo.CdcBatchAcknowledgements)
           OR EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE IsBootstrapped=1 OR LastAcknowledgedLsn IS NOT NULL)
            THROW 51204, 'Unrecognized existing replication history. Refusing reset.', 1;
        BEGIN TRANSACTION;
        IF EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1 AND SeededAt IS NULL)
        BEGIN
            IF EXISTS(SELECT 1 FROM dbo.Customers) OR EXISTS(SELECT 1 FROM dbo.Products)
               OR EXISTS(SELECT 1 FROM dbo.LegacyBalances) OR EXISTS(SELECT 1 FROM dbo.EventLog)
                THROW 51205, 'Unrecognized preexisting business data. Refusing automatic seed/reset.', 1;
            INSERT dbo.Customers(CustomerId,CustomerName,Email,CreditLimit,UpdatedAt)
                VALUES(1,N'Ada',N'ada@example.invalid',1000.00,@SeedTime),(2,N'Lin  ',NULL,NULL,@SeedTime);
            INSERT dbo.Products(SKU,ProductName,Price,Notes,UpdatedAt)
                VALUES(N'SEED-001',N'Widget',12.50,NULL,@SeedTime),(N'SEED-002',N'Gadget',20.00,N'Trailing spaces  ',@SeedTime);
            INSERT dbo.LegacyBalances(AccountLabel,Balance,Note,AsOf)
                VALUES(N'duplicate',12.34,NULL,@SeedTime),(N'duplicate',12.34,NULL,@SeedTime),
                      (NULL,NULL,N'nullable ',NULL),(N'Case',1.00,N'x',@SeedTime),(N'case',1.00,N'x ',@SeedTime);
            INSERT dbo.EventLog(EventCode,Description,OccurredAt) VALUES(0,NULL,@SeedTime);
            UPDATE dbo.ReplicationState SET SeededAt=SYSUTCDATETIME(),UpdatedAt=SYSUTCDATETIME() WHERE SingletonId=1;
        END;
        COMMIT;
        -- The internal session lock spans seed COMMIT, capture, and snapshot; external writers stay paused.
        -- Reattempt after a timeout uses a new marker but never seeds twice.
        EXEC dbo.WaitForCdcCapture @CapturedLsn=@Boundary OUTPUT;
        BEGIN TRANSACTION;
        EXEC dbo.ReadReplicaEnvelope @SchemaJson=@Schema OUTPUT,@RowsJson=@Rows OUTPUT;
        SELECT @Count=COUNT(*),@Bytes=COALESCE(SUM(CONVERT(bigint,DATALENGTH(RowJson))),0) FROM dbo.BusinessRowImages;
        IF @Count=0 OR @Count>2000 OR @Bytes>2097152 THROW 51207, 'Invalid/oversized initial baseline.', 1;
        SET @Batch=NEWID();
        INSERT dbo.CdcBatches(BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes)
            VALUES(@Batch,0x00000000000000000000,@Boundary,@Count,@Bytes);
        INSERT dbo.CdcOutbox(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        SELECT @Batch,CONVERT(int,ROW_NUMBER() OVER(ORDER BY TableName COLLATE Latin1_General_100_BIN2,CONVERT(varbinary(max),RowJson))),
            TableName,@Boundary,0x00000000000000000000,2,RowJson FROM dbo.BusinessRowImages;
        UPDATE dbo.ReplicationState SET UpdatedAt=SYSUTCDATETIME() WHERE SingletonId=1;
        COMMIT;
        EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        IF @Lock>=0 EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
        THROW;
    END CATCH;
END;
GO
-- Explicit bootstrap command, with the pipeline/writers paused: EXEC dbo.InitializeSource;
-- Then use the NORMAL Prepare -> Copy -> Apply -> Ack pipeline. No remote INSERT exists.
