-- SQL Server 2022 Standard, SOURCE VM only; run as sysadmin. SQL Server Agent must run.
USE master;
GO
IF DB_ID(N'SqlSyncSource') IS NULL EXEC(N'CREATE DATABASE [SqlSyncSource];');
GO
ALTER DATABASE SqlSyncSource SET TRUSTWORTHY OFF;
ALTER DATABASE SqlSyncSource SET DB_CHAINING OFF;
GO
USE SqlSyncSource;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF OBJECT_ID(N'dbo.Customers',N'U') IS NULL
CREATE TABLE dbo.Customers
(
    CustomerId int NOT NULL CONSTRAINT PK_Customers PRIMARY KEY,
    CustomerName nvarchar(100) COLLATE Latin1_General_100_CI_AS NOT NULL,
    Email nvarchar(200) COLLATE Latin1_General_100_CI_AS NULL,
    CreditLimit decimal(18,2) NULL,UpdatedAt datetime2(3) NOT NULL
);
IF OBJECT_ID(N'dbo.Products',N'U') IS NULL
CREATE TABLE dbo.Products
(
    SKU nvarchar(40) COLLATE Latin1_General_100_CI_AS NOT NULL,
    ProductName nvarchar(100) COLLATE Latin1_General_100_CI_AS NOT NULL,
    Price decimal(18,2) NOT NULL,Notes nvarchar(200) COLLATE Latin1_General_100_CI_AS NULL,
    UpdatedAt datetime2(3) NOT NULL
);
IF NOT EXISTS(SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.Products') AND name=N'UX_Products_SKU')
    CREATE UNIQUE INDEX UX_Products_SKU ON dbo.Products(SKU);
IF OBJECT_ID(N'dbo.LegacyBalances',N'U') IS NULL
CREATE TABLE dbo.LegacyBalances
(
    AccountLabel nvarchar(80) COLLATE Latin1_General_100_CI_AS NULL,Balance decimal(18,2) NULL,
    Note nvarchar(100) COLLATE Latin1_General_100_CI_AS NULL,AsOf datetime2(3) NULL
);
IF OBJECT_ID(N'dbo.EventLog',N'U') IS NULL
CREATE TABLE dbo.EventLog
(
    EventCode int NOT NULL,Description nvarchar(200) COLLATE Latin1_General_100_CI_AS NULL,
    OccurredAt datetime2(3) NOT NULL
);
IF OBJECT_ID(N'dbo.CdcCaptureMarkers',N'U') IS NULL
CREATE TABLE dbo.CdcCaptureMarkers
(
    MarkerId nvarchar(36) NOT NULL CONSTRAINT PK_CdcCaptureMarkers PRIMARY KEY,MarkedAt datetime2(3) NOT NULL
);
IF OBJECT_ID(N'dbo.ReplicationState',N'U') IS NULL
CREATE TABLE dbo.ReplicationState
(
    SingletonId int NOT NULL CONSTRAINT PK_ReplicationState PRIMARY KEY CHECK(SingletonId=1),
    IsBootstrapped bit NOT NULL DEFAULT(0),LastAcknowledgedLsn varbinary(10) NULL,
    LastBatchId uniqueidentifier NULL,SeededAt datetime2(3) NULL,
    BootstrapMarkerId nvarchar(36) NULL,BootstrappedAt datetime2(3) NULL,
    UpdatedAt datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME())
);
IF NOT EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1)
    INSERT dbo.ReplicationState(SingletonId) VALUES(1);
IF OBJECT_ID(N'dbo.CdcBatches',N'U') IS NULL
CREATE TABLE dbo.CdcBatches
(
    BatchId uniqueidentifier NOT NULL CONSTRAINT PK_CdcBatches PRIMARY KEY,
    FromLsn varbinary(10) NOT NULL,ToLsn varbinary(10) NOT NULL,
    EventCount int NOT NULL CHECK(EventCount BETWEEN 0 AND 2000),
    RowJsonBytes bigint NOT NULL CHECK(RowJsonBytes BETWEEN 0 AND 2097152),
    CreatedAt datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME()),
    CHECK(DATALENGTH(FromLsn)=10 AND DATALENGTH(ToLsn)=10)
);
IF OBJECT_ID(N'dbo.CdcOutbox',N'U') IS NULL
CREATE TABLE dbo.CdcOutbox
(
    BatchId uniqueidentifier NOT NULL,EventNo int NOT NULL,TableName nvarchar(128) NOT NULL,
    StartLsn varbinary(10) NOT NULL,SeqVal varbinary(10) NOT NULL,Operation int NOT NULL,
    RowJson nvarchar(max) NOT NULL,
    CONSTRAINT PK_CdcOutbox PRIMARY KEY(BatchId,EventNo),
    CHECK(EventNo BETWEEN 1 AND 2000 AND Operation BETWEEN 1 AND 4),
    CHECK(ISJSON(RowJson)=1),CHECK(DATALENGTH(StartLsn)=10 AND DATALENGTH(SeqVal)=10)
);
IF OBJECT_ID(N'dbo.CdcBatchAcknowledgements',N'U') IS NULL
CREATE TABLE dbo.CdcBatchAcknowledgements
(
    BatchId uniqueidentifier NOT NULL CONSTRAINT PK_CdcBatchAcknowledgements PRIMARY KEY,
    AcknowledgedAt datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME())
);
IF OBJECT_ID(N'dbo.DemoRuns',N'U') IS NULL
CREATE TABLE dbo.DemoRuns
(
    Round int NOT NULL CONSTRAINT PK_DemoRuns PRIMARY KEY,ExecutedAt datetime2(3) NOT NULL,
    BusinessStatements int NOT NULL
);
IF (SELECT is_cdc_enabled FROM sys.databases WHERE database_id=DB_ID())=0 EXEC sys.sp_cdc_enable_db;
DECLARE @Name sysname,@Columns nvarchar(400),@Instance sysname;
DECLARE capture_tables CURSOR LOCAL FAST_FORWARD FOR
SELECT TableName,ColumnList FROM (VALUES
    (N'Customers',N'CustomerId,CustomerName,Email,CreditLimit,UpdatedAt'),
    (N'Products',N'SKU,ProductName,Price,Notes,UpdatedAt'),
    (N'LegacyBalances',N'AccountLabel,Balance,Note,AsOf'),
    (N'EventLog',N'EventCode,Description,OccurredAt'),
    (N'CdcCaptureMarkers',N'MarkerId,MarkedAt')
) t(TableName,ColumnList);
OPEN capture_tables;
FETCH NEXT FROM capture_tables INTO @Name,@Columns;
WHILE @@FETCH_STATUS=0
BEGIN
    SET @Instance=N'dbo_'+@Name;
    IF NOT EXISTS(SELECT 1 FROM cdc.change_tables WHERE capture_instance=@Instance)
        EXEC sys.sp_cdc_enable_table @source_schema=N'dbo',@source_name=@Name,
            @capture_instance=@Instance,@role_name=NULL,@supports_net_changes=0,
            @captured_column_list=@Columns,@allow_partition_switch=0;
    FETCH NEXT FROM capture_tables INTO @Name,@Columns;
END;
CLOSE capture_tables;
DEALLOCATE capture_tables;
EXEC sys.sp_cdc_change_job @job_type=N'cleanup',@retention=10080;
-- Restarting the continuous capture job is an installation-time operator action if polling is changed.
GO
-- Remove only the obsolete lab guards; existing application writers need no lock protocol.
DROP TRIGGER IF EXISTS dbo.tr_Customers_WriterLock;
DROP TRIGGER IF EXISTS dbo.tr_Products_WriterLock;
DROP TRIGGER IF EXISTS dbo.tr_LegacyBalances_WriterLock;
GO
CREATE OR ALTER TRIGGER dbo.tr_EventLog_AppendOnly ON dbo.EventLog AFTER INSERT,UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS(SELECT 1 FROM deleted) THROW 51000, 'EventLog is append-only.', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.tr_CdcBatches_Immutable ON dbo.CdcBatches AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51001, 'CDC batch manifests are immutable.', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.tr_CdcOutbox_Immutable ON dbo.CdcOutbox AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51002, 'Durable outbox events are immutable.', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.tr_DemoRuns_Immutable ON dbo.DemoRuns AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51003, 'Demo run history is immutable.', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.tr_CdcAcknowledgements_Immutable ON dbo.CdcBatchAcknowledgements AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51031, 'Acknowledgement history is immutable.', 1;
END;
GO
CREATE OR ALTER PROCEDURE dbo.AssertCdcConfiguration
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.AssertBusinessSchema;
    IF EXISTS
    (
        SELECT 1 FROM (VALUES (N'Customers',5),(N'Products',5),(N'LegacyBalances',4),(N'EventLog',3),(N'CdcCaptureMarkers',2)) t(TableName,ExpectedColumns)
        LEFT JOIN cdc.change_tables ct ON ct.capture_instance=N'dbo_'+t.TableName
        WHERE ct.object_id IS NULL OR ct.source_object_id<>OBJECT_ID(N'dbo.'+t.TableName)
           OR ct.supports_net_changes<>0
           OR (SELECT COUNT(*) FROM cdc.captured_columns cc WHERE cc.object_id=ct.object_id)<>t.ExpectedColumns
           OR (SELECT COUNT(*) FROM sys.columns sc WHERE sc.object_id=ct.source_object_id)<>t.ExpectedColumns
    )
        THROW 51004, 'CDC capture configuration changed. Restore the contract; never skip LSNs.', 1;
    IF EXISTS
    (
        SELECT 1 FROM cdc.change_tables ct JOIN sys.columns sc ON sc.object_id=ct.source_object_id
        LEFT JOIN sys.columns cc ON cc.object_id=ct.object_id AND cc.name=sc.name
        WHERE ct.capture_instance IN
            (N'dbo_Customers',N'dbo_Products',N'dbo_LegacyBalances',N'dbo_EventLog',N'dbo_CdcCaptureMarkers')
          AND (cc.column_id IS NULL OR sc.system_type_id<>cc.system_type_id OR sc.max_length<>cc.max_length
               OR sc.precision<>cc.precision OR sc.scale<>cc.scale OR sc.is_computed=1 OR sc.is_identity=1
               OR ISNULL(sc.collation_name,N'')<>ISNULL(cc.collation_name,N''))
    )
        THROW 51005, 'Captured columns no longer match their source definitions.', 1;
END;
GO
CREATE OR ALTER PROCEDURE dbo.WaitForCdcCapture @CapturedLsn varbinary(10) OUTPUT
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT<>0 THROW 51032, 'Capture barrier requires no ambient transaction.', 1;
    DECLARE @Lock int,@Marker nvarchar(36)=CONVERT(nvarchar(36),NEWID()),
            @Low binary(10),@High binary(10),@Deadline datetime2(3)=DATEADD(second,180,SYSUTCDATETIME());
    SET @CapturedLsn=NULL;
    BEGIN TRY
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=60000;
        IF @Lock<0 THROW 51033, 'Could not serialize capture barrier.', 1;
        EXEC dbo.AssertCdcConfiguration;
        INSERT dbo.CdcCaptureMarkers(MarkerId,MarkedAt) VALUES(@Marker,SYSUTCDATETIME());
        WHILE @CapturedLsn IS NULL AND SYSUTCDATETIME()<@Deadline
        BEGIN
            SET @Low=sys.fn_cdc_get_min_lsn(N'dbo_CdcCaptureMarkers');
            SET @High=sys.fn_cdc_get_max_lsn();
            IF @Low<>0x00000000000000000000 AND @High>=@Low
                SELECT @CapturedLsn=__$start_lsn
                FROM cdc.fn_cdc_get_all_changes_dbo_CdcCaptureMarkers(@Low,@High,N'all update old')
                WHERE MarkerId=@Marker AND __$operation=2;
            IF @CapturedLsn IS NULL WAITFOR DELAY '00:00:01';
        END;
        IF @CapturedLsn IS NULL THROW 51206, 'CDC marker not captured within 180 seconds. Check Agent/capture job; safe to retry.', 1;
        EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
    END TRY
    BEGIN CATCH
        IF @Lock>=0 EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE dbo.PrepareCdcBatch
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    IF @@TRANCOUNT<>0 THROW 51006, 'Prepare requires no ambient transaction.', 1;
    DECLARE @Lock int,@BatchId uniqueidentifier,@Checkpoint binary(10),@From binary(10),@To binary(10),
            @Count int,@Bytes bigint;
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=60000;
        IF @Lock<0 THROW 51007, 'Could not serialize batch preparation.', 1;
        EXEC dbo.AssertCdcConfiguration;
        IF (SELECT COUNT(*) FROM dbo.CdcBatches b WHERE NOT EXISTS
            (SELECT 1 FROM dbo.CdcBatchAcknowledgements a WHERE a.BatchId=b.BatchId))>1
            THROW 51009, 'Multiple unacknowledged batches: source state is corrupt.', 1;
        SELECT @BatchId=b.BatchId FROM dbo.CdcBatches b WHERE NOT EXISTS
            (SELECT 1 FROM dbo.CdcBatchAcknowledgements a WHERE a.BatchId=b.BatchId);
        IF @BatchId IS NOT NULL
        BEGIN
            COMMIT;
            SELECT CONVERT(varchar(36),BatchId) AS BatchId,CONVERT(varchar(22),FromLsn,1) AS FromLsn,
                   CONVERT(varchar(22),ToLsn,1) AS ToLsn,EventCount FROM dbo.CdcBatches WHERE BatchId=@BatchId;
            RETURN;
        END;
        IF NOT EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1 AND IsBootstrapped=1)
            THROW 51008, 'Execute InitializeSource, then deliver/acknowledge its baseline through the pipeline.', 1;
        SELECT @Checkpoint=LastAcknowledgedLsn FROM dbo.ReplicationState WHERE SingletonId=1;
        IF @Checkpoint IS NULL THROW 51010, 'Source checkpoint is missing.', 1;
        SET @From=sys.fn_cdc_increment_lsn(@Checkpoint);
        SET @To=sys.fn_cdc_get_max_lsn();
        IF @To IS NULL OR @To=0x00000000000000000000 OR @To<@Checkpoint
            THROW 51011, 'CDC high watermark is unavailable or behind the checkpoint.', 1;
        IF EXISTS
        (
            SELECT 1 FROM (VALUES(N'dbo_Customers'),(N'dbo_Products'),(N'dbo_LegacyBalances'),(N'dbo_EventLog')) t(InstanceName)
            WHERE sys.fn_cdc_get_min_lsn(t.InstanceName)=0x00000000000000000000
               OR (@From<=@To AND @From<sys.fn_cdc_get_min_lsn(t.InstanceName))
        )
            THROW 51012, 'CDC retention gap. Stop and rebaseline explicitly; never skip to min_lsn.', 1;
        CREATE TABLE #Changes
        (
            TableName nvarchar(128) NOT NULL,StartLsn binary(10) NOT NULL,SeqVal binary(10) NOT NULL,
            Operation int NOT NULL,RowJson nvarchar(max) NOT NULL
        );
        IF @From<=@To
        BEGIN
            -- TOP(2001) detects overflow; it is never used to commit a truncated range.
            INSERT #Changes SELECT TOP(2001) N'Customers',c.__$start_lsn,c.__$seqval,c.__$operation,
                (SELECT c.CustomerId,c.CustomerName,c.Email,c.CreditLimit,c.UpdatedAt FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER)
            FROM cdc.fn_cdc_get_all_changes_dbo_Customers(@From,@To,N'all update old') c;
            INSERT #Changes SELECT TOP(2001) N'Products',c.__$start_lsn,c.__$seqval,c.__$operation,
                (SELECT c.SKU,c.ProductName,c.Price,c.Notes,c.UpdatedAt FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER)
            FROM cdc.fn_cdc_get_all_changes_dbo_Products(@From,@To,N'all update old') c;
            INSERT #Changes SELECT TOP(2001) N'LegacyBalances',c.__$start_lsn,c.__$seqval,c.__$operation,
                (SELECT c.AccountLabel,c.Balance,c.Note,c.AsOf FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER)
            FROM cdc.fn_cdc_get_all_changes_dbo_LegacyBalances(@From,@To,N'all update old') c;
            INSERT #Changes SELECT TOP(2001) N'EventLog',c.__$start_lsn,c.__$seqval,c.__$operation,
                (SELECT c.EventCode,c.Description,c.OccurredAt FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER)
            FROM cdc.fn_cdc_get_all_changes_dbo_EventLog(@From,@To,N'all update old') c;
        END;
        SELECT @Count=COUNT(*),@Bytes=COALESCE(SUM(CONVERT(bigint,DATALENGTH(RowJson))),0) FROM #Changes;
        IF @Count>2000 THROW 51013, 'CDC range exceeds 2000 events. No batch/checkpoint committed; poll sooner or rebaseline explicitly.', 1;
        IF @Bytes>2097152 THROW 51014, 'CDC range exceeds 2 MiB row JSON. Nothing truncated or committed.', 1;
        SET @BatchId=NEWID();
        INSERT dbo.CdcBatches(BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes) VALUES(@BatchId,@From,@To,@Count,@Bytes);
        INSERT dbo.CdcOutbox(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        SELECT @BatchId,CONVERT(int,ROW_NUMBER() OVER(ORDER BY StartLsn,SeqVal,Operation,
            TableName COLLATE Latin1_General_100_BIN2,CONVERT(varbinary(max),RowJson))),
            TableName,StartLsn,SeqVal,Operation,RowJson FROM #Changes;
        COMMIT;
        SELECT CONVERT(varchar(36),@BatchId) AS BatchId,CONVERT(varchar(22),@From,1) AS FromLsn,
               CONVERT(varchar(22),@To,1) AS ToLsn,@Count AS EventCount;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        IF ERROR_NUMBER()=313 THROW 51015, 'CDC enumeration range invalid (retention/capture); nothing committed.', 1;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE dbo.AcknowledgeCdcBatch @BatchId uniqueidentifier
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT<>0 THROW 51016, 'Acknowledge requires no ambient transaction.', 1;
    DECLARE @Lock int,@From binary(10),@To binary(10),@Checkpoint binary(10),@Bootstrapped bit;
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=60000;
        IF @Lock<0 THROW 51017, 'Could not serialize acknowledgement.', 1;
        SELECT @From=FromLsn,@To=ToLsn FROM dbo.CdcBatches WHERE BatchId=@BatchId;
        IF @From IS NULL THROW 51018, 'Cannot acknowledge an unknown batch.', 1;
        IF EXISTS(SELECT 1 FROM dbo.CdcBatchAcknowledgements WHERE BatchId=@BatchId)
        BEGIN
            COMMIT;
            RETURN;
        END;
        SELECT @Checkpoint=LastAcknowledgedLsn,@Bootstrapped=IsBootstrapped
        FROM dbo.ReplicationState WITH(UPDLOCK,HOLDLOCK) WHERE SingletonId=1;
        IF @Bootstrapped=0
        BEGIN
            IF @From<>0x00000000000000000000 OR @To<=@From OR @Checkpoint IS NOT NULL
               OR (SELECT COUNT(*) FROM dbo.CdcBatches)<>1
                THROW 51019, 'Invalid baseline acknowledgement.', 1;
        END
        ELSE IF @Checkpoint IS NULL OR @From<>sys.fn_cdc_increment_lsn(@Checkpoint) OR @To<@Checkpoint
            THROW 51019, 'Out-of-order acknowledgement or invalid checkpoint range.', 1;
        INSERT dbo.CdcBatchAcknowledgements(BatchId) VALUES(@BatchId);
        UPDATE dbo.ReplicationState SET IsBootstrapped=1,LastAcknowledgedLsn=@To,LastBatchId=@BatchId,
            BootstrappedAt=COALESCE(BootstrappedAt,SYSUTCDATETIME()),UpdatedAt=SYSUTCDATETIME() WHERE SingletonId=1;
        COMMIT;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE dbo.GetReplicaSnapshot
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    IF @@TRANCOUNT<>0 THROW 51400, 'Snapshot requires no ambient transaction.', 1;
    DECLARE @Lock int,@Barrier varbinary(10),@Checkpoint binary(10),@From binary(10),@High binary(10),
            @LastBatch uniqueidentifier,@Schema nvarchar(max),@Rows nvarchar(max),@Snapshot nvarchar(max);
    BEGIN TRY
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=60000;
        IF @Lock<0 THROW 51401, 'Could not serialize source snapshot.', 1;
        EXEC dbo.WaitForCdcCapture @CapturedLsn=@Barrier OUTPUT;
        BEGIN TRANSACTION;
        SELECT @Checkpoint=LastAcknowledgedLsn,@LastBatch=LastBatchId FROM dbo.ReplicationState
        WHERE SingletonId=1 AND IsBootstrapped=1;
        IF @Checkpoint IS NULL THROW 51402, 'Source baseline has not been acknowledged.', 1;
        IF EXISTS(SELECT 1 FROM dbo.CdcBatches b WHERE b.EventCount>0 AND NOT EXISTS
            (SELECT 1 FROM dbo.CdcBatchAcknowledgements a WHERE a.BatchId=b.BatchId))
            THROW 51403, 'Unacknowledged nonempty batch; finish Copy/Apply/Ack before snapshot.', 1;
        SET @From=sys.fn_cdc_increment_lsn(@Checkpoint);
        SET @High=sys.fn_cdc_get_max_lsn();
        IF @High<@Checkpoint OR @High<@Barrier THROW 51404, 'Capture watermark moved behind the checkpoint/barrier.', 1;
        IF EXISTS(SELECT 1 FROM (VALUES(N'dbo_Customers'),(N'dbo_Products'),(N'dbo_LegacyBalances'),(N'dbo_EventLog')) t(InstanceName)
            WHERE sys.fn_cdc_get_min_lsn(t.InstanceName)=0x00000000000000000000
               OR (@From<=@High AND @From<sys.fn_cdc_get_min_lsn(t.InstanceName)))
            THROW 51012, 'Retention gap prevents a safe source snapshot.', 1;
        IF @From<=@High
        BEGIN
            IF EXISTS(SELECT 1 FROM cdc.fn_cdc_get_all_changes_dbo_Customers(@From,@High,N'all update old'))
               OR EXISTS(SELECT 1 FROM cdc.fn_cdc_get_all_changes_dbo_Products(@From,@High,N'all update old'))
               OR EXISTS(SELECT 1 FROM cdc.fn_cdc_get_all_changes_dbo_LegacyBalances(@From,@High,N'all update old'))
               OR EXISTS(SELECT 1 FROM cdc.fn_cdc_get_all_changes_dbo_EventLog(@From,@High,N'all update old'))
                THROW 51405, 'Business CDC remains undrained. Keep writes paused and run pipeline, then retry snapshot.', 1;
        END;
        EXEC dbo.ReadReplicaEnvelope @SchemaJson=@Schema OUTPUT,@RowsJson=@Rows OUTPUT;
        SET @Snapshot=(SELECT 1 AS ContractVersion,CONVERT(varchar(22),@Checkpoint,1) AS CheckpointLsn,
            CONVERT(varchar(36),@LastBatch) AS LastBatchId,JSON_QUERY(@Schema) AS TableSchema,
            JSON_QUERY(@Rows) AS RowMultisets FOR JSON PATH,WITHOUT_ARRAY_WRAPPER);
        IF DATALENGTH(@Snapshot)>8388608 THROW 51604, 'Serialized snapshot exceeds the 8 MiB SQL defensive bound.', 1;
        COMMIT;
        EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
        SELECT @Snapshot AS SnapshotJson;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        IF @Lock>=0 EXEC sys.sp_releaseapplock @Resource=N'SqlSync.Source',@LockOwner=N'Session';
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE dbo.GetReplicationStatus
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    SELECT DB_NAME() AS DatabaseName,s.IsBootstrapped,CONVERT(varchar(22),s.LastAcknowledgedLsn,1) AS CheckpointLsn,
        CONVERT(varchar(22),sys.fn_cdc_get_max_lsn(),1) AS CapturedHighLsn,s.LastBatchId,s.SeededAt,s.UpdatedAt,
        (SELECT COUNT(*) FROM dbo.CdcBatches b WHERE NOT EXISTS
            (SELECT 1 FROM dbo.CdcBatchAcknowledgements a WHERE a.BatchId=b.BatchId)) AS PendingBatches,
        (SELECT COUNT_BIG(*) FROM dbo.CdcOutbox) AS OutboxEvents
    FROM dbo.ReplicationState s WHERE SingletonId=1;
END;
GO
