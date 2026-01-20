# Philosophy

Design principles for the Hopsworks MCP servers.

## Core Principles

### 1. Principle of Least Privilege

AI agents should only have access to the capabilities they need. This is why we provide two server modes:

- **readonly**: Safe exploration mode. Agents can list, inspect, and preview data but cannot create or modify anything. Use this for:
  - Data exploration and discovery
  - Schema understanding
  - Debugging and troubleshooting
  - Untrusted or experimental agents

- **readwrite**: Full access mode. Agents can create feature groups, feature views, and execute scripts. Use this when you explicitly trust the agent to make changes.

### 2. Explicit Over Implicit

Tool names and parameters are explicit and self-documenting:

```
get_feature_groups          → Lists feature groups
get_feature_group_details   → Gets metadata for one
preview_feature_group       → Shows actual data
create_feature_group        → Creates one
```

No ambiguity. An agent reading these names knows exactly what each tool does.

### 3. Stateful vs Stateless Tools

Tools come in two flavors:

- **Stateful** (`_in_current_project`): Operate on the currently active project. Fewer parameters, faster to use.
- **Stateless**: Require explicit project specification. More verbose but work without prior context.

Both are provided because different agent architectures have different needs.

### 4. Granular + High-Level

For complex operations, we provide both:

- **Granular tools**: Fine-grained control over each step
- **High-level tools**: Combine multiple steps into one operation

Example (Charts):
```
create_aggregation    → Just the data processing
create_visualization  → Just the rendering
create_chart          → Full pipeline in one call
```

Agents can choose the level of control they need.

### 5. Validation at Boundaries

We validate inputs at MCP boundaries:
- JSON schemas are validated before execution
- Parameters are type-checked
- Errors are returned with clear messages

Don't trust, verify.

### 6. No Magic

Tools do what they say and nothing more:
- No automatic retries with modified parameters
- No silent fallbacks
- No "helpful" modifications to user input

If something fails, it fails explicitly with a clear error.

## Tool Design Guidelines

### Naming Convention

```
{action}_{resource}[_in_current_project]
```

Actions:
- `get_` / `list_` → Read operations
- `create_` → Create new resources
- `preview_` → Read data samples
- `delete_` → Remove resources (use with caution)

### Parameter Design

Required parameters come first:
```python
async def create_feature_group(
    name: str,           # Required
    version: int,        # Required
    primary_key: list,   # Required
    description: str = None,  # Optional
    online_enabled: bool = False,  # Optional with default
)
```

### Return Types

Always return structured data:
```python
class FeatureGroup(BaseModel):
    id: int
    name: str
    version: int
    description: str | None
```

Never return raw strings for structured data.

### Error Handling

Errors are exceptions with clear messages:
```python
raise RuntimeError(
    f"Feature group {name} v{version} not found."
)
```

Not:
```python
return {"error": "something went wrong"}
```

## Security Considerations

### Dangerous Operations

Some operations are inherently dangerous:
- Terminal access (`start_session`, `add_input`)
- Script execution (`execute`)
- Project deletion

These are **only available in readwrite mode** and are clearly documented as dangerous.

### Authentication

The MCP server inherits authentication from the Hopsworks client:
- API keys
- JWT tokens
- Session credentials

Never expose credentials in tool responses or logs.

### Network Isolation

For production deployments:
- Run readonly servers for exploration workloads
- Run readwrite servers in isolated networks
- Use separate API keys with minimal scopes

## Future Directions

### Router Pattern

When you have 20+ specialized MCPs, don't dump them all into one session. Use a router:

```
User: "Check the server logs and restart the failed pod"

Router Agent:
  → Analyzes request
  → "I need DevOps_MCP and Logs_MCP"
  → Dynamically loads only those two
```

The Hopsworks MCP is designed to work in this pattern as one specialized server among many.

### Observability

Future versions may include:
- Tool usage metrics
- Latency tracking
- Audit logging for compliance

All opt-in and privacy-respecting.
