# Server Modes

The Hopsworks MCP server supports two operational modes to enforce the principle of least privilege.

## Overview

| Mode | Entry Point | Tools Available |
|------|-------------|-----------------|
| readonly | `hopsworks-mcp-readonly` | Read operations only |
| readwrite | `hopsworks-mcp-readwrite` | All operations |

## Readonly Mode

### Purpose

Readonly mode is designed for:
- **Safe exploration**: Agents can browse and understand data without risk
- **Untrusted agents**: New or experimental agents that haven't been validated
- **Production monitoring**: Query data without modification risk
- **Compliance**: Audit-friendly access with no mutation capabilities

### Starting Readonly Server

```bash
# Explicit readonly entry point
hopsworks-mcp-readonly --port 8000

# Or via mode flag
hopsworks-mcp --mode readonly --port 8000
```

### Available Tools

| Category | Tools |
|----------|-------|
| Auth | `login` |
| Projects | `use_project`, `list_projects`, `get_current_project_details`, `get_project_details` |
| Feature Groups | `get_feature_groups`, `get_feature_group_versions`, `get_feature_group_details`, `preview_feature_group`, `get_features` |
| Feature Views | `get_feature_views`, `get_feature_view_versions`, `get_feature_view_details`, `preview_feature_view` |
| Datasets | `get_datasets`, `get_datasets_in_current_project`, `list_files`, `list_files_in_current_project` |
| Jobs | `get_jobs`, `get_jobs_in_current_project` |

### Not Available

- `create_project`
- `create_feature_group`
- `create_feature_view`
- `mkdir`, `mkdir_in_current_project`
- Terminal tools (`start_session`, `add_input`, `get_output`)
- Brewer tools (`execute`)

## Readwrite Mode

### Purpose

Readwrite mode is for:
- **Development**: Building and iterating on feature pipelines
- **Automation**: CI/CD pipelines that create resources
- **Trusted agents**: Validated agents with explicit permissions

### Starting Readwrite Server

```bash
# Explicit readwrite entry point
hopsworks-mcp-readwrite --port 8000

# Or via mode flag
hopsworks-mcp --mode readwrite --port 8000

# Default (backwards compatible)
hopsworks-mcp --port 8000
```

### Available Tools

All readonly tools plus:

| Category | Tools |
|----------|-------|
| Projects | `create_project` |
| Feature Groups | `create_feature_group` |
| Feature Views | `create_feature_view` |
| Datasets | `mkdir`, `mkdir_in_current_project` |
| Terminal | `start_session`, `add_input`, `get_output` |
| Brewer | `execute` |

## Implementation Details

### How It Works

Each tool class accepts a `readonly` parameter:

```python
class FeatureGroupTools:
    def __init__(self, mcp, readonly: bool = False):
        # Read tools - always registered
        self.mcp.tool(...)(self.get_feature_groups)

        # Write tools - only in readwrite mode
        if not readonly:
            self.mcp.tool(...)(self.create_feature_group)
```

Server files initialize with the appropriate mode:

```python
# server_readonly.py
FeatureGroupTools(mcp, readonly=True)

# server_readwrite.py
FeatureGroupTools(mcp, readonly=False)
```

### Tool Discovery

Agents discover available tools via the MCP protocol. In readonly mode, write tools simply don't appear in the tool list - there's no runtime error, they just don't exist.

## Deployment Patterns

### Single Server

Simplest deployment - one server, one mode:

```
┌─────────────────┐
│  AI Agent       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ hopsworks-mcp   │
│ (readwrite)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Hopsworks       │
└─────────────────┘
```

### Split Deployment

Separate servers for different access levels:

```
┌─────────────────┐     ┌─────────────────┐
│  Explorer Agent │     │  Builder Agent  │
└────────┬────────┘     └────────┬────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│ hopsworks-mcp   │     │ hopsworks-mcp   │
│ (readonly:8000) │     │ (readwrite:8001)│
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     ▼
           ┌─────────────────┐
           │ Hopsworks       │
           └─────────────────┘
```

### Router Pattern

For multiple specialized MCPs:

```
┌─────────────────┐
│  AI Agent       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Router MCP     │  "What MCPs do I need for this request?"
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    ▼         ▼            ▼
┌───────┐ ┌───────┐ ┌────────────┐
│FS-MCP │ │Charts │ │ Other MCP  │
│(read) │ │ MCP   │ │            │
└───────┘ └───────┘ └────────────┘
```

## Security Considerations

### API Key Scopes

Use minimal API key scopes:
- Readonly mode: `featurestore:read`, `project:read`
- Readwrite mode: `featurestore:*`, `project:*`, `job:*`

### Network Isolation

```
Production Network          Development Network
┌─────────────────┐        ┌─────────────────┐
│ readonly server │        │ readwrite server│
│ (port 8000)     │        │ (port 8001)     │
└─────────────────┘        └─────────────────┘
```

### Audit Logging

Both modes support audit logging. Track:
- Tool invocations
- Parameters (sanitized)
- Results (summarized)
- Timestamps

## Migration Guide

### From Single Mode

If you were using the default server:

```bash
# Old (implicit readwrite)
hopsworks-mcp --port 8000

# New (explicit)
hopsworks-mcp-readwrite --port 8000
```

### Adding Readonly Access

Add a second server for safe exploration:

```bash
# Existing development server
hopsworks-mcp-readwrite --port 8001

# New readonly server for exploration
hopsworks-mcp-readonly --port 8000
```

## FAQ

**Q: Can I switch modes at runtime?**
A: No. Mode is determined at server startup. Run two servers for both modes.

**Q: What happens if an agent tries a write operation in readonly mode?**
A: The tool doesn't exist. The agent will see it's not in the available tools list.

**Q: Is there a "restricted write" mode?**
A: Not currently. Use readonly + separate readwrite server for specific operations.

**Q: How do I know which mode a server is running?**
A: Check the server name in MCP discovery: "Hopsworks MCP (Read-Only)" vs "Hopsworks MCP (Read-Write)"
