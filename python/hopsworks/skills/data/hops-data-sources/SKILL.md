---
name: hops-data-sources
description: Mount or ingest a table from a supported datasource. Mount tables from a datasource as an external feature group or ingest data into a new feature group using DLTHub. Auto-invoke when user works with external data (Snowflake, BigQuery, Redshift, S3, ADLS, GCS, JDBC, SQL, Databricks Unity Catalog, Postgres, MySQL, Oracle, ClickHouse, Teradata, SQL Server, SAP, MongoDB, Elasticsearch, another Hopsworks cluster, CRM, REST APIs, Gmail) or copies files from SharePoint into a dataset. For searching copied emails use hops-email-search.
---

Prefer the `hops` CLI for mounting or ingesting external tables from a datasource. Use the `hopsworks` Python SDK if the CLI is unsuccessful.

Read a data source only through Hopsworks: mount it as an external feature group, look at it with a query that has a LIMIT, or ingest it. Never open a database driver (psycopg2, SQLAlchemy, pymysql, JDBC) with a credential copied from a secret, a variable or the connector. To look at a table, run `hops datasource preview <ds> --table <t> [--database <db>]` (`<schema>.<t>` when the name is in several schemas), or mount a query and read it: `hops fg create-external <fg> --connector <ds> --query "SELECT ... LIMIT 100" --primary-key <col> --no-statistics`, then `hops fg preview <fg>` (notebook: `fs.get_feature_group("<fg>", 1).read()`). A query mount is written against the source's schema (`schema.table`, as `hops datasource tables <ds>` shows), not the `--database` name. `read()` pulls the whole mount, so put the LIMIT or filter in the query. `connector.read()` raises `NotImplementedError` for a SQL source on the Python engine. `hops sql` cannot read a mount (`TABLE_NOT_FOUND`); use `hops fg preview` or `fg.read()`. A failed read is reported with its error, not routed around with a driver.

`hops datasource infer-metadata` is optional: it needs the cluster's platform intelligence key and comes after the look, never before it.

A secret option of `hops datasource create` (`--password`, `--api-key`, ...) takes the value itself, `-` to read it from stdin, or `account:NAME` to read your account variable `NAME`. Use `account:NAME` for a credential the user saved in their account (the Factory's forms do): the value goes to the data source without a shell, a file or a printout, and never through `$(...)` around a command that also prints, such as `hopsworks.login()`.

Never ask the user for a password, token or key in the conversation, in a message or with AskUserQuestion: whatever is typed there reaches the model. In the Factory's chat, end your reply with a `hops-secret` block instead. It is a form: the secret as a hidden field, the plain settings that go with it (`fields`, never a secret) and, for a data source, a `test` command the form's Test button runs:

```hops-secret
{"name": "CRM_PASSWORD", "label": "CRM password", "description": "Password of the hopsworks_reader user",
 "fields": [{"name": "host", "label": "Host"}, {"name": "port", "label": "Port", "default": "5432"},
            {"name": "database", "label": "Database"}, {"name": "user", "label": "User"}],
 "test": "hops datasource create sql crm --database-type POSTGRESQL --host {{host}} --port {{port}} --database {{database}} --user {{user}} --password account:CRM_PASSWORD --test"}
```

`name` is in capitals, digits and underscores. `test` is a `hops datasource create ... --test` command, which checks the connection and creates nothing; `{{field}}` stands for a field's value. The chat saves the secret as the user's private account variable `name`, and replies with the settings and the secret's name, never its value. Then create the data source with the same command without `--test`. Outside the chat, ask the user to add the account variable in their account settings and tell you its name.

Mounting and ingesting both build a feature pipeline's input side: an external feature group leaves data in the source (no copy, no copy-time MITs); a DLTHub ingest copies the source into a managed feature group. Mounting is the lower-cost path when the source already holds the data you want, since reuse beats rebuilding a pipeline.

Google Drive and Google Analytics sources have no tables: [references/google-sources.md](references/google-sources.md).

## Contract
- **Input:** a configured connector + a table from it.
- **Output:** an external feature group (mounted in place), or a managed feature group ingested via DLTHub.
- **Pre-condition:** a connector exists. Create one with `hops datasource create <type>` (see below), or in the UI.
- **Pick mount vs ingest:** mounting serves the offline store only. To load the online store or a vector index, ingest (an ETL path) instead.

## Smoke-test: what connectors exist

```bash
hops datasource list                 # configured connectors; STATE is the owner's last answer
hops datasource info <connector>     # detail for one, with its state and Hopsworks' message
```

## Infer metadata (platform intelligence)

Use platform intelligence to (1) select the primary key and event_time columns and (2) write column descriptions for the mounted/ingested feature group. It returns, per column, a suggested snake_case feature name, an offline data type, and a one-line description, plus a suggested primary key and event-time column.

```bash
hops datasource infer-metadata <connector-name> <table> [--database <db>]
```

If platform intelligence is not configured on the cluster, the command exits with a clear error. Ask the cluster admin to set `PLATFORM_INTELLIGENCE_LLM_API_KEY` rather than guessing the metadata by hand.

Programmatic equivalent: `data_source.infer_metadata()` on a `DataSource` returned by `data_source.get_tables()`; it raises `hopsworks.client.exceptions.PlatformIntelligenceException` (with `.reason` of `NOT_CONFIGURED` or `INFERENCE_FAILED`) on the same failure modes.

## Preview source schema and data (CRM / Google Sheets / REST)

Non-SQL sources (CRM, Google Sheets, REST) have no catalog to read the schema from, so Hopsworks samples the source with a server-side schema-fetch job and infers it.

```bash
hops datasource tables <connector>                     # a CRM's resources (account, contact, ...)
hops datasource preview <connector> --table account    # its inferred schema and sampled rows
hops datasource preview <connector> --table account --table lead   # several: one schema-fetch job
```

Preview only the tables the request names; the listing above is the one call that shows the rest. These sources cannot be mounted (`hops fg create-external` refuses a CRM source), so `hops datasource preview` reads a named table's rows here. A named table of a SQL, warehouse or object-store source is mounted instead (Mount a table as an external feature group) and read with `hops fg preview <fg> --limit 20`. A loop of previews over every table reads tables nobody asked about, one call each.

Previews go through `hops datasource preview`, several tables in one call (a CRM, Google Sheets or REST source fetches them with one job; Google Analytics one by one), so the host shows each table. The SDK calls are for a program that needs the rows.

The SDK calls (`get_data`, `get_data_batch`, the `.preview` dict shape, REST endpoints, the cache): `hops skills info hops-data-sources references/preview-sdk.md`.

## Mount a table as an external feature group

An external feature group leaves data in the source and queries it through the connector, no copy into Hopsworks. It is offline-only: reads serve training and batch inference. Set an `event_time` column so the feature store can read point-in-time correct snapshots and so polling can read a `start_time`/`end_time` range for backfill or incremental runs.

```python
import hopsworks

project = hopsworks.login()
fs = project.get_feature_store()

ds = fs.get_data_source("my_connector")          # the configured connector
table = ds.get_tables(database="my_db")[0]        # pick a table
meta = table.infer_metadata()                     # PK / event_time / descriptions

ext_fg = fs.create_external_feature_group(
    name="customers_external",
    version=1,
    data_source=table,                            # the source table
    primary_key=meta.primary_key,
    event_time=meta.event_time,
    online_enabled=False,
    statistics_config=False,                      # statistics later, see below
)
ext_fg.save()
```

Statistics read the whole source in a job that `save()` waits for, so mount without them. To compute them later: `ext_fg.statistics_config.enabled = True`, `ext_fg.update_statistics_config()`, then `ext_fg.compute_statistics()` (it waits for the job).

### A folder in the project (HopsFS)

A folder of parquet, csv or orc files, or a Delta or Iceberg table folder, in a project dataset mounts through a HOPSFS data source on that dataset. `--dataset` takes the dataset's name, never a path: `Resources`, `<project>_Training_Datasets`, or `<backing name>_featurestore.db`, where each feature group's table is the folder `<name>_<version>`. Reuse a HOPSFS data source on that dataset when `hops datasource list` shows one. A HOPSFS data source has no connection test: its create checks that the dataset exists. `--path` is the folder relative to the dataset. A folder with `_delta_log/` is Delta, one with `metadata/` is Iceberg; otherwise the files' extension names the format. The create reads the schema from the files, so `--features` is not needed, and `--primary-key` can wait until `hops fg info` has shown the columns.

```bash
hops files list /apps/hive/warehouse/<backing name>_featurestore.db   # the feature store's table folders; other datasets: hops files list <dataset>
hops datasource create hopsfs <ds> --dataset <backing name>_featurestore.db
hops fg create-external <fg> --connector <ds> --path <folder> --data-format <parquet|csv|orc|delta|iceberg> --no-statistics
hops fg info <fg>               # the columns
hops fg preview <fg> --limit 5
```

## Create a connector

`hops datasource create <type>` exists for every type the UI offers; `--help` lists its options and the secret variables. Every type's command line, secrets from stdin or `HOPSWORKS_DS_*` variables, HopsFS key paths and `--argument`: `hops skills info hops-data-sources references/create.md`. Oracle's `PROVIDED` credentials mode (each member reads with their own login): `hops skills info hops-data-sources references/provided-credentials.md`.

A source whose STATE is REFUSED (AWS no longer trusts Hopsworks) or REVOKED (the vendor grant is gone) cannot be read; tell the user before reading from it. `-` means Hopsworks keeps no state for that type.

**Confirm before deleting.** `hops datasource delete` removes the source and every feature group made from it, with their data and the jobs Hopsworks made for them; it lists those groups first. Confirm the exact name and that list with the user. It deletes only the source it read, never a same-name one made since. In `--json` mode it needs `--yes`.

## Tag a data source

Schematized tags classify a data source (owner, source system, sensitivity) and make it findable in search.
A tag that an admin made mandatory for data sources must be passed when the data source is created.
`hops datasource create` cannot attach tags, so it fails while a tag is mandatory; create the data source from Python with `sc.save(tags=[...])` or `fs.create_data_source(sc, tags=[...])` instead.
The SDK calls, mandatory tags and search are in [references/tags.md](references/tags.md).

## Ingest into a new feature group with DLTHub

DLTHub copies a source into a managed feature group for the cases mounting cannot serve: loading the online store or a vector index, or pulling from an API/SaaS/REST endpoint. Ingestion runs server-side in the `dlthub-ingestion-pipeline` environment, driven by a `SinkJobConfiguration` attached to a sink-enabled feature group. It is configuration plus a server job, not an in-process `dlt` call.

One table, from the CLI: the group's schema is read from the source, then its ingestion job runs once and the command waits for it.

```bash
hops fg ingest sf_accounts --connector my_salesforce --table account --primary-key id
hops job run <job printed above>      # copy again later
```

Copy only new rows on later runs: pick the strategy by the cursor column's type, `incremental-id` (an increasing number), `incremental-timestamp` (a timestamp) or `incremental-date` (a date). Each run reads from where the last one stopped; `--start` sets where the first run begins: an id for `incremental-id`; for `incremental-timestamp` epoch milliseconds or an ISO time or dashed date (`2026-11-01`), read as UTC when it names no zone; for `incremental-date` a date (`2026-11-01` or `20261101`) or an ISO time. The default `--strategy full` copies the whole table each run.

```bash
hops fg ingest orders --connector pg --table orders --primary-key id \
  --strategy incremental-id --cursor id --start 0
hops fg ingest events --connector pg --table events --primary-key id \
  --strategy incremental-timestamp --cursor updated_at --start 2026-11-01T00:00:00Z
```

The per-feature-group `sink_job` (`hops fg ingest`, or the SDK form in references/ingest-sdk.md) is for ingesting a **single** table. Without `features`, `save()` reads the schema from the data source, as `hops datasource preview` shows it. When ingesting more than one table from the same connector, default to one multi-table job for all of them — see "Ingest many tables with one job" in references/ingest-sdk.md — instead of looping this pattern.

The SDK form (`SinkJobConfiguration`, `loading_config`, a custom transform), tagging an ingested table as bronze for **hops-analytics**, many tables in one job with per-table control and resource estimates, and Google Sheets tabs: `hops skills info hops-data-sources references/ingest-sdk.md`.

## Mount a feature group from another Hopsworks cluster

A `hopsworks` data source reads a project on another Hopsworks cluster.
Its databases are the feature stores the remote project can read, its own `<project>_featurestore` and the ones shared with it, and its tables are their feature groups, named `<name>_<version>`.
External feature groups are read through the remote cluster's Hopsworks Query Service, and DLTHub ingestion copies one into a local feature group.
The rest, with the CLI and SDK calls: `hops skills info hops-data-sources references/remote-hopsworks.md`.

## Copy or sync SharePoint folders into a dataset

A SharePoint data source has no tables: it copies a folder of a document library into a new top-level dataset of the project, once or on a cron schedule.
With `index_files` a sync also keeps a Delta feature group with one row per file in the dataset; search it with **hops-sharepoint-search**.
Each sync is an ordinary Python job, `sharepoint_<connector>_<dataset>`, running the script `DataSources/<connector>/sharepoint_sync.py` that data owners can edit; `reset_script()` restores the template.
Creating, running and changing syncs needs the Data Owner role; members read the dataset and the audit log.
With mirror deletions (the default for a schedule) the dataset equals the folder after each run; files edited or deleted in Hopsworks are restored, and data never flows back to SharePoint.
The rest, with the CLI and SDK calls: `hops skills info hops-data-sources references/sharepoint.md`.

## Copy a Gmail mailbox into a feature group

A `gmail` data source is a mailbox, read with an OAuth refresh token or a Workspace service account with domain-wide delegation, both with the `gmail.readonly` scope.
Its credentials are write-only; code that calls Gmail itself asks for a short-lived token with `get_access_token()`.
It is not browsed or mounted: a sync job upserts its emails into a Delta feature group named after the mailbox (`jane.doe@gmail.com` → `jane_doe_gmails`), partitioned by the `day` of the UTC receive time (an hourly job adds a file per run, and the first run after midnight compacts the finished day), and writes attachments as files whose paths the `attachments` column lists.
The rest, with the CLI and SDK calls: `hops skills info hops-data-sources references/mailboxes.md`.

## Copy an Outlook mailbox into a feature group

An `outlook` data source is a Microsoft 365 or Outlook.com mailbox, read through Microsoft Graph with an app registration in Microsoft Entra ID:
- `OAUTH`: a refresh token the mailbox owner granted with `Mail.Read offline_access`;
- `CLIENT_CREDENTIALS`: a client secret of an app with the `Mail.Read` application permission in the mailbox's tenant, which needs `--tenant-id`.
The rest, with the CLI and SDK calls: `hops skills info hops-data-sources references/mailboxes.md`.

## Next Steps

An Elasticsearch index mounts as an external feature group too: `hops skills info hops-data-sources references/elasticsearch.md`.

- Discover connectors/tables first: **hops-data-discovery**.
- Work with the resulting feature group: **hops-fg** (read/insert), **hops-fv** (build a view).
- Query the mounted table via SQL: **hops-trino-sql**.
