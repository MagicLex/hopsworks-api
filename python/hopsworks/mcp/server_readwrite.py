#
#   Copyright 2026 Hopsworks AB
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

"""
Hopsworks MCP Server - READ-WRITE Mode

This server provides FULL READ-WRITE access to Hopsworks Feature Store.
AI agents connected to this server can:
- All READ operations (list, inspect, preview data)
- Create and modify feature groups
- Create and modify feature views
- Create projects and directories
- Execute scripts via Brewer
- Run terminal commands

WARNING: Only use this server when you trust the AI agent to make changes
to your Hopsworks environment.
"""

from fastmcp import FastMCP
from starlette import status
from starlette.responses import Response

from .prompts import ProjectPrompts, SystemPrompts
from .resources.project import ProjectResources
from .tools import (
    AuthTools,
    BrewerTools,
    DatasetTools,
    FeatureGroupTools,
    FeatureViewTools,
    JobTools,
    ProjectTools,
    TerminalTools,
)


# Create a FastMCP server instance with explicit readwrite name
mcp = FastMCP(name="Hopsworks MCP (Read-Write)")

# Initialize all tools in readwrite mode (readonly=False is default)
AuthTools(mcp)
ProjectTools(mcp)
ProjectResources(mcp)
ProjectPrompts(mcp)
SystemPrompts(mcp)
JobTools(mcp)
DatasetTools(mcp)
FeatureGroupTools(mcp)
FeatureViewTools(mcp)
TerminalTools(mcp)
BrewerTools(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(_):
    return Response(status_code=status.HTTP_204_NO_CONTENT)


app = mcp.http_app()
