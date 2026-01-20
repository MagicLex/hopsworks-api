# Write Operations

Write operations are **only available in readwrite mode**. These tools allow AI agents to create and modify resources in the Hopsworks Feature Store.

## Server Mode Requirement

To use write operations, start the server in readwrite mode:

```bash
hopsworks-mcp --mode readwrite
# or
hopsworks-mcp-readwrite
```

In readonly mode, these tools are not registered and will not appear in the tool list.

## Projects

### create_project

Create a new Hopsworks project.

```
Parameters:
  - name: str (required) - Project name
  - description: str | None - Project description

Returns: Project
  - name: str
  - id: int
  - owner: str
  - description: str
  - created: datetime
```

**Notes:**
- Project names must be unique within the Hopsworks instance
- The authenticated user becomes the project owner

## Feature Groups

### create_feature_group

Create a new feature group in the current project's feature store.

```
Parameters:
  - name: str (required) - Feature group name
  - version: int (required) - Version number
  - primary_key: list[str] (required) - Primary key columns
  - event_time: str | None - Event time column name
  - description: str | None - Description
  - online_enabled: bool - Enable online storage (default: False)
  - partition_key: list[str] | None - Partitioning columns
  - features: list[dict] | None - Schema definition

Returns: FeatureGroup
```

**Features Schema:**

Each feature in the `features` list should be a dict:
```python
{
    "name": "user_id",
    "type": "int",           # bigint, float, double, string, timestamp, etc.
    "description": "User identifier"  # optional
}
```

**Example:**

```python
create_feature_group(
    name="transactions",
    version=1,
    primary_key=["transaction_id"],
    event_time="timestamp",
    description="User transaction data",
    online_enabled=True,
    features=[
        {"name": "transaction_id", "type": "bigint"},
        {"name": "user_id", "type": "bigint"},
        {"name": "amount", "type": "double"},
        {"name": "timestamp", "type": "timestamp"},
        {"name": "category", "type": "string"}
    ]
)
```

**Notes:**
- If a feature group with the same name and version exists, it will be returned (idempotent)
- The `features` parameter is optional - schema can be inferred from first data insert
- `online_enabled=True` enables real-time serving via the online feature store

## Feature Views

### create_feature_view

Create a feature view from one or more feature groups.

```
Parameters:
  - name: str (required) - Feature view name
  - version: int (required) - Version number
  - feature_group_name: str (required) - Primary feature group
  - feature_group_version: int | None - FG version (latest if not specified)
  - selected_features: list[str] | None - Columns to select (all if not specified)
  - description: str | None - Description
  - labels: list[str] | None - Label columns for ML
  - joins: list[dict] | None - Additional feature groups to join

Returns: FeatureView
```

**Joins Schema:**

Each join in the `joins` list should be a dict:
```python
{
    "feature_group_name": "users",
    "feature_group_version": 1,  # optional, defaults to latest
    "selected_features": ["age", "country"]  # optional, defaults to all
}
```

**Example - Simple Feature View:**

```python
create_feature_view(
    name="transactions_view",
    version=1,
    feature_group_name="transactions",
    feature_group_version=1,
    labels=["fraud_label"]
)
```

**Example - Feature View with Joins:**

```python
create_feature_view(
    name="fraud_detection_view",
    version=1,
    feature_group_name="transactions",
    selected_features=["amount", "category", "fraud_label"],
    labels=["fraud_label"],
    joins=[
        {
            "feature_group_name": "user_profiles",
            "selected_features": ["age", "account_age_days"]
        },
        {
            "feature_group_name": "transaction_aggregates",
            "selected_features": ["avg_amount_7d", "count_7d"]
        }
    ]
)
```

**Notes:**
- Feature views define the schema for training data and inference
- Join keys are automatically inferred from primary keys
- Labels are excluded from feature vectors during inference

## Datasets

### mkdir_in_current_project

Create a directory in the current project.

```
Parameters:
  - path: str (required) - Directory path to create

Returns: str - Success message
```

### mkdir

Create a directory in a specific project.

```
Parameters:
  - project_name: str (required) - Project name
  - path: str (required) - Directory path to create

Returns: str - Success message
```

## Terminal (Dangerous)

These tools provide shell access and are only available in readwrite mode.

### start_session

Start a bash terminal session.

```
Parameters:
  - cwd: str (required) - Working directory

Returns: int - Process ID (pid)
```

### add_input

Send input to an active terminal session.

```
Parameters:
  - pid: int (required) - Process ID
  - addon: str (required) - Input to send (include \n for Enter)

Returns: None
```

### get_output

Get output from a terminal session.

```
Parameters:
  - pid: int (required) - Process ID
  - offset: int - Character offset to start from (default: 0)

Returns: str - Terminal output
```

**Warning:** Terminal access allows arbitrary command execution. Only enable for trusted agents.

## Brewer (Dangerous)

### execute

Execute a Python script in a conda environment.

```
Parameters:
  - chat_id: int (required) - Brewer chat ID
  - script_path: Path (required) - Path to Python script

Returns: ExecutionResult
  - output: str
  - returncode: int
```

**Warning:** Script execution allows arbitrary code execution. Only enable for trusted agents.

## Best Practices

### Idempotent Operations

Use `get_or_create` semantics:
- `create_feature_group` with existing name/version returns the existing FG
- `create_feature_view` with existing name/version returns the existing FV

This makes operations safe to retry.

### Version Management

Always specify versions explicitly:
```python
# Good - explicit version
create_feature_group(name="users", version=1, ...)

# Risky - version might change unexpectedly
create_feature_group(name="users", version=None, ...)
```

### Minimal Permissions

Only enable write operations when needed:
```bash
# For exploration
hopsworks-mcp-readonly

# For development
hopsworks-mcp-readwrite

# Never in production without explicit need
```
