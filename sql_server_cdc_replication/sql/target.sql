-- SQL Server 2022 Standard, DESTINATION VM only. No source connection is used here.
USE master;
GO
IF DB_ID(N'SqlSyncDestination') IS NULL EXEC(N'CREATE DATABASE [SqlSyncDestination];');
GO
ALTER DATABASE SqlSyncDestination SET TRUSTWORTHY OFF;
ALTER DATABASE SqlSyncDestination SET DB_CHAINING OFF;
GO
USE SqlSyncDestination;
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
IF OBJECT_ID(N'dbo.ReplicationState',N'U') IS NULL
CREATE TABLE dbo.ReplicationState
(
    SingletonId int NOT NULL CONSTRAINT PK_ReplicationState PRIMARY KEY CHECK(SingletonId=1),
    IsBootstrapped bit NOT NULL DEFAULT(0),LastAppliedLsn varbinary(10) NULL,
    LastBatchId uniqueidentifier NULL,BootstrappedAt datetime2(3) NULL,
    UpdatedAt datetime2(3) NOT NULL DEFAULT(SYSUTCDATETIME())
);
IF NOT EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1)
    INSERT dbo.ReplicationState(SingletonId) VALUES(1);
IF OBJECT_ID(N'dbo.CdcStage',N'U') IS NULL
CREATE TABLE dbo.CdcStage
(
    BatchId uniqueidentifier NOT NULL,EventNo int NOT NULL,TableName nvarchar(128) NOT NULL,
    StartLsn varbinary(10) NOT NULL,SeqVal varbinary(10) NOT NULL,Operation int NOT NULL,
    RowJson nvarchar(max) NOT NULL,
    CONSTRAINT PK_CdcStage PRIMARY KEY(BatchId,EventNo),
    CHECK(EventNo BETWEEN 1 AND 2000 AND Operation BETWEEN 1 AND 4),
    CHECK(ISJSON(RowJson)=1),CHECK(DATALENGTH(StartLsn)=10 AND DATALENGTH(SeqVal)=10)
);
IF OBJECT_ID(N'dbo.AppliedBatches',N'U') IS NULL
CREATE TABLE dbo.AppliedBatches
(
    BatchId uniqueidentifier NOT NULL CONSTRAINT PK_AppliedBatches PRIMARY KEY,
    FromLsn varbinary(10) NOT NULL,ToLsn varbinary(10) NOT NULL,EventCount int NOT NULL,
    RowJsonBytes bigint NOT NULL,InsertCount int NOT NULL,DeleteCount int NOT NULL,
    AppliedAt datetime2(3) NOT NULL,ReplayCount int NOT NULL DEFAULT(0),LastReplayedAt datetime2(3) NULL
);
IF OBJECT_ID(N'dbo.AppliedEvents',N'U') IS NULL
CREATE TABLE dbo.AppliedEvents
(
    BatchId uniqueidentifier NOT NULL,EventNo int NOT NULL,TableName nvarchar(128) NOT NULL,
    StartLsn varbinary(10) NOT NULL,SeqVal varbinary(10) NOT NULL,Operation int NOT NULL,
    RowJson nvarchar(max) NOT NULL,CONSTRAINT PK_AppliedEvents PRIMARY KEY(BatchId,EventNo)
);
GO
CREATE OR ALTER TRIGGER dbo.tr_AppliedEvents_Immutable ON dbo.AppliedEvents AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    THROW 51122, 'Committed event audit images are immutable.', 1;
END;
GO
CREATE OR ALTER TRIGGER dbo.tr_AppliedBatches_ManifestImmutable ON dbo.AppliedBatches AFTER UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS
    (
        SELECT BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes,InsertCount,DeleteCount,AppliedAt FROM deleted
        EXCEPT SELECT BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes,InsertCount,DeleteCount,AppliedAt FROM inserted
    ) OR EXISTS
    (
        SELECT BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes,InsertCount,DeleteCount,AppliedAt FROM inserted
        EXCEPT SELECT BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes,InsertCount,DeleteCount,AppliedAt FROM deleted
    )
        THROW 51123, 'Committed manifests are immutable; only replay audit counters may change.', 1;
END;
GO
CREATE OR ALTER PROCEDURE dbo.ApplyCdcBatch
    @BatchId uniqueidentifier,@FromLsn varchar(22),@ToLsn varchar(22),@EventCount int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
    IF @@TRANCOUNT<>0 THROW 51100, 'Apply requires no ambient transaction.', 1;
    DECLARE @From varbinary(10)=TRY_CONVERT(varbinary(10),@FromLsn,1),
            @To varbinary(10)=TRY_CONVERT(varbinary(10),@ToLsn,1);
    IF @BatchId IS NULL OR @FromLsn IS NULL OR @ToLsn IS NULL
       OR DATALENGTH(@FromLsn)<>22 OR DATALENGTH(@ToLsn)<>22
       OR @From IS NULL OR @To IS NULL OR DATALENGTH(@From)<>10 OR DATALENGTH(@To)<>10
       OR @EventCount IS NULL OR @EventCount NOT BETWEEN 0 AND 2000
        THROW 51101, 'Invalid manifest: UUID, 0x-prefixed 10-byte LSNs, event count 0..2000 required.', 1;
    DECLARE @Lock int,@Checkpoint binary(10),@Bootstrapped bit,@Count int,@Bytes bigint,
            @MinEvent int,@MaxEvent int,@Deletes int=0,@Inserts int=0,
            @Table nvarchar(128),@Operation int,@Json nvarchar(max),@Canonical nvarchar(max),@Affected int;
    DECLARE @CustomerId int,@CustomerName nvarchar(100),@Email nvarchar(200),
            @CreditLimit decimal(18,2),@UpdatedAt datetime2(3),
            @SKU nvarchar(40),@ProductName nvarchar(100),@Price decimal(18,2),@Notes nvarchar(200),
            @AccountLabel nvarchar(80),@Balance decimal(18,2),@Note nvarchar(100),@AsOf datetime2(3),
            @EventCode int,@Description nvarchar(200),@OccurredAt datetime2(3);
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Destination',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=60000;
        IF @Lock<0 THROW 51102, 'Could not serialize destination apply.', 1;
        EXEC dbo.AssertBusinessSchema;
        SELECT @Checkpoint=LastAppliedLsn,@Bootstrapped=IsBootstrapped
        FROM dbo.ReplicationState WITH(UPDLOCK,HOLDLOCK) WHERE SingletonId=1;
        IF @Bootstrapped IS NULL THROW 51103, 'Destination control singleton is missing.', 1;
        -- Includes the whole BatchId key range, blocking concurrent Copy during this apply.
        SELECT @Count=COUNT(*),@MinEvent=MIN(EventNo),@MaxEvent=MAX(EventNo),
               @Bytes=COALESCE(SUM(CONVERT(bigint,DATALENGTH(RowJson))),0)
        FROM dbo.CdcStage WITH(UPDLOCK,HOLDLOCK) WHERE BatchId=@BatchId;
        IF @Count<>@EventCount OR (@Count>0 AND (@MinEvent<>1 OR @MaxEvent<>@EventCount))
            THROW 51104, 'Staging missing/extra events, or EventNo is not contiguous from 1.', 1;
        IF @Bytes>2097152 THROW 51105, 'Staged row JSON exceeds 2 MiB; nothing truncated.', 1;
        IF EXISTS
        (
            SELECT 1 FROM dbo.CdcStage WHERE BatchId=@BatchId AND
            (
                StartLsn<@From OR StartLsn>@To
                OR CONVERT(varbinary(256),TableName) NOT IN
                    (CONVERT(varbinary(256),N'Customers'),CONVERT(varbinary(256),N'Products'),
                     CONVERT(varbinary(256),N'LegacyBalances'),CONVERT(varbinary(256),N'EventLog'))
                OR (TableName=N'EventLog' AND Operation<>2) OR Operation NOT BETWEEN 1 AND 4
                OR ISJSON(RowJson,OBJECT)<>1
            )
        )
            THROW 51106, 'Staged event has invalid LSN, table, operation, or JSON object.', 1;
        IF EXISTS
        (
            SELECT 1 FROM
            (
                SELECT EventNo,ROW_NUMBER() OVER(ORDER BY StartLsn,SeqVal,Operation,
                    TableName COLLATE Latin1_General_100_BIN2,CONVERT(varbinary(max),RowJson),EventNo) AS ExpectedNo
                FROM dbo.CdcStage WHERE BatchId=@BatchId
            ) e WHERE EventNo<>ExpectedNo
        )
            THROW 51107, 'EventNo does not follow deterministic LSN/sequence/operation/table/image order.', 1;
        IF EXISTS
        (
            SELECT 1 FROM dbo.CdcStage WHERE BatchId=@BatchId AND Operation IN (3,4)
            GROUP BY TableName,StartLsn,SeqVal
            HAVING SUM(CASE WHEN Operation=3 THEN 1 ELSE 0 END)<>SUM(CASE WHEN Operation=4 THEN 1 ELSE 0 END)
        )
            THROW 51108, 'An update before/after pair is incomplete.', 1;
        IF EXISTS(SELECT 1 FROM dbo.AppliedBatches WHERE BatchId=@BatchId)
        BEGIN
            IF NOT EXISTS(SELECT 1 FROM dbo.AppliedBatches WHERE BatchId=@BatchId AND FromLsn=@From AND ToLsn=@To
                AND EventCount=@EventCount AND RowJsonBytes=@Bytes)
                THROW 51109, 'Committed BatchId replayed with a different manifest.', 1;
            -- Byte-for-byte audit equality detects same-length payload mutation on replay.
            IF EXISTS
            (
                SELECT EventNo,CONVERT(varbinary(256),TableName),StartLsn,SeqVal,Operation,CONVERT(varbinary(max),RowJson)
                FROM dbo.CdcStage WHERE BatchId=@BatchId
                EXCEPT
                SELECT EventNo,CONVERT(varbinary(256),TableName),StartLsn,SeqVal,Operation,CONVERT(varbinary(max),RowJson)
                FROM dbo.AppliedEvents WHERE BatchId=@BatchId
            ) OR EXISTS
            (
                SELECT EventNo,CONVERT(varbinary(256),TableName),StartLsn,SeqVal,Operation,CONVERT(varbinary(max),RowJson)
                FROM dbo.AppliedEvents WHERE BatchId=@BatchId
                EXCEPT
                SELECT EventNo,CONVERT(varbinary(256),TableName),StartLsn,SeqVal,Operation,CONVERT(varbinary(max),RowJson)
                FROM dbo.CdcStage WHERE BatchId=@BatchId
            )
                THROW 51121, 'Staging differs from immutable committed event images; replay rejected.', 1;
            UPDATE dbo.AppliedBatches SET ReplayCount=ReplayCount+1,LastReplayedAt=SYSUTCDATETIME() WHERE BatchId=@BatchId;
            COMMIT;
            SELECT CONVERT(varchar(36),@BatchId) AS BatchId,CAST(1 AS bit) AS WasReplay,@EventCount AS EventCount;
            RETURN;
        END;
        IF @Bootstrapped=0
        BEGIN
            IF @From<>0x00000000000000000000 OR @To<=@From OR @Count=0 OR @Checkpoint IS NOT NULL
               OR EXISTS(SELECT 1 FROM dbo.AppliedBatches)
                THROW 51110, 'Fresh destination accepts only the explicit zero-to-marker baseline range.', 1;
            IF EXISTS(SELECT 1 FROM dbo.CdcStage WHERE BatchId=@BatchId
                AND (Operation<>2 OR StartLsn<>@To OR SeqVal<>0x00000000000000000000))
                THROW 51110, 'Baseline must contain only inserts at the captured marker, with zero SeqVal.', 1;
            IF EXISTS(SELECT 1 FROM dbo.Customers WITH(TABLOCKX,HOLDLOCK))
               OR EXISTS(SELECT 1 FROM dbo.Products WITH(TABLOCKX,HOLDLOCK))
               OR EXISTS(SELECT 1 FROM dbo.LegacyBalances WITH(TABLOCKX,HOLDLOCK))
               OR EXISTS(SELECT 1 FROM dbo.EventLog WITH(TABLOCKX,HOLDLOCK))
                THROW 51120, 'Fresh destination contains business data. Refusing to overwrite/reset.', 1;
        END
        ELSE IF @Checkpoint IS NULL OR @From<>sys.fn_cdc_increment_lsn(@Checkpoint) OR @To<@Checkpoint
             OR (@From>@To AND (@To<>@Checkpoint OR @EventCount<>0))
            THROW 51110, 'Batch range does not continue the checkpoint (or invalid empty range).', 1;
        DECLARE events CURSOR LOCAL FAST_FORWARD FOR
            SELECT TableName,Operation,RowJson FROM dbo.CdcStage WHERE BatchId=@BatchId ORDER BY EventNo;
        OPEN events;
        FETCH NEXT FROM events INTO @Table,@Operation,@Json;
        WHILE @@FETCH_STATUS=0
        BEGIN
            SET @Canonical=NULL;
            SET @Affected=0;
            IF @Table COLLATE Latin1_General_100_BIN2=N'Customers'
            BEGIN
                SELECT @CustomerId=CustomerId,@CustomerName=CustomerName,@Email=Email,
                       @CreditLimit=CreditLimit,@UpdatedAt=UpdatedAt
                FROM OPENJSON(@Json) WITH(CustomerId int,CustomerName nvarchar(100),Email nvarchar(200),CreditLimit decimal(18,2),UpdatedAt datetime2(3));
                IF @CustomerId IS NULL OR @CustomerName IS NULL OR @UpdatedAt IS NULL
                    THROW 51111, 'Customers JSON contains missing/null required column.', 1;
                SET @Canonical=(SELECT @CustomerId AS CustomerId,@CustomerName AS CustomerName,@Email AS Email,
                    @CreditLimit AS CreditLimit,@UpdatedAt AS UpdatedAt FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
                IF CONVERT(varbinary(max),@Canonical)<>CONVERT(varbinary(max),@Json)
                    THROW 51112, 'Customers JSON is not the exact bounded, strongly typed source row image.', 1;
                IF @Operation IN (1,3)
                BEGIN
                    DELETE TOP(1) t FROM dbo.Customers t WHERE
                        CONVERT(varbinary(max),(SELECT t.CustomerId,t.CustomerName,t.Email,t.CreditLimit,t.UpdatedAt
                            FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER))=CONVERT(varbinary(max),@Canonical);
                    SET @Affected=@@ROWCOUNT;
                END
                ELSE INSERT dbo.Customers(CustomerId,CustomerName,Email,CreditLimit,UpdatedAt)
                    VALUES(@CustomerId,@CustomerName,@Email,@CreditLimit,@UpdatedAt);
            END
            ELSE IF @Table COLLATE Latin1_General_100_BIN2=N'Products'
            BEGIN
                SELECT @SKU=SKU,@ProductName=ProductName,@Price=Price,@Notes=Notes,@UpdatedAt=UpdatedAt
                FROM OPENJSON(@Json) WITH(SKU nvarchar(40),ProductName nvarchar(100),Price decimal(18,2),Notes nvarchar(200),UpdatedAt datetime2(3));
                IF @SKU IS NULL OR @ProductName IS NULL OR @Price IS NULL OR @UpdatedAt IS NULL
                    THROW 51113, 'Products JSON contains missing/null required column.', 1;
                SET @Canonical=(SELECT @SKU AS SKU,@ProductName AS ProductName,@Price AS Price,@Notes AS Notes,@UpdatedAt AS UpdatedAt
                    FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
                IF CONVERT(varbinary(max),@Canonical)<>CONVERT(varbinary(max),@Json)
                    THROW 51114, 'Products JSON is not the exact bounded, strongly typed source row image.', 1;
                IF @Operation IN (1,3)
                BEGIN
                    DELETE TOP(1) t FROM dbo.Products t WHERE
                        CONVERT(varbinary(max),(SELECT t.SKU,t.ProductName,t.Price,t.Notes,t.UpdatedAt
                            FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER))=CONVERT(varbinary(max),@Canonical);
                    SET @Affected=@@ROWCOUNT;
                END
                ELSE INSERT dbo.Products(SKU,ProductName,Price,Notes,UpdatedAt) VALUES(@SKU,@ProductName,@Price,@Notes,@UpdatedAt);
            END
            ELSE IF @Table COLLATE Latin1_General_100_BIN2=N'LegacyBalances'
            BEGIN
                SELECT @AccountLabel=AccountLabel,@Balance=Balance,@Note=Note,@AsOf=AsOf
                FROM OPENJSON(@Json) WITH(AccountLabel nvarchar(80),Balance decimal(18,2),Note nvarchar(100),AsOf datetime2(3));
                SET @Canonical=(SELECT @AccountLabel AS AccountLabel,@Balance AS Balance,@Note AS Note,@AsOf AS AsOf
                    FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
                IF CONVERT(varbinary(max),@Canonical)<>CONVERT(varbinary(max),@Json)
                    THROW 51115, 'LegacyBalances JSON is not the exact bounded, strongly typed source row image.', 1;
                IF @Operation IN (1,3)
                BEGIN
                    DELETE TOP(1) t FROM dbo.LegacyBalances t WHERE
                        CONVERT(varbinary(max),(SELECT t.AccountLabel,t.Balance,t.Note,t.AsOf
                            FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER))=CONVERT(varbinary(max),@Canonical);
                    SET @Affected=@@ROWCOUNT;
                END
                ELSE INSERT dbo.LegacyBalances(AccountLabel,Balance,Note,AsOf) VALUES(@AccountLabel,@Balance,@Note,@AsOf);
            END
            ELSE IF @Table COLLATE Latin1_General_100_BIN2=N'EventLog'
            BEGIN
                SELECT @EventCode=EventCode,@Description=Description,@OccurredAt=OccurredAt
                FROM OPENJSON(@Json) WITH(EventCode int,Description nvarchar(200),OccurredAt datetime2(3));
                IF @EventCode IS NULL OR @OccurredAt IS NULL THROW 51116, 'EventLog JSON missing required column.', 1;
                SET @Canonical=(SELECT @EventCode AS EventCode,@Description AS Description,@OccurredAt AS OccurredAt
                    FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
                IF CONVERT(varbinary(max),@Canonical)<>CONVERT(varbinary(max),@Json)
                    THROW 51117, 'EventLog JSON is not the exact bounded, strongly typed source row image.', 1;
                INSERT dbo.EventLog(EventCode,Description,OccurredAt) VALUES(@EventCode,@Description,@OccurredAt);
            END
            ELSE THROW 51118, 'Table is not on the apply allowlist.', 1;
            IF @Operation IN (1,3)
            BEGIN
                IF @Affected<>1 THROW 51119, 'Expected full before/delete row missing on destination. Entire batch rolled back.', 1;
                SET @Deletes+=1;
            END
            ELSE SET @Inserts+=1;
            FETCH NEXT FROM events INTO @Table,@Operation,@Json;
        END;
        CLOSE events;
        DEALLOCATE events;
        INSERT dbo.AppliedEvents(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
            SELECT BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson FROM dbo.CdcStage WHERE BatchId=@BatchId;
        INSERT dbo.AppliedBatches(BatchId,FromLsn,ToLsn,EventCount,RowJsonBytes,InsertCount,DeleteCount,AppliedAt)
            VALUES(@BatchId,@From,@To,@EventCount,@Bytes,@Inserts,@Deletes,SYSUTCDATETIME());
        UPDATE dbo.ReplicationState SET IsBootstrapped=1,LastAppliedLsn=@To,LastBatchId=@BatchId,
            BootstrappedAt=COALESCE(BootstrappedAt,SYSUTCDATETIME()),UpdatedAt=SYSUTCDATETIME() WHERE SingletonId=1;
        COMMIT;
        SELECT CONVERT(varchar(36),@BatchId) AS BatchId,CAST(0 AS bit) AS WasReplay,@EventCount AS EventCount;
    END TRY
    BEGIN CATCH
        IF CURSOR_STATUS('local','events')>=0 CLOSE events;
        IF CURSOR_STATUS('local','events')>=-1 DEALLOCATE events;
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER PROCEDURE dbo.GetReplicationStatus
AS
BEGIN
    SET NOCOUNT ON;
    SELECT DB_NAME() AS DatabaseName,s.IsBootstrapped,CONVERT(varchar(22),s.LastAppliedLsn,1) AS CheckpointLsn,
        s.LastBatchId,s.UpdatedAt,(SELECT COUNT(*) FROM dbo.AppliedBatches) AS AppliedBatchCount,
        (SELECT COALESCE(SUM(CONVERT(bigint,ReplayCount)),0) FROM dbo.AppliedBatches) AS ReplayCount,
        (SELECT COUNT_BIG(*) FROM dbo.CdcStage) AS StagedEvents FROM dbo.ReplicationState s WHERE SingletonId=1;
END;
GO
