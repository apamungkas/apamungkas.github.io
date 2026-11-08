USE SqlSyncSource;
GO
CREATE OR ALTER PROCEDURE dbo.GenerateDemoChanges @Round int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT<>0 THROW 51300, 'Demo changes require no ambient transaction.', 1;
    IF @Round IS NULL OR @Round NOT BETWEEN 1 AND 1000000 THROW 51301, 'Round must be 1..1000000.', 1;
    DECLARE @Lock int,@Suffix nvarchar(20)=CONVERT(nvarchar(20),@Round),
            @At datetime2(3)=DATEADD(second,@Round,CONVERT(datetime2(3),'2026-02-01T00:00:00.000')),
            @Duplicate nvarchar(80)=N'round-duplicate-'+CONVERT(nvarchar(20),@Round);
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC @Lock=sys.sp_getapplock @Resource=N'SqlSync.Source',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=60000;
        IF @Lock<0 THROW 51302, 'Could not serialize demo changes.', 1;
        IF NOT EXISTS(SELECT 1 FROM dbo.ReplicationState WHERE SingletonId=1 AND IsBootstrapped=1)
            THROW 51303, 'Deliver and acknowledge baseline before generating changes.', 1;
        EXEC dbo.AssertBusinessSchema;
        IF EXISTS(SELECT 1 FROM dbo.DemoRuns WHERE Round=@Round)
        BEGIN
            COMMIT;
            SELECT @Round AS Round,CAST(1 AS bit) AS WasReplay;
            RETURN;
        END;
        INSERT dbo.Customers(CustomerId,CustomerName,Email,CreditLimit,UpdatedAt)
            VALUES(1000+@Round,N'Customer '+@Suffix,NULL,NULL,@At),
                  (-@Round,N'Transient customer',NULL,NULL,@At);
        UPDATE dbo.Customers SET CustomerName=N'ADA '+@Suffix+N'  ',
            Email=CASE WHEN @Round%2=0 THEN NULL ELSE N'ada@example.invalid' END,
            CreditLimit=CASE WHEN @Round%2=0 THEN NULL ELSE 1000.00+@Round END,UpdatedAt=@At WHERE CustomerId=1;
        IF @@ROWCOUNT<>1 THROW 51304, 'Seed Customer 1 missing.', 1;
        DELETE dbo.Customers WHERE CustomerId=-@Round;
        IF @@ROWCOUNT<>1 THROW 51304, 'Transient Customer missing.', 1;
        INSERT dbo.Products(SKU,ProductName,Price,Notes,UpdatedAt)
            VALUES(N'ROUND-'+@Suffix,N'Product '+@Suffix,10.00+@Round,N'before  ',@At);
        UPDATE dbo.Products SET SKU=N'ROUND-'+@Suffix+N'-RENAMED',Notes=NULL,UpdatedAt=DATEADD(millisecond,1,@At)
            WHERE SKU=N'ROUND-'+@Suffix;
        IF @@ROWCOUNT<>1 THROW 51305, 'Expected round Product missing.', 1;
        INSERT dbo.Products(SKU,ProductName,Price,Notes,UpdatedAt) VALUES(N'DELETE-'+@Suffix,N'Transient',1.00,NULL,@At);
        DELETE dbo.Products WHERE SKU=N'DELETE-'+@Suffix;
        IF @@ROWCOUNT<>1 THROW 51306, 'Transient Product missing.', 1;
        INSERT dbo.LegacyBalances(AccountLabel,Balance,Note,AsOf)
            VALUES(@Duplicate,NULL,N'  ',NULL),(@Duplicate,NULL,N'  ',NULL);
        DELETE TOP(1) FROM dbo.LegacyBalances
            WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),@Duplicate)
              AND Balance IS NULL AND CONVERT(varbinary(200),Note)=CONVERT(varbinary(200),N'  ') AND AsOf IS NULL;
        IF @@ROWCOUNT<>1 THROW 51307, 'Identical keyless duplicate missing.', 1;
        UPDATE dbo.LegacyBalances SET Balance=CASE WHEN @Round%2=0 THEN NULL ELSE CONVERT(decimal(18,2),@Round) END,
            Note=N'nullable round '+@Suffix+N' ' WHERE AccountLabel IS NULL AND AsOf IS NULL;
        IF @@ROWCOUNT<>1 THROW 51308, 'NULL-bearing seed row missing.', 1;
        UPDATE dbo.LegacyBalances SET Balance=Balance+1.00,Note=N'Updated '+@Suffix+N'  '
            WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),N'Case');
        IF @@ROWCOUNT<>1 THROW 51309, 'Case-sensitive seed row missing.', 1;
        IF @Round=1
        BEGIN
            DELETE TOP(1) FROM dbo.LegacyBalances
                WHERE CONVERT(varbinary(160),AccountLabel)=CONVERT(varbinary(160),N'duplicate')
                  AND Balance=12.34 AND Note IS NULL AND AsOf=CONVERT(datetime2(3),'2026-01-01T00:00:00.000');
            IF @@ROWCOUNT<>1 THROW 51310, 'Original duplicate missing.', 1;
        END;
        -- ASCII source constructs synthetic CJK/Greek text to exercise Unicode transport.
        INSERT dbo.EventLog(EventCode,Description,OccurredAt)
            VALUES(@Round,CASE WHEN @Round%2=0 THEN NULL ELSE N'Round '+@Suffix+N' '+NCHAR(28450)+NCHAR(937)+N'  ' END,@At);
        INSERT dbo.DemoRuns(Round,ExecutedAt,BusinessStatements) VALUES(@Round,SYSUTCDATETIME(),CASE WHEN @Round=1 THEN 13 ELSE 12 END);
        COMMIT;
        SELECT @Round AS Round,CAST(0 AS bit) AS WasReplay;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO
