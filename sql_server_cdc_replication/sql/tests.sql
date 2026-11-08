-- DESTINATION ONLY, after baseline, with pipeline and source writers stopped.
-- All synthetic Apply calls fail; successful replay changes only replay audit counters.
USE SqlSyncDestination;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @Checkpoint binary(10),@From varchar(22),@High varchar(22),@Batch uniqueidentifier=NEWID(),
        @Json nvarchar(max),@Missing nvarchar(max),@At datetime2(3)='2026-03-01T00:00:00.000',
        @Caught int,@Passed int=0,@SchemaBefore nvarchar(max),@RowsBefore nvarchar(max),
        @SchemaAfter nvarchar(max),@RowsAfter nvarchar(max),
        @Replay uniqueidentifier,@ReplayFrom varchar(22),@ReplayTo varchar(22),@ReplayEvents int,
        @ReplayEvent int,@ReplayImage nvarchar(max),@ReplayCount int,@ReplayTested bit=0,
        @Snapshot nvarchar(max),@BadSnapshot nvarchar(max),@LastBatch uniqueidentifier;
SELECT @Checkpoint=LastAppliedLsn,@LastBatch=LastBatchId FROM dbo.ReplicationState WHERE SingletonId=1 AND IsBootstrapped=1;
IF @Checkpoint IS NULL THROW 51500, 'Bootstrap before tests.', 1;
IF EXISTS(SELECT 1 FROM dbo.Customers WHERE CustomerId IN (-2147483648,-2147483647))
    THROW 51501, 'Reserved test CustomerIds are occupied; no test writes made.', 1;
SET @From=CONVERT(varchar(22),sys.fn_cdc_increment_lsn(@Checkpoint),1);
SET @High=CONVERT(varchar(22),@Checkpoint,1);
EXEC dbo.ReadReplicaEnvelope @SchemaJson=@SchemaBefore OUTPUT,@RowsJson=@RowsBefore OUTPUT;
BEGIN TRY
    DECLARE @Cases TABLE(Id int,FromHex varchar(22),ToHex varchar(22),Events int,Expected int);
    INSERT @Cases VALUES(1,@From,@From,-1,51101),(2,'not-an-lsn',@From,0,51101),
        (3,@High,@High,0,51110),(4,@From,@From,1,51104),(5,@From,@From,2001,51101);
    DECLARE @Id int=1,@CaseFrom varchar(22),@CaseTo varchar(22),@CaseCount int,@Expected int;
    WHILE @Id<=5
    BEGIN
        SELECT @CaseFrom=FromHex,@CaseTo=ToHex,@CaseCount=Events,@Expected=Expected FROM @Cases WHERE Id=@Id;
        SET @Caught=0;
        BEGIN TRY
            EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@CaseFrom,@ToLsn=@CaseTo,@EventCount=@CaseCount;
        END TRY
        BEGIN CATCH
            IF ERROR_NUMBER()<>@Expected THROW;
            SET @Caught=1;
        END CATCH;
        IF @Caught<>1 THROW 51502, 'Manifest/missing-stage negative test did not fail as expected.', 1;
        SET @Passed+=1;
        SET @Id+=1;
    END;
    SET @Json=(SELECT CONVERT(int,-2147483648) AS CustomerId,CONVERT(nvarchar(100),N'test  ') AS CustomerName,
        CONVERT(nvarchar(200),NULL) AS Email,CONVERT(decimal(18,2),NULL) AS CreditLimit,@At AS UpdatedAt
        FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
    SET @Missing=(SELECT CONVERT(int,-2147483647) AS CustomerId,CONVERT(nvarchar(100),N'missing') AS CustomerName,
        CONVERT(nvarchar(200),NULL) AS Email,CONVERT(decimal(18,2),NULL) AS CreditLimit,@At AS UpdatedAt
        FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
    INSERT dbo.CdcStage(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        VALUES(@Batch,1,N'Customers',CONVERT(varbinary(10),@From,1),0x00000000000000000001,2,@Json),
              (@Batch,2,N'Customers',CONVERT(varbinary(10),@From,1),0x00000000000000000002,1,@Missing);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51104 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51503, 'Extra staging row not rejected.', 1;
    SET @Passed+=1;
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=2;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51119 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51504, 'Missing before row not rejected.', 1;
    IF EXISTS(SELECT 1 FROM dbo.Customers WHERE CustomerId=-2147483648)
       OR EXISTS(SELECT 1 FROM dbo.AppliedBatches WHERE BatchId=@Batch)
       OR EXISTS(SELECT 1 FROM dbo.AppliedEvents WHERE BatchId=@Batch)
       OR EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE LastAppliedLsn<>@Checkpoint)
        THROW 51505, 'Atomic rollback left business row, audit, or checkpoint changes.', 1;
    SET @Passed+=1;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    SET @Json=(SELECT CONVERT(int,-2147483648) AS CustomerId,REPLICATE(N'x',101) AS CustomerName,
        CONVERT(nvarchar(200),NULL) AS Email,CONVERT(decimal(18,2),NULL) AS CreditLimit,@At AS UpdatedAt
        FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER);
    INSERT dbo.CdcStage(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        VALUES(@Batch,1,N'Customers',CONVERT(varbinary(10),@From,1),0x00000000000000000001,2,@Json);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51112 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51506, 'Overlong bounded string not rejected.', 1;
    SET @Passed+=1;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    INSERT dbo.CdcStage(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        VALUES(@Batch,1,N'UnlistedTable',CONVERT(varbinary(10),@From,1),0x00000000000000000001,2,@Missing);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51106 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51507, 'Unknown table not rejected.', 1;
    SET @Passed+=1;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    INSERT dbo.CdcStage(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        VALUES(@Batch,1,N'Customers',CONVERT(varbinary(10),@From,1),0x00000000000000000001,3,@Missing);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51108 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51508, 'Incomplete update not rejected.', 1;
    SET @Passed+=1;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    SET @Json=N'{"x":"'+REPLICATE(CONVERT(nvarchar(max),N'x'),1048576)+N'"}';
    INSERT dbo.CdcStage(BatchId,EventNo,TableName,StartLsn,SeqVal,Operation,RowJson)
        VALUES(@Batch,1,N'Customers',CONVERT(varbinary(10),@From,1),0x00000000000000000001,2,@Json);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Batch,@FromLsn=@From,@ToLsn=@From,@EventCount=1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51105 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51509, 'Oversized payload not rejected.', 1;
    SET @Passed+=1;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    -- DDL assertion is rolled back; Apply itself deliberately refuses ambient transactions.
    BEGIN TRANSACTION;
    ALTER TABLE dbo.Products ALTER COLUMN Notes nvarchar(201) COLLATE Latin1_General_100_CI_AS NULL;
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.AssertBusinessSchema;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51601 THROW;
        SET @Caught=1;
    END CATCH;
    IF XACT_STATE()<>0 ROLLBACK;
    IF @Caught<>1 THROW 51510, 'Schema drift not rejected.', 1;
    SET @Passed+=1;
    SELECT TOP(1) @Replay=BatchId,@ReplayFrom=CONVERT(varchar(22),FromLsn,1),
        @ReplayTo=CONVERT(varchar(22),ToLsn,1),@ReplayEvents=EventCount,@ReplayCount=ReplayCount
    FROM dbo.AppliedBatches WHERE EventCount>0 ORDER BY AppliedAt DESC,BatchId;
    IF @Replay IS NULL THROW 51511, 'Expected committed nonempty baseline for replay tests.', 1;
    EXEC dbo.ApplyCdcBatch @BatchId=@Replay,@FromLsn=@ReplayFrom,@ToLsn=@ReplayTo,@EventCount=@ReplayEvents;
    IF NOT EXISTS(SELECT 1 FROM dbo.AppliedBatches WHERE BatchId=@Replay AND ReplayCount=@ReplayCount+1 AND LastReplayedAt IS NOT NULL)
        THROW 51512, 'Replay was not audited.', 1;
    SELECT TOP(1) @ReplayEvent=EventNo,@ReplayImage=RowJson FROM dbo.CdcStage WHERE BatchId=@Replay ORDER BY EventNo DESC;
    UPDATE dbo.CdcStage SET RowJson=STUFF(RowJson,3,1,N'Z') WHERE BatchId=@Replay AND EventNo=@ReplayEvent;
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ApplyCdcBatch @BatchId=@Replay,@FromLsn=@ReplayFrom,@ToLsn=@ReplayTo,@EventCount=@ReplayEvents;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51121 THROW;
        SET @Caught=1;
    END CATCH;
    UPDATE dbo.CdcStage SET RowJson=@ReplayImage WHERE BatchId=@Replay AND EventNo=@ReplayEvent;
    IF @Caught<>1 THROW 51513, 'Same-length replay mutation not rejected by exact audit comparison.', 1;
    SET @Passed+=1;
    SET @ReplayTested=1;
    SET @Snapshot=(SELECT 1 AS ContractVersion,@High AS CheckpointLsn,CONVERT(varchar(36),@LastBatch) AS LastBatchId,
        JSON_QUERY(@SchemaBefore) AS TableSchema,JSON_QUERY(@RowsBefore) AS RowMultisets FOR JSON PATH,WITHOUT_ARRAY_WRAPPER);
    EXEC dbo.ValidateReplica @SourceSnapshot=@Snapshot;
    SET @BadSnapshot=JSON_MODIFY(@Snapshot,'$.RowMultisets[0].Multiplicity',999);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ValidateReplica @SourceSnapshot=@BadSnapshot;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51417 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51514, 'Duplicate multiplicity mismatch not detected.', 1;
    SET @Passed+=1;
    SET @BadSnapshot=JSON_MODIFY(@Snapshot,'$.CheckpointLsn',@From);
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ValidateReplica @SourceSnapshot=@BadSnapshot;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51414 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51515, 'Checkpoint mismatch not detected.', 1;
    SET @Passed+=1;
    SET @BadSnapshot=JSON_MODIFY(@Snapshot,'$.TableSchema[0].IsPrimaryKey',CONVERT(bit,0));
    SET @Caught=0;
    BEGIN TRY
        EXEC dbo.ValidateReplica @SourceSnapshot=@BadSnapshot;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER()<>51415 THROW;
        SET @Caught=1;
    END CATCH;
    IF @Caught<>1 THROW 51516, 'Schema/key metadata mismatch not detected.', 1;
    SET @Passed+=1;
    EXEC dbo.ReadReplicaEnvelope @SchemaJson=@SchemaAfter OUTPUT,@RowsJson=@RowsAfter OUTPUT;
    IF CONVERT(varbinary(max),@SchemaBefore)<>CONVERT(varbinary(max),@SchemaAfter)
       OR CONVERT(varbinary(max),@RowsBefore)<>CONVERT(varbinary(max),@RowsAfter)
       OR EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE LastAppliedLsn<>@Checkpoint OR LastBatchId<>@LastBatch)
        THROW 51517, 'Tests changed business multisets/schema/checkpoint.', 1;
    SELECT N'Passed' AS Tests,@Passed AS ExpectedFailuresVerified,@ReplayTested AS ExactReplayVerified;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK;
    IF @Replay IS NOT NULL AND @ReplayImage IS NOT NULL
        UPDATE dbo.CdcStage SET RowJson=@ReplayImage WHERE BatchId=@Replay AND EventNo=@ReplayEvent;
    DELETE dbo.CdcStage WHERE BatchId=@Batch;
    THROW;
END CATCH;
GO
