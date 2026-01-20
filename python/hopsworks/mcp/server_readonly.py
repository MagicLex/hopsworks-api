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
Hopsworks MCP Server - READ-ONLY Mode

This server provides READ-ONLY access to Hopsworks Feature Store.
AI agents connected to this server can:
- List and inspect projects, feature groups, feature views, datasets, jobs
- Preview data from feature groups and feature views
- Navigate the file system

AI agents CANNOT:
- Create or modify feature groups
- Create or modify feature views
- Create projects or directories
- Execute scripts or terminal commands

Use this server when you want to give an AI agent exploration/read access
without the ability to modify any data.
"""

from fastmcp import FastMCP
from starlette import status
from starlette.responses import Response

from .prompts import ProjectPrompts, SystemPrompts
from .resources.project import ProjectResources
from .tools import (
    AuthTools,
    DatasetTools,
    FeatureGroupTools,
    FeatureViewTools,
    JobTools,
    ProjectTools,
)


# Create a FastMCP server instance with explicit readonly name
mcp = FastMCP(name="Hopsworks MCP (Read-Only)")

# Initialize tools in readonly mode
AuthTools(mcp, readonly=True)
ProjectTools(mcp, readonly=True)
ProjectResources(mcp)
ProjectPrompts(mcp)
SystemPrompts(mcp)
JobTools(mcp, readonly=True)
DatasetTools(mcp, readonly=True)
FeatureGroupTools(mcp, readonly=True)
FeatureViewTools(mcp, readonly=True)
# Note: TerminalTools and BrewerTools are NOT included in readonly mode


@mcp.custom_route("/health", methods=["GET"])
async def health(_):
    return Response(status_code=status.HTTP_204_NO_CONTENT)


app = mcp.http_app()
