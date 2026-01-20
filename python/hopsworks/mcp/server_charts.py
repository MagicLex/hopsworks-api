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
Hopsworks MCP Server - Charts

This server provides tools for creating and managing data visualizations.
Separate from the Feature Store MCP to maintain focused, single-purpose servers.

Available tools:
- create_chart: High-level tool to create a complete chart (aggregation + visualization)
- create_aggregation: Create and execute a data aggregation script
- create_visualization: Create chart HTML from aggregated data
- list_charts: List all charts in the project
- get_chart: Get details of a specific chart
- preview_chart_data: Preview the data.json for a chart
"""

from fastmcp import FastMCP
from starlette import status
from starlette.responses import Response

from .tools.auth import AuthTools
from .tools.charts import ChartsTools


# Create a FastMCP server instance for charts
mcp = FastMCP(name="Hopsworks MCP (Charts)")

# Auth is required to connect to Hopsworks
AuthTools(mcp)

# Charts tools
ChartsTools(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(_):
    return Response(status_code=status.HTTP_204_NO_CONTENT)


app = mcp.http_app()
