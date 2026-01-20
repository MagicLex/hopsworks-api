# Read Operations

All read operations are available in both `readonly` and `readwrite` server modes. These tools allow AI agents to explore and understand the Hopsworks Feature Store without making any modifications.

## Projects

### list_projects

List all projects accessible by the authenticated user.

```
Returns: Projects
  - projects: list[Project]
    - name: str
    - id: int
    - owner: str
    - description: str | None
    - created: datetime
```

### get_project_details

Get details of a specific project by name.

```
Parameters:
  - name: str (required) - Project name

Returns: Project
```

### get_current_project_details

Get details of the currently active project. Requires prior `login` or `use_project` call.

```
Returns: Project
```

### use_project

Switch to a different project context. All subsequent stateful operations will use this project.

```
Parameters:
  - name: str (required) - Project name to switch to

Returns: Project
```

## Feature Groups

### get_feature_groups

List the latest version of all feature groups in the current project.

```
Returns: list[FeatureGroup]
  - id: int
  - name: str
  - version: int
```

### get_feature_group_versions

Get all versions of a specific feature group.

```
Parameters:
  - name: str (required) - Feature group name

Returns: list[int] - Version numbers
```

### get_feature_group_details

Get detailed metadata for a feature group.

```
Parameters:
  - name: str (required)
  - version: int | None - Defaults to latest

Returns: FeatureGroup
  - id: int
  - name: str
  - version: int
  - description: str | None
  - location: str | None
  - event_time: str | None
  - online_enabled: bool
  - topic_name: str | None
  - deprecated: bool
```

### get_features

List all features (columns) in a feature group.

```
Parameters:
  - name: str (required)
  - version: int | None - Defaults to latest

Returns: list[Feature]
  - name: str
  - type: str
  - description: str | None
  - primary: bool
  - event_time: bool
```

### preview_feature_group

Preview actual data from a feature group. Returns first N rows.

```
Parameters:
  - name: str (required)
  - version: int | None - Defaults to latest
  - n: int - Number of rows (default: 10)

Returns: dict[str, list] - Column name → values
```

## Feature Views

### get_feature_views

List the latest version of all feature views in the current project.

```
Returns: list[FeatureView]
  - id: int
  - name: str
  - version: int
```

### get_feature_view_versions

Get all versions of a specific feature view.

```
Parameters:
  - name: str (required)

Returns: list[int] - Version numbers
```

### get_feature_view_details

Get detailed metadata for a feature view.

```
Parameters:
  - name: str (required)
  - version: int | None - Defaults to latest

Returns: FeatureView
  - id: int
  - name: str
  - version: int
  - description: str | None
  - labels: list[str] | None
  - inference_helper_columns: list[str] | None
  - training_helper_columns: list[str] | None
```

### preview_feature_view

Preview batch data from a feature view.

```
Parameters:
  - name: str (required)
  - version: int | None - Defaults to latest
  - n: int - Number of rows (default: 10)

Returns: dict[str, list] - Column name → values
```

## Datasets

### get_datasets_in_current_project

List datasets in the current project.

```
Parameters:
  - offset: int - Pagination offset (default: 0)
  - limit: int - Max results (default: 100)

Returns: Datasets
  - datasets: list[Dataset]
    - id: int
    - name: str
    - description: str | None
    - datasetType: str
  - total: int
  - offset: int
  - limit: int
```

### get_datasets

List datasets in a specific project.

```
Parameters:
  - project_name: str (required)
  - offset: int (default: 0)
  - limit: int (default: 100)

Returns: Datasets
```

### list_files_in_current_project

List files at a path in the current project.

```
Parameters:
  - path: str (required) - Directory path
  - offset: int (default: 0)
  - limit: int (default: 100)

Returns: Files
  - files: list[File]
    - name: str
    - is_directory: bool
    - owner: str
    - path: str
    - permission: str
    - last_modified: datetime
  - total: int
```

### list_files

List files at a path in a specific project.

```
Parameters:
  - project_name: str (required)
  - path: str (required)
  - offset: int (default: 0)
  - limit: int (default: 100)

Returns: Files
```

## Jobs

### get_jobs_in_current_project

List jobs in the current project.

```
Returns: Jobs
  - jobs: list[Job]
    - id: int
    - name: str
    - job_type: str
    - creation_time: datetime
    - creator: str
  - total: int
```

### get_jobs

List jobs in a specific project.

```
Parameters:
  - project_name: str (required)

Returns: Jobs
```

## Usage Patterns

### Exploration Flow

Typical pattern for exploring a new project:

```
1. login(host, api_key_value, project)
2. get_feature_groups()           → See what's available
3. get_feature_group_details(name) → Understand schema
4. preview_feature_group(name)     → See actual data
5. get_feature_views()             → See derived views
```

### Cross-Project Comparison

Compare feature groups across projects:

```
1. login(host, api_key)
2. list_projects()
3. For each project:
   - get_feature_groups() via stateless tools
   - Compare schemas
```
