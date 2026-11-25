# Copying files from S3, GCS, ADLS, SFTP or Google Drive into a dataset

**SFTP.** `hops datasource create sftp <name> --host H [--port 22] --username U [--path /] (--password - | --private-key-file PATH [--passphrase -])`: the key file is read locally and stored in Hopsworks, never uploaded to the project; `--password` and `--passphrase` also read `HOPSWORKS_DS_SFTP_PASSWORD` / `HOPSWORKS_DS_SFTP_PASSPHRASE`.
The credentials are write-only (`credentials_set` on read) and belong to the data source, so `hops datasource credentials set` does not apply; rotate them with `sc.set_credentials(password=...)` or `sc.set_credentials(private_key=..., passphrase=...)` then `sc.update()`.
`--test` only checks that an SSH server answers at the host and port.

**Google Drive.** `hops datasource create google-drive <name> --key-file ./sa.json --folder-id ID [--folder-name N]` uploads the service-account key to `Resources/datasources/<name>/` and sends its path, refusing to replace a key already there (`--key-path /Projects/<project>/...` reuses a key already in the project); the folder ID is the last part of the folder's URL in Drive.
Share the folder, or its shared drive, with the service account as `Viewer`: `hops datasource info <name>` shows its address as `Service account`, and `--test` reports it.
The Drive API must be enabled in the service account's Google Cloud project; neither `--test` nor the UI checks sharing or the API, the first run does.
Console steps and a troubleshooting table by Google's error message (`403 ... API has not been used`, `404 File not found` = not shared, `invalid_grant: Invalid JWT Signature` = key deleted or rotated) are in [references/google-drive-setup.md](google-drive-setup.md).

```python
sftp = hsfs.storage_connector.SftpConnector(id=None, name="partner_drop", featurestore_id=fs.id,
    host="sftp.example.com", port=22, username="hopsworks", path="/outgoing",
    private_key=open("id_ed25519").read())            # or password="..."; auth_type follows
sftp.save()
drive = hsfs.storage_connector.GoogleDriveConnector(id=None, name="shared_docs", featurestore_id=fs.id,
    key_path="/Projects/<project>/Resources/drive-sa.json",   # upload the key first, as for GCS
    folder_id="1AbCdEfGhIjKlMnOpQrStUvWxYz", folder_name="Handbook")
drive.save()
```

```bash
hops datasource ingest-files <source> --dataset NAME [--prefix P] [--include GLOB]... [--exclude GLOB]... [--mirror-deletions] [--index | --index-name FG] [--max-file-size-mb N] [--copy-workers N] [--transform-script Resources/transform.py [--keep-original]] [--cron "0 0 2 ? * *"] [--name JOB] [--no-run]
hops datasource files-jobs <source>          # the files jobs of this data source: job, dataset, prefix, schedule, mirror, index
hops job history <job>                       # runs; a run that failed on some files is FAILED, the successful copies stay
```

```python
sc = fs.get_data_source("my_s3").storage_connector     # S3, GCS, ADLS, SFTP or Google Drive; others raise ValueError
job = sc.ingest_files(
    dataset="acme_docs_handbook",      # existing (owned) or new
    prefix="handbook/2026/",           # relative to the bucket root, SFTP --path or Drive folder, no leading "/"; "" = everything
    include=["**/*.pdf"],              # globs over the path below the prefix; [] = every file
    exclude=["**/~$*"],
    mirror_deletions=False,            # True: files gone from the source are deleted from the dataset
    index=False,                       # True -> feature group files_<dataset>; or a name
    max_file_size_mb=None,             # None or 0 = no cap; larger files are counted as oversized
    copy_workers=4,                    # files downloaded at the same time, 1-16; 1 = one at a time
    transform_script=None,             # project path of a .py with a HopsFileTransformer, see "Transform files" below
    keep_original=False,               # with a script: also store the file as it is in the source
    schedule=None,                     # Quartz cron in UTC, seconds first; None = run only when started
    resources=None,                    # {"cores": 1, "memory": 2048, "gpus": 0}
    name=None,                         # default "<source>_<dataset>_files"
)                                      # created on the server at once
execution = job.run(await_termination=True)
execution.files_progress               # {"status": "ok|partial|failed", "listed", "copied", "updated", "skipped",
                                       #  "deleted", "failed", "oversized", "bytesCopied", "failedFiles": [...], "auditPath", ...}
job.last_stats                         # the same dict for the latest run, None before the first
job.update(prefix="handbook/2027/", include=["**/*.pdf", "**/*.docx"], mirror_deletions=True, index=True, schedule="0 0 2 ? * *")
job.index_feature_group                # "files_acme_docs_handbook" | None
job.delete(delete_files=False, delete_index=False)   # the job, its runs and state; the dataset and index only when asked
sc.get_files_ingestion_jobs()          # the FILES jobs of this data source in the project
```

Each run lists the prefix (`files_per_page` objects per request), copies new and changed files (size, modification time or ETag differ from the manifest), and skips the rest; a file that fails does not stop the run (unless `fail_on_first_error=True`), the copies that succeeded stay, and the run ends FAILED with the failures in `files_progress["failedFiles"]`.
`copy_workers` files are downloaded at the same time; raise it for many small files, lower it when the source throttles or refuses connections (SFTP and Google Drive open one connection per worker).
The transform script, when there is one, still runs on one file at a time.
`prefix`, `include`, `exclude`, `mirror_deletions`, `index`, `max_file_size_mb`, `copy_workers`, `transform_script`, `keep_original`, `schedule` and `resources` can change with `update(...)`; the data source and the dataset cannot, create another job instead.

**Transform files before they are stored.** `transform_script` names a `.py` file in the project (upload it first, e.g. to `Resources/`) that runs on every downloaded and checksum-verified file before anything of it is in the dataset:

```python
# Resources/transform.py: runs in dlthub-ingestion-pipeline, not locally.
import os
from dlt.destinations.impl.hopsworks_fg import HopsFileTransformer

class Redact(HopsFileTransformer):
    def transform(self, path: str, context: dict) -> str | None:
        if context["file_name"].startswith("~"):
            return None                                  # store nothing of this file
        out = os.path.join(context["work_dir"], os.path.splitext(context["file_name"])[0] + ".txt")
        with open(path, errors="replace") as src, open(out, "w") as dst:
            dst.write(src.read().replace("SECRET", "***"))
        return out                                       # its name becomes the stored name
```

Return a new file written under `context["work_dir"]`, `path` itself (edited in place, or to store it unchanged), or `None`.
`context` also has `relative_path`, `source_url`, `size_bytes`, `modified_at`, `mime_type`, `etag`, `connector`, `dataset`, `job_name`, `execution_id` and `keep_original`.
By default the output replaces the file at the source's path (`handbook/a.docx` becomes `handbook/a.txt`); with `keep_original=True` the original is stored as usual and the output goes to `_transformed/<path>`, and `path` is then the stored original, which must not be modified.
A raising transform fails that file only (`transform failed: ...` in `failedFiles`); a script that does not import fails the run before it lists anything.
Editing the script, changing `transform_script` or removing it processes every file again on the next run, from the stored originals when they are kept and from the source otherwise.
Two source files must not produce the same output path; the second fails with `output path ... is already used by ...`.
`files_progress` counts `transformed` and, for files a transform stored nothing of, `dropped`.
Keep heavy work (PDF parsing, chunking, embedding) in a separate job that reads the index; the script needs its imports installed in the job's environment.

**Mirror semantics.** With `mirror_deletions=True` the dataset equals the prefix after each run: copies whose source file disappeared, or that a new `prefix`, `include`, `exclude` or size cap now leaves out, are deleted from the dataset (and from the index).
Deletions are skipped, and the run reports `deletionsSkipped: true`, when the listing did not complete (`listingComplete: false`), so a source outage never empties the dataset.
Without mirror deletions nothing is ever deleted from the dataset; data never flows back to the source.

**State** lives in `DataSources/<source>/files/<dataset>/` of the project (`/hopsfs/DataSources/...` in a terminal), owned by the job and deleted with it:

- `owner.json` names the job that owns the dataset; `lease.json` is the running execution's lease, so two overlapping runs do not race (the second exits at once).
- `manifest.jsonl` has one row per source file: `file_id`, `source_url`, `relative_path`, `hopsfs_path`, `size_bytes`, `modified_at`, `etag`, `content_hash`, `execution_id`, `copied_at`; with a transform also `original_path`, `output_path`, `source_size_bytes`, `transform_hash`, and `hopsfs_path`, `size_bytes` and `content_hash` describe the output.
- `audit/date=YYYY-MM-DD/<HHMMSS>Z_<executionId>.csv` lists what a run changed (`execution_id, op, kind, path, src_path`; op `copied|updated|deleted|failed|oversized|unsupported|transformed|dropped`), written only by runs that changed something and kept for the cluster's audit retention (7 days by default); read it with DuckDB `read_csv(..., hive_partitioning = true)`.
- `<executionId>_files_stats.json` is the run's result, the same object as `execution.files_progress`.

**Index.** With `index=True` (or a name) the backend creates a Delta feature group `files_<dataset>` version 1 at job creation, keyed by `file_id` (SHA-1 of the source URL) and partitioned by `ingestion_date`, the day a file was first indexed; each run upserts the rows of new and changed files and removes the rows of mirrored-away files.
Turning the index on later indexes every file of the manifest on the next run.
Its columns are listed in **hops-files-search**; `delete(delete_index=True)` removes it only when this job created it.
