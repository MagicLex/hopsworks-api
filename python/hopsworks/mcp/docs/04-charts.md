# Charts Operations

The Hopsworks Charts MCP (`hopsworks-mcp-charts`) provides tools for creating and managing data visualizations. This is a separate MCP server focused on chart creation workflows.

## Server

```bash
# Start the charts MCP server
hopsworks-mcp-charts --host 0.0.0.0 --port 8001
```

**Note:** This server runs separately from the Feature Store MCP. Use both together for full functionality.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Chart Creation Flow                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. AGGREGATION                                              │
│     ├─ Upload Python script (aggregate.py)                   │
│     ├─ Create Hopsworks job and execute                      │
│     ├─ Validate output against JSON schema                   │
│     └─ Optionally retain job for re-run                      │
│                                                              │
│  2. VISUALIZATION                                            │
│     ├─ Generate HTML with Chart.js                           │
│     ├─ Upload to Hopsworks                                   │
│     └─ Register chart in Hopsworks API                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## File Structure

Charts are stored in the Hopsworks project:

```
/Projects/{project_name}/Charts/{chart_id}/
├── aggregate.py    # Python aggregation script
├── data.json       # Output data (validated against schema)
└── chart.html      # Chart.js visualization
```

## Available Tools

### High-Level

| Tool | Description |
|------|-------------|
| `create_chart` | Full pipeline: aggregation → validation → visualization → register |

### Granular

| Tool | Description |
|------|-------------|
| `create_aggregation` | Execute aggregation script, validate output, optionally create job |
| `create_visualization` | Generate HTML chart from existing data.json |

### Read

| Tool | Description |
|------|-------------|
| `list_charts` | List all charts in the project |
| `get_chart` | Get chart metadata by ID |
| `preview_chart_data` | Preview data.json content |

## Tool Reference

### create_chart

Create a complete chart in one operation.

```
Parameters:
  - title: str (required) - Chart title
  - description: str (required) - Chart description
  - aggregation_script: str (required) - Python script that outputs data.json
  - visualization_script: str (required) - Chart.js JavaScript code
  - json_schema: dict (required) - JSON Schema for data validation
  - plugins: "boxplot" | "matrix" | "treemap" | None - Chart.js plugin
  - create_job: bool - Create Hopsworks job (default: True)

Returns: Chart
  - chart_id: str
  - title: str
  - description: str
  - html_path: str
  - data_path: str
  - script_path: str
  - job_id: int | None
```

**Example:**

```python
create_chart(
    title="Monthly Revenue",
    description="Revenue breakdown by product category",
    aggregation_script="""
import json
import hopsworks

project = hopsworks.login()
fs = project.get_feature_store()
fg = fs.get_feature_group("transactions", version=1)

df = fg.read()
result = df.groupby("category")["amount"].sum().to_dict()

with open("data.json", "w") as f:
    json.dump({"labels": list(result.keys()), "values": list(result.values())}, f)
""",
    visualization_script="""
new Chart(document.getElementById('myChart'), {
    type: 'bar',
    data: {
        labels: chartData.labels,
        datasets: [{
            label: 'Revenue',
            data: chartData.values,
            backgroundColor: 'rgba(54, 162, 235, 0.5)'
        }]
    }
});
""",
    json_schema={
        "type": "object",
        "properties": {
            "labels": {"type": "array", "items": {"type": "string"}},
            "values": {"type": "array", "items": {"type": "number"}}
        },
        "required": ["labels", "values"]
    },
    create_job=True
)
```

### create_aggregation

Execute only the aggregation step.

```
Parameters:
  - aggregation_script: str (required) - Python script
  - json_schema: dict (required) - JSON Schema for validation
  - chart_id: str | None - Chart ID (generated if not provided)
  - create_job: bool - Create Hopsworks job (default: True)

Returns: ChartAggregationResult
  - chart_id: str
  - script_path: str
  - data_path: str
  - job_id: int | None
```

**Use case:** When you want to iterate on data processing before creating the visualization.

### create_visualization

Create visualization from existing data.

```
Parameters:
  - chart_id: str (required) - Chart ID from create_aggregation
  - title: str (required) - Chart title
  - description: str (required) - Chart description
  - visualization_script: str (required) - Chart.js code
  - plugins: "boxplot" | "matrix" | "treemap" | None
  - job_id: int | None - Job ID to associate

Returns: ChartVisualizationResult
  - chart_id: str
  - html_path: str
  - title: str
  - description: str
```

### list_charts

List all charts in the current project.

```
Returns: list[dict]
  - Chart metadata from Hopsworks API
```

### get_chart

Get a specific chart by ID.

```
Parameters:
  - chart_id: str (required)

Returns: dict | None
```

### preview_chart_data

Preview the data.json for a chart.

```
Parameters:
  - chart_id: str (required)

Returns: ChartDataPreview
  - chart_id: str
  - data: dict
```

## JSON Schema Validation

All chart data must conform to a JSON Schema (Draft 2020-12). The schema is validated:

1. **At specification time**: Schema itself is validated
2. **After aggregation**: Output data.json is validated against schema

**Example Schema:**

```json
{
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {"type": "string"}
        },
        "datasets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "data": {"type": "array", "items": {"type": "number"}}
                },
                "required": ["label", "data"]
            }
        }
    },
    "required": ["labels", "datasets"]
}
```

## Chart.js Plugins

Supported plugins for specialized chart types:

| Plugin | Import | Use Case |
|--------|--------|----------|
| `boxplot` | chartjs-chart-boxplot | Statistical distributions |
| `matrix` | chartjs-chart-matrix | Heatmaps, correlation matrices |
| `treemap` | chartjs-chart-treemap | Hierarchical data |

Plugins are automatically included in the HTML when specified.

## Hopsworks Job Integration

When `create_job=True`, the aggregation script is registered as a Hopsworks job:

- **Job name**: `chart_{chart_id}`
- **Type**: PYTHON
- **Path**: Points to aggregate.py

This allows:
- Scheduled re-runs
- Manual re-execution from Hopsworks UI
- Integration with Hopsworks pipelines

## Workflow Patterns

### Iterative Development

```
1. create_aggregation(script, schema)     # Test data processing
2. preview_chart_data(chart_id)           # Check output
3. [Iterate on script if needed]
4. create_visualization(chart_id, ...)    # Add visualization
```

### One-Shot Creation

```
1. create_chart(...)    # Everything in one call
```

### Refresh Existing Chart

```
1. get_chart(chart_id)                    # Get existing chart
2. preview_chart_data(chart_id)           # Check current data
3. [Re-run job via Hopsworks UI/API]      # Refresh data
```

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `Invalid JSON schema` | Schema doesn't conform to Draft 2020-12 | Fix schema syntax |
| `Output does not conform to schema` | data.json validation failed | Fix aggregation script output |
| `Aggregation script failed` | Python execution error | Check script logs, fix code |
| `No active chat` | Session not initialized | Ensure proper authentication |

## Naming Convention

All Hopsworks MCP servers follow the pattern:

```
hopsworks-mcp-{capability}
```

| Server | Purpose |
|--------|---------|
| `hopsworks-mcp` | Feature Store (default, readwrite) |
| `hopsworks-mcp-readonly` | Feature Store (read-only) |
| `hopsworks-mcp-readwrite` | Feature Store (explicit readwrite) |
| `hopsworks-mcp-charts` | Chart creation |

This naming:
- Gives agents clear context about what each server does
- Allows router agents to select appropriate servers
- Follows predictable patterns for discovery
