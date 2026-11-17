# Copying a Gmail or Outlook mailbox into a feature group

## Copy a Gmail mailbox into a feature group

A `gmail` data source is a mailbox, read with an OAuth refresh token or a Workspace service account with domain-wide delegation, both with the `gmail.readonly` scope.
Its credentials are write-only; code that calls Gmail itself asks for a short-lived token with `get_access_token()`.
It is not browsed or mounted: a sync job upserts its emails into a Delta feature group named after the mailbox (`jane.doe@gmail.com` → `jane_doe_gmails`), partitioned by the `day` of the UTC receive time (an hourly job adds a file per run, and the first run after midnight compacts the finished day), and writes attachments as files whose paths the `attachments` column lists.

```bash
hops datasource create gmail support_inbox --mailbox support@example.com --auth-type OAUTH --client-id CID --client-secret - --refresh-token - < secrets.txt
hops datasource labels support_inbox    # label ids and names: INBOX, SENT, Label_12 (receipts), ...
hops datasource sync support_inbox --label INBOX --label SENT --start-date 2026-01-01 --schedule hourly
hops datasource sync-jobs support_inbox
```

Each `--label` (SDK `labels=[...]`) adds the messages carrying that label; without any label, every message except spam, trash and drafts is copied.

```python
fs.get_data_source("support_inbox").sync(query="-category:promotions", schedule="daily")
emails = fs.get_feature_group("support_examples", version=1)
```

`INCREMENTAL_TIMESTAMP` (the default) upserts messages received since the previous run, and its first run backfills from `initial_value`; `FULL_LOAD` copies every selected message on every run, then removes the rows and attachments of the messages it did not copy.
The copy is an archive: deletions and label changes in Gmail after a message was copied do not reach it until a full load.
A sync job is a Python job; schedule it `hourly` or `daily`, and monitor it like any job.
To search the emails, use **hops-email-search**.

## Copy an Outlook mailbox into a feature group

An `outlook` data source is a Microsoft 365 or Outlook.com mailbox, read through Microsoft Graph with an app registration in Microsoft Entra ID:
- `OAUTH`: a refresh token the mailbox owner granted with `Mail.Read offline_access`;
- `CLIENT_CREDENTIALS`: a client secret of an app with the `Mail.Read` application permission in the mailbox's tenant, which needs `--tenant-id`.

Its credentials are write-only, and Hopsworks stores the new refresh token Microsoft returns on each use.
It syncs exactly like Gmail, into a feature group with the same columns (`jane.doe@outlook.com` → `jane_doe_outlooks`), so **hops-email-search** works on it too.
It selects folders instead of labels and takes no query:

```bash
hops datasource create outlook support_outlook --mailbox support@contoso.com --auth-type CLIENT_CREDENTIALS --tenant-id contoso.com --client-id APP_ID --client-secret - < secret.txt
hops datasource labels support_outlook    # folder ids and paths: Inbox, Inbox/Projects, Sent Items, ...
hops datasource sync support_outlook --label <inbox folder id> --start-date 2026-01-01 --schedule hourly
```

Without `--label` (SDK `folders=[...]`), every folder but Deleted Items, Junk Email, Drafts and Outbox and the folders below them is copied; a chosen folder's subfolders need choosing too.
A message keeps its key when moved between folders, but its `labels` keep the folder it was in when copied.
