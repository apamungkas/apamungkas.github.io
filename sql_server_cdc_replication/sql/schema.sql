-- Install in BOTH databases after source.sql / target.sql, using the corresponding connection.
-- No USE statement: this shared file deliberately stays in the connection's database.
IF DB_NAME() NOT IN (N'SqlSyncSource',N'SqlSyncDestination')
    THROW 51600, 'Connect to SqlSyncSource or SqlSyncDestination before installing schema.sql.', 1;
GO
CREATE OR ALTER VIEW dbo.ExpectedBusinessColumns
AS
SELECT CONVERT(sysname,TableName) AS TableName,ColumnId,CONVERT(sysname,ColumnName) AS ColumnName,
       CONVERT(sysname,TypeName) AS TypeName,MaxLength,NumericPrecision,NumericScale,IsNullable
FROM (VALUES
    (N'Customers',1,N'CustomerId',N'int',4,10,0,0),
    (N'Customers',2,N'CustomerName',N'nvarchar',200,0,0,0),
    (N'Customers',3,N'Email',N'nvarchar',400,0,0,1),
    (N'Customers',4,N'CreditLimit',N'decimal',9,18,2,1),
    (N'Customers',5,N'UpdatedAt',N'datetime2',7,23,3,0),
    (N'Products',1,N'SKU',N'nvarchar',80,0,0,0),
    (N'Products',2,N'ProductName',N'nvarchar',200,0,0,0),
    (N'Products',3,N'Price',N'decimal',9,18,2,0),
    (N'Products',4,N'Notes',N'nvarchar',400,0,0,1),
    (N'Products',5,N'UpdatedAt',N'datetime2',7,23,3,0),
    (N'LegacyBalances',1,N'AccountLabel',N'nvarchar',160,0,0,1),
    (N'LegacyBalances',2,N'Balance',N'decimal',9,18,2,1),
    (N'LegacyBalances',3,N'Note',N'nvarchar',200,0,0,1),
    (N'LegacyBalances',4,N'AsOf',N'datetime2',7,23,3,1),
    (N'EventLog',1,N'EventCode',N'int',4,10,0,0),
    (N'EventLog',2,N'Description',N'nvarchar',400,0,0,1),
    (N'EventLog',3,N'OccurredAt',N'datetime2',7,23,3,0)
) v(TableName,ColumnId,ColumnName,TypeName,MaxLength,NumericPrecision,NumericScale,IsNullable);
GO
CREATE OR ALTER PROCEDURE dbo.AssertBusinessSchema
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    -- datetime2(3) is fixed by its type/scale; normalize engine metadata storage-length reporting.
    IF EXISTS
    (
        SELECT CONVERT(varbinary(256),TableName),ColumnId,CONVERT(varbinary(256),ColumnName),
            CONVERT(varbinary(256),TypeName),MaxLength,NumericPrecision,NumericScale,IsNullable
        FROM dbo.ExpectedBusinessColumns
        EXCEPT
        SELECT CONVERT(varbinary(256),t.name),c.column_id,CONVERT(varbinary(256),c.name),
            CONVERT(varbinary(256),TYPE_NAME(c.system_type_id)),
            CASE WHEN c.system_type_id=42 THEN 7 ELSE c.max_length END,
            CASE WHEN c.system_type_id=42 THEN 23 ELSE c.precision END,c.scale,c.is_nullable
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
        JOIN sys.columns c ON c.object_id=t.object_id
        WHERE s.name=N'dbo' AND t.name IN (N'Customers',N'Products',N'LegacyBalances',N'EventLog')
    ) OR EXISTS
    (
        SELECT CONVERT(varbinary(256),t.name),c.column_id,CONVERT(varbinary(256),c.name),
            CONVERT(varbinary(256),TYPE_NAME(c.system_type_id)),
            CASE WHEN c.system_type_id=42 THEN 7 ELSE c.max_length END,
            CASE WHEN c.system_type_id=42 THEN 23 ELSE c.precision END,c.scale,c.is_nullable
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
        JOIN sys.columns c ON c.object_id=t.object_id
        WHERE s.name=N'dbo' AND t.name IN (N'Customers',N'Products',N'LegacyBalances',N'EventLog')
        EXCEPT SELECT CONVERT(varbinary(256),TableName),ColumnId,CONVERT(varbinary(256),ColumnName),
            CONVERT(varbinary(256),TypeName),MaxLength,NumericPrecision,NumericScale,IsNullable
        FROM dbo.ExpectedBusinessColumns
    )
        THROW 51601, 'Business column schema is incompatible with the fixed demo contract.', 1;
    IF EXISTS
    (
        SELECT 1 FROM sys.columns c JOIN dbo.ExpectedBusinessColumns e
          ON c.object_id=OBJECT_ID(N'dbo.'+e.TableName) AND c.column_id=e.ColumnId
        WHERE c.is_identity=1 OR c.is_computed=1 OR c.is_sparse=1
           OR c.user_type_id<>c.system_type_id OR c.generated_always_type<>0
           OR (e.TypeName=N'nvarchar' AND c.collation_name<>N'Latin1_General_100_CI_AS')
    )
        THROW 51602, 'Computed, identity, alias, sparse, generated, or changed-collation columns are unsupported.', 1;
    IF (SELECT COUNT(*) FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.Customers') AND index_id>0)<>1
       OR NOT EXISTS
       (
           SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic
             ON ic.object_id=i.object_id AND ic.index_id=i.index_id
           WHERE i.object_id=OBJECT_ID(N'dbo.Customers') AND i.is_primary_key=1 AND i.is_unique=1
             AND i.type=1 AND i.is_disabled=0 AND i.has_filter=0
             AND ic.column_id=1 AND ic.key_ordinal=1 AND ic.is_descending_key=0
             AND (SELECT COUNT(*) FROM sys.index_columns x WHERE x.object_id=i.object_id AND x.index_id=i.index_id AND x.column_id>0)=1
       )
       OR (SELECT COUNT(*) FROM sys.indexes WHERE object_id=OBJECT_ID(N'dbo.Products') AND index_id>0)<>1
       OR NOT EXISTS
       (
           SELECT 1 FROM sys.indexes i JOIN sys.index_columns ic
             ON ic.object_id=i.object_id AND ic.index_id=i.index_id
           WHERE i.object_id=OBJECT_ID(N'dbo.Products') AND i.is_primary_key=0 AND i.is_unique=1
             AND i.type=2 AND i.is_disabled=0 AND i.has_filter=0
             AND ic.column_id=1 AND ic.key_ordinal=1 AND ic.is_descending_key=0
             AND (SELECT COUNT(*) FROM sys.index_columns x WHERE x.object_id=i.object_id AND x.index_id=i.index_id AND x.column_id>0)=1
       )
       OR EXISTS (SELECT 1 FROM sys.indexes
           WHERE object_id IN (OBJECT_ID(N'dbo.LegacyBalances'),OBJECT_ID(N'dbo.EventLog')) AND index_id>0)
        THROW 51603, 'Business key/index contract changed: Customers PK, Products unique SKU, two keyless heaps required.', 1;
END;
GO
CREATE OR ALTER VIEW dbo.BusinessRowImages
AS
SELECT CONVERT(nvarchar(128),N'Customers') AS TableName,
    (SELECT t.CustomerId,t.CustomerName,t.Email,t.CreditLimit,t.UpdatedAt
     FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER) AS RowJson FROM dbo.Customers t
UNION ALL
SELECT N'Products',(SELECT t.SKU,t.ProductName,t.Price,t.Notes,t.UpdatedAt
     FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER) FROM dbo.Products t
UNION ALL
SELECT N'LegacyBalances',(SELECT t.AccountLabel,t.Balance,t.Note,t.AsOf
     FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER) FROM dbo.LegacyBalances t
UNION ALL
SELECT N'EventLog',(SELECT t.EventCode,t.Description,t.OccurredAt
     FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER) FROM dbo.EventLog t;
GO
CREATE OR ALTER PROCEDURE dbo.ReadReplicaEnvelope
    @SchemaJson nvarchar(max) OUTPUT,@RowsJson nvarchar(max) OUTPUT
WITH EXECUTE AS OWNER
AS
BEGIN
    SET NOCOUNT ON;
    EXEC dbo.AssertBusinessSchema;
    SET @SchemaJson=
    (
        SELECT e.TableName,e.ColumnId,e.ColumnName,e.TypeName,c.max_length AS MaxLength,c.precision AS NumericPrecision,
               c.scale AS NumericScale,e.IsNullable,c.collation_name AS CollationName,
               CONVERT(bit,CASE WHEN e.TableName=N'Customers' AND e.ColumnId=1 THEN 1 ELSE 0 END) AS IsPrimaryKey,
               CONVERT(bit,CASE WHEN e.TableName IN (N'Customers',N'Products') AND e.ColumnId=1 THEN 1 ELSE 0 END) AS IsUniqueKey
        FROM dbo.ExpectedBusinessColumns e
        JOIN sys.columns c ON c.object_id=OBJECT_ID(N'dbo.'+e.TableName) AND c.column_id=e.ColumnId
        ORDER BY e.TableName COLLATE Latin1_General_100_BIN2,e.ColumnId
        FOR JSON PATH,INCLUDE_NULL_VALUES
    );
    IF (SELECT COUNT_BIG(*) FROM dbo.BusinessRowImages)>2000
       OR (SELECT COALESCE(SUM(CONVERT(bigint,DATALENGTH(RowJson))),0) FROM dbo.BusinessRowImages)>2097152
        THROW 51604, 'Full snapshot exceeds the bounded demo limit of 2000 rows / 2 MiB row JSON.', 1;
    -- Hex is the complete UTF-16 row image, not a hash. Group on bytes, never collation.
    SET @RowsJson=
    (
        SELECT TableName,CONVERT(varchar(max),CONVERT(varbinary(max),RowJson),1) AS RowHex,COUNT_BIG(*) AS Multiplicity
        FROM dbo.BusinessRowImages
        GROUP BY TableName,CONVERT(varbinary(max),RowJson)
        ORDER BY TableName COLLATE Latin1_General_100_BIN2,CONVERT(varbinary(max),RowJson)
        FOR JSON PATH
    );
END;
GO
EXEC dbo.AssertBusinessSchema;
GO
