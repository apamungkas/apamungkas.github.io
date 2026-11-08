# Source-to-Target CDC Synchronization Test

This walkthrough demonstrates a source data change, the period before replication, delivery through the Fabric pipeline, and verification at the destination. It uses the existing lab commands; it does not provision or reinstall the environment.

For the complete project setup, see the [README](README.md). This procedure expands the [manual replication test](README.md#manual-replication-tests).

## Prerequisites and safety

- Azure resources, SQL objects, Fabric connections, and pipelines are already deployed and working.
- The initial baseline has been delivered to the destination and acknowledged at the source.
- Use the original configured deployment folder, including its private configuration and generated state. A fresh download without that state is not sufficient.
- Use the existing authenticated Azure CLI identity with the required Fabric and VM Run Command permissions.
- Run commands in the workstation's **PowerShell terminal in VS Code**, not in the SSMS query editor or a VM terminal.
- Run each command separately. Wait for success before proceeding; stop and investigate errors.
- Stop external application writers and do not start other generators or synchronization jobs during this controlled test.
- This test changes demo business data. Do not run it against production databases.
- Do not combine this sequence with the README's fresh-lab automation on the same test state; that automation expects specific rounds and row counts.

## Preparation: open the existing deployment folder

Replace the example path with the location of your original deployment folder:

```powershell
Set-Location "C:\path\to\sqlsync-lab"
.\.venv\Scripts\Activate.ps1
```

If organizational policy blocks activation, do not weaken it. Replace `python` in subsequent commands with `.\.venv\Scripts\python.exe` instead.

### Pause an existing schedule

Run this command **only if a synchronization schedule already exists**:

```powershell
python fabric.py pause
```

If no schedule exists, skip it. Pausing prevents future scheduled triggers but does not cancel queued or running jobs. Wait for all existing jobs to finish in Fabric Monitor before continuing. Keep the schedule paused and other writers stopped through validation.

## Step 1: inspect source and destination before the change

Run the inspection pipeline:

```powershell
python fabric.py run --pipeline pl_inspect_sql_demo
```

Inspect the table contents and replication status directly through administrative VM Run Command:

```powershell
python deploy.py sql --role source --file inspect-source.sql
python deploy.py sql --role target --file inspect-target.sql
```

**Check:** record the current table row counts, sample contents, and replication status. The baseline should already be synchronized, with no unresolved source batch pending. Resolve any existing backlog or failure before generating another round.

For a tabular view in SSMS, open [inspect-source.sql](inspect-source.sql) on the source SQL Server and [inspect-target.sql](inspect-target.sql) on the destination SQL Server, then execute each query. These scripts are read-only and display up to 1,000 rows per business table; they are not a full reconciliation test.

## Step 2: generate a new source change round

Use round **100 only if it has not already been used**:

```powershell
python fabric.py run --pipeline pl_generate_demo_changes --round 100
```

The generator performs INSERT, UPDATE, and DELETE operations. Repeating a round deliberately makes no new business changes. If round 100 was used previously, choose another unused round between 1 and 1,000,000 and adjust the expected identifiers below.

Inspect both databases again, **before running synchronization**:

```powershell
python deploy.py sql --role source --file inspect-source.sql
python deploy.py sql --role target --file inspect-target.sql
```

**Check:** the new round's changes should exist at the source but not yet at the destination, because scheduling and other synchronization jobs are stopped.

For a newly generated round 100, useful identifiers include:

| Table | Expected source result after generation |
| --- | --- |
| `Customers` | New `CustomerId = 1100`, with `CustomerName = Customer 100` |
| `Products` | New product with final `SKU = ROUND-100-RENAMED` |
| `LegacyBalances` | One surviving row with `AccountLabel = round-duplicate-100` |
| `EventLog` | New row with `EventCode = 100` |

These are examples of observable results, not the complete list of changes. If an identifier is outside the inspection scripts' 1,000-row sample, use SSMS to inspect that row specifically; absence from the sample does not establish absence from the table.

## Step 3: wait for native CDC capture

```powershell
python deploy.py sql --role source --file capture-barrier.sql
```

**Check:** the command completes successfully and returns a captured LSN.

[capture-barrier.sql](capture-barrier.sql) writes a non-business marker and waits until native SQL Server CDC captures it. A committed source transaction may not be captured immediately, which is why this step precedes synchronization.

This command is **not read-only**. It does not copy data, apply destination changes, or prove that the destination is synchronized.

## Step 4: synchronize source changes to the destination

```powershell
python fabric.py run --pipeline pl_cdc_sync
```

The pipeline runs **Prepare → Copy → Apply → Acknowledge**.

**Check in Fabric Monitor and the recorded activity results:**

- The pipeline completes successfully.
- Prepare returns a batch manifest with its batch ID and LSN range.
- Copy reports `rowsCopied` greater than zero for the newly generated, not-yet-delivered business changes.
- Apply succeeds at the destination.
- Acknowledge succeeds at the source.

Copy rows represent CDC event images, not the net number of new business rows. An UPDATE produces before and after images, so do not equate `rowsCopied` with a table's row-count increase.

If a command times out, inspect the existing run in Fabric Monitor before submitting another run; a local timeout does not establish that the service-side job stopped. If synchronization fails, investigate and recover the same pending batch. Never manually skip an LSN range or acknowledge an unapplied batch.

## Step 5: inspect the destination after synchronization

```powershell
python fabric.py run --pipeline pl_inspect_sql_demo
python deploy.py sql --role source --file inspect-source.sql
python deploy.py sql --role target --file inspect-target.sql
```

**Check:** the identifiers and final values from Step 2 now appear at the destination as well as at the source. Compare the source and destination results, not just total row counts.

You can also rerun the two inspection scripts in SSMS. Successful visual inspection demonstrates delivery, but does not by itself prove exact equality of all data.

## Step 6: validate exact data equality

Keep all external writers, generators, and scheduling stopped. Confirm there are no queued/running synchronization jobs and no source batch remains pending.

If a pre-existing pending batch was recovered rather than all current changes delivered, repeat normal synchronization as needed to catch up through the captured boundary. Do not advance checkpoints manually.

Run validation:

```powershell
python fabric.py run --pipeline pl_validate_replica
```

**Required result:**

- `IsValid = 1` or `true`.
- Source and destination checkpoints and last batch match.
- Schema and row-multiset contents match, including duplicate multiplicity.

Follow the [exact reconciliation procedure](README.md#exact-reconciliation-procedure) if validation fails. Do not treat equal row counts alone as success or validate while source/destination data is changing.

## Step 7: optionally resume the existing schedule

After successful validation, deliberately resume normal writers if appropriate. If a schedule existed before the test and should run again:

```powershell
python fabric.py resume
```

Skip this command if no schedule exists or if it should remain paused. Resume only re-enables the existing schedule; it does not create one or extend its expiry. An expired schedule requires review and an end-date update in Fabric before expecting new scheduled runs.

## Completion checklist

- [ ] Observed the new round at the source before it appeared at the destination.
- [ ] Waited for native CDC capture.
- [ ] Confirmed successful Prepare, Copy, Apply, and Acknowledge.
- [ ] Observed the same final changes at the destination.
- [ ] Obtained `IsValid = 1/true` under quiet conditions.
- [ ] Deliberately restored the intended writer and scheduling state.
