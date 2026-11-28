# Reading a CRM, Google Sheets or REST source from the SDK

For a program that needs the rows. To look at a table, `hops datasource preview` (SKILL.md) is the call.

`StorageConnector.get_data(data_source)` fetches one resource and starts one job per call.
`StorageConnector.get_data_batch(data_sources)` fetches several resources with ONE job. It takes `DataSource` objects (from `get_tables()`, or `DataSource(table="lead", storage_connector=sc)`), never bare names. Each result's `.preview` is a dict, `{"preview": [{"values": [{"value0": <column>, "value1": <value>}, ...]}, ...]}`, with no `value1` for a null.

```python
sc = fs.get_data_source("my_crm_connector").storage_connector
tables = sc.get_tables()                  # CRM: the connector's supported resources

by_name = sc.get_data_batch(tables[:3])   # N resources, ONE schema-fetch job
by_name["contacts"].features              # inferred schema of one resource
by_name["contacts"].preview               # sampled rows

data = sc.get_data(tables[0])             # single resource fallback
```

Both calls block until the fetch finishes and raise `hopsworks.client.exceptions.DataSourceException` with the job logs when it fails; `get_data_batch` reports every failed resource in one exception.
`get_data` raises the same exception for SQL sources, where the read is served inline rather than by a job: a refused read is answered with HTTP 200 and the source's own message, so the exception carries that message.
`DataSourceException` is a subclass of `FeatureStoreException`, so an `except FeatureStoreException` around `fg.save()` catches a failed schema fetch during schema inference.
Results are cached server-side per resource, failed fetches too. Updating or deleting the data source (after fixing a credential, for example) clears its cache, so the next call fetches again; pass `use_cached=False` to force a refetch otherwise.
For REST connectors there is no `get_tables()`; build each entry yourself with `DataSource(table="issues", rest_endpoint=RestEndpointConfig(relative_url="v1/issues"))` so every endpoint carries its own request config.
