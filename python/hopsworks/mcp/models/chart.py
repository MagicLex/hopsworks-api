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

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChartAggregationResult(BaseModel):
    """Result of chart data aggregation."""

    chart_id: str = Field(description="Unique identifier for the chart")
    script_path: str = Field(description="Path to the aggregation script in Hopsworks")
    data_path: str = Field(description="Path to the generated data.json in Hopsworks")
    job_id: int | None = Field(default=None, description="Hopsworks job ID if created")


class ChartVisualizationResult(BaseModel):
    """Result of chart visualization."""

    chart_id: str = Field(description="Unique identifier for the chart")
    html_path: str = Field(description="Path to the generated chart HTML in Hopsworks")
    title: str = Field(description="Chart title")
    description: str = Field(description="Chart description")


class Chart(BaseModel):
    """Complete chart information."""

    chart_id: str = Field(description="Unique identifier for the chart")
    title: str = Field(description="Chart title")
    description: str = Field(description="Chart description")
    html_path: str = Field(description="Path to the chart HTML file")
    data_path: str = Field(description="Path to the chart data JSON file")
    script_path: str = Field(description="Path to the aggregation script")
    job_id: int | None = Field(
        default=None, description="Hopsworks job ID for re-running aggregation"
    )


class ChartDataPreview(BaseModel):
    """Preview of chart data."""

    chart_id: str
    data: dict[str, Any]


PluginType = Literal["boxplot", "matrix", "treemap"]
