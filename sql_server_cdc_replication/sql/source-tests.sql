-- SOURCE ONLY, as administrator with external writers/pipeline paused. Business DML is rolled back.
USE SqlSyncSource;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @Caught int=0,@Passed int=0,@Unknown uniqueidentifier=NEWID(),@WriterVerified bit=0,
        @WriterSKU nvarchar(40),@WriterLabel nvarchar(80),@At datetime2(3)='2026-03-01T00:00:00.000',
        @SchemaBefore nvarchar(max),@RowsBefore nvarchar(max),@SchemaAfter nvarchar(max),@RowsAfter nvarchar(max);
IF @@TRANCOUNT<>0 THROW 51532, 'Source tests require no ambient transaction.', 1;
SET @WriterSKU=N'W-'+CONVERT(nvarchar(36),@Unknown);
SET @WriterLabel=N'ordinary-writer-'+CONVERT(nvarchar(36),@Unknown);
EXEC dbo.AssertCdcConfiguration;
EXEC dbo.ReadReplicaEnvelope @SchemaJson=@SchemaBefore OUTPUT,@RowsJson=@RowsBefore OUTPUT;
BEGIN TRY
    EXEC dbo.GenerateDemoChanges @Round=0;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER()<>51301 THROW;
    SET @Caught=1;
END CATCH;
IF @Caught<>1 THROW 51530, 'Invalid demo round not rejected.', 1;
SET @Passed+=1;
SET @Caught=0;
BEGIN TRY
    EXEC dbo.AcknowledgeCdcBatch @BatchId=@Unknown;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER()<>51018 THROW;
    SET @Caught=1;
END CATCH;
IF @Caught<>1 THROW 51531, 'Unknown acknowledgement not rejected.', 1;
SET @Passed+=1;
-- No application lock: ordinary INSERT/UPDATE/DELETE must work on PK and keyless tables.
BEGIN TRY
    BEGIN TRANSACTION;
    IF EXISTS(SELECT 1 FROM dbo.Customers WHERE CustomerId=-2147483648)
       OR EXISTS(SELECT 1 FROM dbo.Products WHERE SKU=@WriterSKU)
       OR EXISTS(SELECT 1 FROM dbo.LegacyBalances
           WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),@WriterLabel))
        THROW 51532, 'Reserved ordinary-writer test values are occupied; no test writes made.', 1;
    INSERT dbo.Customers(CustomerId,CustomerName,Email,CreditLimit,UpdatedAt)
        VALUES(-2147483648,N'Ordinary writer',NULL,NULL,@At);
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Customer insert failed.', 1;
    UPDATE dbo.Customers SET CustomerName=N'Ordinary writer updated  ',CreditLimit=1.23 WHERE CustomerId=-2147483648;
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Customer update failed.', 1;
    DELETE dbo.Customers WHERE CustomerId=-2147483648;
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Customer delete failed.', 1;
    INSERT dbo.Products(SKU,ProductName,Price,Notes,UpdatedAt) VALUES(@WriterSKU,N'Ordinary product',1.00,NULL,@At);
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Product insert failed.', 1;
    UPDATE dbo.Products SET Price=2.00,Notes=N'updated  ' WHERE SKU=@WriterSKU;
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Product update failed.', 1;
    DELETE dbo.Products WHERE SKU=@WriterSKU;
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary Product delete failed.', 1;
    INSERT dbo.LegacyBalances(AccountLabel,Balance,Note,AsOf)
        VALUES(@WriterLabel,NULL,NULL,@At),(@WriterLabel,NULL,NULL,@At);
    IF @@ROWCOUNT<>2 THROW 51533, 'Ordinary keyless duplicate insert failed.', 1;
    UPDATE TOP(1) dbo.LegacyBalances SET Balance=1.23,Note=N'updated '
        WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),@WriterLabel)
          AND Balance IS NULL AND Note IS NULL AND AsOf=@At;
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary keyless update failed.', 1;
    DELETE dbo.LegacyBalances WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),@WriterLabel);
    IF @@ROWCOUNT<>2 THROW 51533, 'Ordinary keyless duplicate delete failed.', 1;
    INSERT dbo.EventLog(EventCode,Description,OccurredAt) VALUES(-2147483648,N'Ordinary append',@At);
    IF @@ROWCOUNT<>1 THROW 51533, 'Ordinary EventLog insert failed.', 1;
    ROLLBACK;
    SET @WriterVerified=1;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK;
    THROW;
END CATCH;
SET @Caught=0;
BEGIN TRY
    BEGIN TRANSACTION;
    UPDATE dbo.EventLog SET Description=Description WHERE EventCode=0;
    ROLLBACK;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK;
    IF ERROR_NUMBER()<>51000 THROW;
    SET @Caught=1;
END CATCH;
IF @Caught<>1 THROW 51534, 'Append-only EventLog guard not enforced.', 1;
SET @Passed+=1;
EXEC dbo.ReadReplicaEnvelope @SchemaJson=@SchemaAfter OUTPUT,@RowsJson=@RowsAfter OUTPUT;
IF CONVERT(varbinary(max),@SchemaBefore)<>CONVERT(varbinary(max),@SchemaAfter)
   OR CONVERT(varbinary(max),@RowsBefore)<>CONVERT(varbinary(max),@RowsAfter)
    THROW 51535, 'Source tests changed durable business data/schema.', 1;
SELECT N'Passed' AS Tests,@Passed AS ExpectedFailuresVerified,@WriterVerified AS OrdinaryWriterDmlVerified;
GO
