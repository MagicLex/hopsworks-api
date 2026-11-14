# Mounting a feature group from another Hopsworks cluster

## Mount a feature group from another Hopsworks cluster

A `hopsworks` data source reads a project on another Hopsworks cluster.
Its databases are the feature stores the remote project can read, its own `<project>_featurestore` and the ones shared with it, and its tables are their feature groups, named `<name>_<version>`.
External feature groups are read through the remote cluster's Hopsworks Query Service, and DLTHub ingestion copies one into a local feature group.

```python
ds = fs.get_data_source("my_remote_cluster")
table = [t for t in ds.get_tables("sales_featurestore") if t.table == "transactions_1"][0]
ext_fg = fs.create_external_feature_group(
    name="remote_transactions", version=1, data_source=table, primary_key=["id"]
)
ext_fg.save()
df = ext_fg.read()   # Python engine: via the remote Hopsworks Query Service
```

The remote cluster must have its Hopsworks Query Service reachable from this one, through its `feature_query` load balancer.
There is no SQL query and no Trino catalog for this data source type.
