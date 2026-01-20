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
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import hopsworks
import jsonschema
from fastmcp import Context
from hopsworks_common import client

from hopsworks.mcp.models.chart import (
    Chart,
    ChartAggregationResult,
    ChartDataPreview,
    ChartVisualizationResult,
    PluginType,
)


# HTML template for Chart.js visualizations
CHART_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    {plugins}
    <style>
      body {{
        font-family: Arial, sans-serif;
        margin: 0;
        padding: 1em;
        background: #fff;
      }}
      #chart-container {{
        width: 100%;
        max-width: 800px;
        margin: 0 auto;
        min-height: 400px;
        height: 100%;
        display: flex;
        justify-content: center;
      }}
      canvas {{
        background: #fff;
      }}
      #error-message {{
        color: red;
        margin-top: 1em;
        text-align: center;
        font-weight: bold;
        padding: 10px;
        border: 1px solid red;
        border-radius: 5px;
        display: none;
      }}
    </style>
  </head>
  <body>
    <div id="chart-container">
      <canvas id="myChart"></canvas>
      <div id="error-message"></div>
    </div>

    <script>
      async function loadChartData() {{
        const url = "{data_url}";
        const token = localStorage.getItem("token");
        if (!token) {{
          throw new Error("No 'token' found in localStorage.");
        }}
        const response = await fetch(url, {{
          headers: {{ Authorization: token }},
        }});
        if (!response.ok) throw new Error("HTTP " + response.status);
        return await response.json();
      }}

      async function initChart() {{
        try {{
          const chartData = await loadChartData();
          {visualization_script}
        }} catch (error) {{
          console.error("Chart generation error:", error);
          const errorMsg = document.getElementById("error-message");
          errorMsg.style.display = "block";
          errorMsg.textContent = "Failed to generate chart: " + error.message;
        }}
      }}

      initChart();
    </script>
  </body>
</html>
"""

PLUGIN_SCRIPTS = {
    "boxplot": '<script src="https://cdn.jsdelivr.net/npm/@sgratzl/chartjs-chart-boxplot"></script>',
    "matrix": '<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-matrix"></script>',
    "treemap": '<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-treemap"></script>',
}


class ChartsTools:
    """Tools for creating and managing charts in Hopsworks.

    This class provides MCP tools for the full chart creation workflow:
    - Aggregation: Execute Python scripts to generate chart data
    - Visualization: Generate Chart.js HTML from aggregated data
    - Registration: Register charts with Hopsworks API

    All file operations use the Hopsworks Dataset API.
    Job execution uses the Hopsworks Job API.
    No external dependencies (e.g., brewer) required.
    """

    # Base path for charts within a project
    CHARTS_BASE_PATH = "Charts"

    def __init__(self, mcp):
        self.mcp = mcp

        # High-level tool
        self.mcp.tool(tags=["charts", "write"])(self.create_chart)

        # Granular tools
        self.mcp.tool(tags=["charts", "write"])(self.create_aggregation)
        self.mcp.tool(tags=["charts", "write"])(self.create_visualization)

        # Read tools
        self.mcp.tool(tags=["charts", "read"])(self.list_charts)
        self.mcp.tool(tags=["charts", "read"])(self.get_chart)
        self.mcp.tool(tags=["charts", "read"])(self.preview_chart_data)

    def _get_project(self):
        """Get the current Hopsworks project."""
        return hopsworks.get_current_project()

    def _get_chart_path(self, chart_id: str) -> str:
        """Get the base path for a chart within the project."""
        return f"{self.CHARTS_BASE_PATH}/{chart_id}"

    def _get_full_path(self, chart_id: str, filename: str) -> str:
        """Get the full Hopsworks path for a chart file."""
        project = self._get_project()
        return f"/Projects/{project.name}/{self.CHARTS_BASE_PATH}/{chart_id}/{filename}"

    def _get_data_url(self, chart_id: str) -> str:
        """Build the authenticated data URL for the chart."""
        _client = client.get_instance()
        data_path = f"{self.CHARTS_BASE_PATH}/{chart_id}/data.json"
        return f"/hopsworks-api/api/project/{_client._project_id}/dataset/download/with_auth/{data_path}"

    def _get_plugin_html(self, plugin: PluginType | None) -> str:
        """Get the HTML script tag for a Chart.js plugin."""
        if not plugin:
            return ""
        return PLUGIN_SCRIPTS.get(plugin, "")

    def _upload_content(self, content: str, upload_path: str, filename: str) -> str:
        """Upload string content to Hopsworks.

        Creates a temp file, uploads it, and returns the full path.
        """
        project = self._get_project()
        dataset_api = project.get_dataset_api()

        # Ensure the directory exists
        try:
            dataset_api.mkdir(upload_path)
        except Exception:
            pass  # Directory might already exist

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=f"_{filename}", delete=False
        ) as f:
            f.write(content)
            temp_path = f.name

        try:
            uploaded = dataset_api.upload(temp_path, upload_path, overwrite=True)
            return uploaded
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _download_content(self, hopsworks_path: str) -> str:
        """Download file content from Hopsworks."""
        project = self._get_project()
        dataset_api = project.get_dataset_api()

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            temp_path = f.name

        try:
            dataset_api.download(hopsworks_path, temp_path, overwrite=True)
            return Path(temp_path).read_text()
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _register_chart(
        self,
        title: str,
        description: str,
        html_path: str,
        job_id: int | None = None,
    ) -> None:
        """Register a chart with the Hopsworks API."""
        _client = client.get_instance()

        chart_data = {
            "title": title,
            "description": description,
            "url": html_path,
        }
        if job_id:
            chart_data["job"] = {"id": job_id}

        _client._send_request(
            "POST",
            ["project", _client._project_id, "charts"],
            headers={"content-type": "application/json"},
            data=json.dumps(chart_data),
        )

    async def create_aggregation(
        self,
        ctx: Context,
        aggregation_script: str,
        json_schema: dict[str, Any],
        chart_id: str | None = None,
        create_job: bool = True,
    ) -> ChartAggregationResult:
        """Create and execute a chart data aggregation script.

        Uploads a Python script to Hopsworks, creates a job to execute it,
        runs the job, and validates the output against the provided JSON schema.

        Args:
            aggregation_script: Python script that generates data.json output.
                The script should write its output to 'data.json' in the working directory.
            json_schema: JSON Schema (Draft 2020-12) to validate the output data.json.
            chart_id: Optional chart ID. If not provided, a UUID will be generated.
            create_job: Whether to keep the Hopsworks job for re-running (default: True).
                If False, the job is deleted after execution.

        Returns:
            ChartAggregationResult with chart_id, paths, and optional job_id.
        """
        await ctx.info("Starting chart data aggregation...")

        # Generate chart_id if not provided
        if not chart_id:
            chart_id = str(uuid.uuid4())

        # Validate the JSON schema itself
        await ctx.info("Validating JSON schema...")
        try:
            jsonschema.Draft202012Validator.check_schema(json_schema)
        except jsonschema.exceptions.SchemaError as e:
            raise ValueError(f"Invalid JSON schema: {e}") from e

        project = self._get_project()
        chart_path = self._get_chart_path(chart_id)
        script_hopsworks_path = f"{chart_path}/aggregate.py"
        data_hopsworks_path = f"{chart_path}/data.json"

        # Upload aggregation script
        await ctx.info(f"Uploading aggregation script to {script_hopsworks_path}...")
        self._upload_content(aggregation_script, chart_path, "aggregate.py")

        # Create and run the job
        await ctx.info("Creating and running aggregation job...")
        job_api = project.get_job_api()

        job_name = f"chart_agg_{chart_id[:8]}"
        config = job_api.get_configuration("PYTHON")
        config["appPath"] = self._get_full_path(chart_id, "aggregate.py")

        job = job_api.create_job(job_name, config)
        execution = job.run(await_termination=True)

        if not execution.success:
            raise RuntimeError(
                f"Aggregation script failed. Check job logs at: {execution.get_url()}"
            )

        # Validate output against schema
        await ctx.info("Validating output data against schema...")
        try:
            output_content = self._download_content(data_hopsworks_path)
            output_data = json.loads(output_content)
            jsonschema.Draft202012Validator(json_schema).validate(output_data)
        except jsonschema.exceptions.ValidationError as e:
            raise ValueError(f"Output data.json does not conform to schema: {e}") from e
        except FileNotFoundError:
            raise RuntimeError(
                "Aggregation script did not produce data.json. "
                "Ensure the script writes to 'data.json' in the working directory."
            ) from None

        # Handle job retention
        job_id = None
        if create_job:
            job_id = job.id
            await ctx.info(f"Job '{job_name}' retained for re-running (ID: {job_id})")
        else:
            job.delete()
            await ctx.info("Temporary job deleted.")

        await ctx.info("Aggregation complete.")

        return ChartAggregationResult(
            chart_id=chart_id,
            script_path=script_hopsworks_path,
            data_path=data_hopsworks_path,
            job_id=job_id,
        )

    async def create_visualization(
        self,
        ctx: Context,
        chart_id: str,
        title: str,
        description: str,
        visualization_script: str,
        plugins: PluginType | None = None,
        job_id: int | None = None,
    ) -> ChartVisualizationResult:
        """Create a chart visualization from existing aggregated data.

        Generates an HTML file with Chart.js visualization code,
        uploads it to Hopsworks, and registers the chart.

        Args:
            chart_id: The chart ID (from create_aggregation).
            title: Chart title.
            description: Chart description.
            visualization_script: JavaScript code for Chart.js initialization.
                This code receives a 'chartData' variable containing the loaded data.json.
                Example: "new Chart(document.getElementById('myChart'), { type: 'bar', data: chartData });"
            plugins: Optional Chart.js plugin to include (boxplot, matrix, treemap).
            job_id: Optional job ID from aggregation step (for re-running).

        Returns:
            ChartVisualizationResult with chart paths.
        """
        await ctx.info("Creating chart visualization...")

        chart_path = self._get_chart_path(chart_id)
        html_full_path = self._get_full_path(chart_id, "chart.html")

        # Build data URL
        data_url = self._get_data_url(chart_id)

        # Render HTML
        await ctx.info("Rendering chart HTML...")
        html_content = CHART_HTML_TEMPLATE.format(
            title=title,
            plugins=self._get_plugin_html(plugins),
            data_url=data_url,
            visualization_script=visualization_script,
        )

        # Upload HTML
        await ctx.info(f"Uploading chart HTML...")
        self._upload_content(html_content, chart_path, "chart.html")

        # Register chart with Hopsworks
        await ctx.info("Registering chart with Hopsworks...")
        self._register_chart(
            title=title,
            description=description,
            html_path=html_full_path,
            job_id=job_id,
        )

        await ctx.info("Chart visualization complete.")

        return ChartVisualizationResult(
            chart_id=chart_id,
            html_path=html_full_path,
            title=title,
            description=description,
        )

    async def create_chart(
        self,
        ctx: Context,
        title: str,
        description: str,
        aggregation_script: str,
        visualization_script: str,
        json_schema: dict[str, Any],
        plugins: PluginType | None = None,
        create_job: bool = True,
    ) -> Chart:
        """Create a complete chart with data aggregation and visualization.

        This is the high-level tool that combines create_aggregation and
        create_visualization into a single operation.

        Args:
            title: Chart title.
            description: Chart description.
            aggregation_script: Python script that generates data.json output.
            visualization_script: JavaScript code for Chart.js initialization.
            json_schema: JSON Schema to validate the output data.json.
            plugins: Optional Chart.js plugin (boxplot, matrix, treemap).
            create_job: Whether to create a Hopsworks job for re-running (default: True).

        Returns:
            Chart with all paths and metadata.
        """
        await ctx.info("Creating complete chart...")

        # Step 1: Aggregation
        agg_result = await self.create_aggregation(
            ctx=ctx,
            aggregation_script=aggregation_script,
            json_schema=json_schema,
            chart_id=None,
            create_job=create_job,
        )

        # Step 2: Visualization
        viz_result = await self.create_visualization(
            ctx=ctx,
            chart_id=agg_result.chart_id,
            title=title,
            description=description,
            visualization_script=visualization_script,
            plugins=plugins,
            job_id=agg_result.job_id,
        )

        await ctx.info("Chart creation complete.")

        return Chart(
            chart_id=agg_result.chart_id,
            title=viz_result.title,
            description=viz_result.description,
            html_path=viz_result.html_path,
            data_path=agg_result.data_path,
            script_path=agg_result.script_path,
            job_id=agg_result.job_id,
        )

    async def list_charts(self, ctx: Context) -> list[dict[str, Any]]:
        """List all charts in the current project.

        Returns a list of chart metadata from the Hopsworks API.
        """
        await ctx.info("Listing charts...")

        _client = client.get_instance()

        try:
            result = _client._send_request(
                "GET", ["project", _client._project_id, "charts"]
            )
            return result.get("items", []) if result else []
        except Exception as e:
            await ctx.info(f"Failed to list charts: {e}")
            return []

    async def get_chart(self, ctx: Context, chart_id: str) -> dict[str, Any] | None:
        """Get details of a specific chart by ID.

        Args:
            chart_id: The chart ID to retrieve.

        Returns:
            Chart metadata or None if not found.
        """
        await ctx.info(f"Getting chart {chart_id}...")

        charts = await self.list_charts(ctx)
        for chart in charts:
            if chart.get("id") == chart_id or chart_id in chart.get("url", ""):
                return chart
        return None

    async def preview_chart_data(
        self, ctx: Context, chart_id: str
    ) -> ChartDataPreview:
        """Preview the data.json content for a chart.

        Args:
            chart_id: The chart ID to preview data for.

        Returns:
            ChartDataPreview with the chart data.
        """
        await ctx.info(f"Previewing data for chart {chart_id}...")

        data_path = f"{self.CHARTS_BASE_PATH}/{chart_id}/data.json"

        try:
            content = self._download_content(data_path)
            data = json.loads(content)
            return ChartDataPreview(chart_id=chart_id, data=data)
        except Exception as e:
            raise RuntimeError(f"Failed to preview chart data: {e}") from e
