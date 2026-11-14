# Copying or syncing SharePoint folders into a dataset

## Copy or sync SharePoint folders into a dataset

A SharePoint data source has no tables: it copies a folder of a document library into a new top-level dataset of the project, once or on a cron schedule.
With `index_files` a sync also keeps a Delta feature group with one row per file in the dataset; search it with **hops-sharepoint-search**.
Each sync is an ordinary Python job, `sharepoint_<connector>_<dataset>`, running the script `DataSources/<connector>/sharepoint_sync.py` that data owners can edit; `reset_script()` restores the template.
Creating, running and changing syncs needs the Data Owner role; members read the dataset and the audit log.
With mirror deletions (the default for a schedule) the dataset equals the folder after each run; files edited or deleted in Hopsworks are restored, and data never flows back to SharePoint.

### Set up SharePoint access first

Hopsworks reads SharePoint as a Microsoft Entra ID application (client credentials, no user sign-in), so a tenant administrator prepares:

- An app registration (single tenant, no redirect URI); note its **tenant id** and **client id**.
- A Microsoft Graph **application** permission with admin consent: `Sites.Read.All`, or `Sites.Selected` plus a read grant on the site (`POST /sites/{site-id}/permissions`).
- A client secret: copy its **Value**, not the Secret ID (`AADSTS7000215` means the wrong one was used).
- A SharePoint Online license on the tenant and the site URL, e.g. `https://contoso.sharepoint.com/sites/Finance`.
- Outbound HTTPS from the cluster to `login.microsoftonline.com`, `graph.microsoft.com` and `<tenant>.sharepoint.com` (other hosts for `US_GOV` and `CHINA`).

Check the credentials before creating anything with `hops datasource create sharepoint ... --test`, which lists the site's libraries or exits 1.
Step-by-step registration, the `Sites.Selected` grant commands and a troubleshooting table by error code are in [references/sharepoint-setup.md](sharepoint-setup.md).

```python
from hsfs.storage_connector import SharePointConnector

sc = fs.create_data_source(SharePointConnector(
    id=None, name="finance_sharepoint", featurestore_id=fs.id,
    tenant_id="...", client_id="...", client_secret="...",
    site_url="https://contoso.sharepoint.com/sites/Finance",
    cloud="GLOBAL",                     # default; GLOBAL | US_GOV | CHINA
)).storage_connector
sc.list_drives()                        # [{"id": "b!...", "name": "Documents", "web_url": ...}, ...]
sc.list_folders(drive_id="b!...", path="Reports/2026")

copy = sc.copy_folder(drive_id="b!...", folder_path="Reports/2026", dataset_name="reports_2026")
# = create_sync(..., schedule=None, mirror_deletions=False, run_now=True)

sync = sc.create_sync(
    drive_id="b!...",
    folder_path="Reports/2026",         # "" for the library root
    dataset_name="sharepoint",          # default; must not exist
    schedule="0 0 * ? * *",             # Quartz cron in UTC, seconds first (hourly); None = copy once
    include=["*.pdf", "*.xlsx"],
    exclude=["drafts/**"],
    max_file_size_mb=1024,
    mirror_deletions=True,
    alert_receiver="ops-slack",         # optional; FAILED and KILLED alerts on the sync job
    run_now=True,
    index_files=True,                   # optional; index feature group sharepoint_<dataset>, version 1
)
sync.status()      # dict: state, paused, scheduled, last_run_*, files_total, bytes_total, last_error, ...
sync.run()         # returns the Execution
sync.pause(); sync.resume()
sync.set_schedule("0 0 2 ? * *"); sync.set_schedule(None)
sync.force_full_resync()               # next run compares checksums
sync.set_folder("Reports/2027")        # re-point; next run reconciles
sync.update(index_files=False)         # stop indexing; the feature group is kept
sync.get_index_feature_group()         # FeatureGroup, after the first indexed run
sync.audit_path                        # ".../DataSources/finance_sharepoint/audit/sharepoint"
sync.get_audit(day=None)               # changed paths of a day (default: today, UTC)
sync.delete(keep_files=True)           # without keep_files the dataset is deleted; False = still DELETING, sync.retry() finishes it
sc.get_syncs()
```

```bash
hops datasource sharepoint drives <name>
hops datasource sharepoint folders <name> --drive DRIVE_ID [--path P]
hops datasource sharepoint copy <name> --drive DRIVE_ID [--folder P] [--dataset N]
hops datasource sharepoint sync create <name> --drive DRIVE_ID [--folder P] [--cron "0 0 * ? * *"] [--include G]... [--exclude G]... [--no-mirror-deletions] [--no-run-now] [--index-files] [--index-feature-group FG]
hops datasource sharepoint sync index <name> SYNC_ID --on|--off [--feature-group FG]
hops datasource sharepoint sync list|status|run|pause|resume|resync|schedule|set-folder|delete|audit <name> [SYNC_ID] ...
hops datasource sharepoint reset-script <name>
```

The index feature group is partitioned by `ingestion_date`, the day a file was first indexed, and keyed by the SharePoint item id; its columns are listed in **hops-sharepoint-search**.
Each run writes the rows of new, changed, renamed and removed files with delta-rs, and compacts each earlier day that holds more than one file into files of about 200 MB.
Deleting a sync deletes its index feature group unless `keep_files=True`.

The audit log is CSV in `date=YYYY-MM-DD` partitions (columns `execution_id, op, kind, path, src_path`), written only by runs that changed something and kept 7 days by default; read it with DuckDB `read_csv(..., hive_partitioning = true)` to prune by date.
