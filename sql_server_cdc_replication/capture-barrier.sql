USE SqlSyncSource;
GO
DECLARE @Lsn varbinary(10);
EXEC dbo.WaitForCdcCapture @CapturedLsn=@Lsn OUTPUT;
SELECT CONVERT(varchar(22),@Lsn,1) AS CapturedLsn;
