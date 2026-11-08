USE SqlSyncSource;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF EXISTS(SELECT 1 FROM dbo.LegacyBalances WHERE AccountLabel=N'APP-EXTERNAL')
    THROW 51700, 'This one-time normal-writer demonstration already exists.', 1;
BEGIN TRY
    BEGIN TRANSACTION;
    INSERT dbo.LegacyBalances(AccountLabel,Balance,Note,AsOf)
    VALUES(N'APP-EXTERNAL',50.25,N'Ordinary SQL without a source key',CONVERT(datetime2(3),'2026-04-01')),
          (N'APP-EXTERNAL',50.25,N'Ordinary SQL without a source key',CONVERT(datetime2(3),'2026-04-01'));
    DELETE TOP(1) FROM dbo.LegacyBalances WHERE AccountLabel=N'APP-EXTERNAL';
    UPDATE dbo.LegacyBalances SET Balance=75.50,Note=N'Updated by normal application SQL'
    WHERE AccountLabel=N'APP-EXTERNAL';
    COMMIT;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK;
    THROW;
END CATCH;
DECLARE @CapturedLsn varbinary(10);
EXEC dbo.WaitForCdcCapture @CapturedLsn=@CapturedLsn OUTPUT;
SELECT N'Ordinary INSERT/DELETE/UPDATE committed without an application lock or source primary key.' AS Demonstration,
       CONVERT(varchar(22),@CapturedLsn,1) AS CapturedLsn,
       (SELECT COUNT(*) FROM dbo.LegacyBalances WHERE AccountLabel=N'APP-EXTERNAL') AS SurvivingDuplicates;
