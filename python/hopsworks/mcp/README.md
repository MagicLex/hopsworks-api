# Hopsworks Feature Store MCP

Model Context Protocol (MCP) server for the Hopsworks Feature Store. Enables AI agents to interact with feature groups, feature views, datasets, and jobs.

## Quick Start

```bash
# Install
pip install hopsworks[mcp]

# Run (read-write mode)
hopsworks-mcp --host 0.0.0.0 --port 8000

# Run (read-only mode for safer exploration)
hopsworks-mcp-readonly --host 0.0.0.0 --port 8000
```

## Server Modes

| Entry Point | Mode | Description |
|-------------|------|-------------|
| `hopsworks-mcp` | readwrite | Full access (default) |
| `hopsworks-mcp-readonly` | readonly | Read-only access, no mutations |
| `hopsworks-mcp-readwrite` | readwrite | Explicit full access |

Use `--mode readonly` or `--mode readwrite` with the base command for explicit control.

## Available Tools

### Authentication

| Tool | Description |
|------|-------------|
| `login` | Connect to a Hopsworks instance |

### Projects (Read)

| Tool | Description |
|------|-------------|
| `use_project` | Switch to a specific project |
| `list_projects` | List all accessible projects |
| `get_current_project_details` | Get current project info |
| `get_project_details` | Get specific project info |

### Projects (Write)

| Tool | Description |
|------|-------------|
| `create_project` | Create a new project |

### Feature Groups (Read)

| Tool | Description |
|------|-------------|
| `get_feature_groups` | List all feature groups (latest versions) |
| `get_feature_group_versions` | Get all versions of a feature group |
| `get_feature_group_details` | Get feature group metadata |
| `preview_feature_group` | Preview first N rows of data |
| `get_features` | List features in a feature group |

### Feature Groups (Write)

| Tool | Description |
|------|-------------|
| `create_feature_group` | Create a new feature group |

### Feature Views (Read)

| Tool | Description |
|------|-------------|
| `get_feature_views` | List all feature views |
| `get_feature_view_versions` | Get all versions of a feature view |
| `get_feature_view_details` | Get feature view metadata |
| `preview_feature_view` | Preview batch data from feature view |

### Feature Views (Write)

| Tool | Description |
|------|-------------|
| `create_feature_view` | Create a feature view from feature groups |

### Datasets (Read)

| Tool | Description |
|------|-------------|
| `get_datasets` | List datasets in a project |
| `get_datasets_in_current_project` | List datasets in current project |
| `list_files` | List files at a path |
| `list_files_in_current_project` | List files in current project |

### Datasets (Write)

| Tool | Description |
|------|-------------|
| `mkdir` | Create a directory |
| `mkdir_in_current_project` | Create directory in current project |

### Jobs (Read)

| Tool | Description |
|------|-------------|
| `get_jobs` | List jobs in a project |
| `get_jobs_in_current_project` | List jobs in current project |

### Terminal (Write only, readwrite mode)

| Tool | Description |
|------|-------------|
| `start_session` | Start a bash terminal session |
| `add_input` | Send input to terminal |
| `get_output` | Get terminal output |

### Brewer (Write only, readwrite mode)

| Tool | Description |
|------|-------------|
| `execute` | Execute Python script in conda environment |

## Configuration

### CLI Options

```bash
hopsworks-mcp \
  --host 0.0.0.0 \
  --port 8000 \
  --transport http \
  --mode readwrite \
  --hopsworks_host app.hopsworks.ai \
  --project my_project \
  --api_key_value "your_api_key"
```

### Environment Variables

- `HOPSWORKS_HOST` - Hopsworks instance hostname
- `HOPSWORKS_PORT` - Hopsworks port (default: 443)
- `HOPSWORKS_PROJECT` - Default project name
- `HOPSWORKS_API_KEY` - API key for authentication

## Transport Methods

- `http` - HTTP transport (default)
- `sse` - Server-Sent Events
- `streamable-http` - Streamable HTTP
- `stdio` - Standard I/O (for CLI integrations)

## Example: Claude Desktop Integration

```json
{
  "mcpServers": {
    "hopsworks": {
      "command": "hopsworks-mcp-readonly",
      "args": ["--transport", "stdio"],
      "env": {
        "HOPSWORKS_HOST": "app.hopsworks.ai",
        "HOPSWORKS_API_KEY": "your_api_key"
      }
    }
  }
}
```

## Claude Code Integration

For development with Claude Code, run the MCP server locally and connect via HTTP:

```bash
# Terminal 1: Start MCP server
cd hopsworks-api/python
uv sync --extra dev --extra mcp --all-groups

uv run hopsworks-mcp \
  --hopsworks_host app.hopsworks.ai \
  --api_key_value YOUR_API_KEY \
  --project your_project
# Server runs on localhost:8000

# Terminal 2: Add MCP to Claude Code and start
claude mcp add --transport http hopsworks http://localhost:8000/mcp
claude
```

The MCP server must be running before starting Claude Code.

## Documentation

- [Philosophy](./PHILOSOPHY.md) - Design principles
- [Read Operations](./docs/01-read-operations.md) - Exploration and data access
- [Write Operations](./docs/02-write-operations.md) - Creating and modifying resources
- [Server Modes](./docs/03-server-modes.md) - Readonly vs readwrite modes
- [Charts](./docs/04-charts.md) - Chart creation and visualization

## License

Apache License 2.0
