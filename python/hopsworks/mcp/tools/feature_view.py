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

import hopsworks
from fastmcp import Context  # noqa: TC002
from hopsworks.mcp.models.feature_view import FeatureView
from hopsworks.mcp.utils.tags import TAGS


class FeatureViewTools:
    """Tools for managing feature views in Hopsworks MCP."""

    def __init__(self, mcp, readonly: bool = False):
        """Initialize the FeatureViewTools with the MCP server instance.

        Args:
            mcp: The MCP server instance
            readonly: If True, only register read-only tools
        """
        self.mcp = mcp

        # READ tools - always registered
        self.mcp.tool(tags=[TAGS.FEATURE_VIEW, TAGS.READ, TAGS.STATEFUL])(
            self.get_feature_views
        )
        self.mcp.tool(tags=[TAGS.FEATURE_VIEW, TAGS.READ, TAGS.STATEFUL])(
            self.get_feature_view_versions
        )
        self.mcp.tool(tags=[TAGS.FEATURE_VIEW, TAGS.READ, TAGS.STATEFUL])(
            self.get_feature_view_details
        )
        self.mcp.tool(tags=[TAGS.FEATURE_VIEW, TAGS.READ, TAGS.STATEFUL])(
            self.preview_feature_view
        )

        # WRITE tools - only in readwrite mode
        if not readonly:
            self.mcp.tool(tags=[TAGS.FEATURE_VIEW, TAGS.WRITE, TAGS.STATEFUL])(
                self.create_feature_view
            )

    def _get_feature_store(self):
        try:
            project = hopsworks.get_current_project()
        except hopsworks.ProjectException:
            raise RuntimeError(
                "No active Hopsworks project found, use login tool."
            ) from None
        return project.get_feature_store()

    def _get_feature_view(self, name: str, version: int | None = None):
        fs = self._get_feature_store()
        if version is not None:
            return fs.get_feature_view(name=name, version=version)
        # Get latest version
        fvs = fs.get_feature_views(name=name)
        if not fvs:
            raise RuntimeError(f"Feature view {name} not found.")
        return sorted(fvs, key=lambda fv: fv.version, reverse=True)[0]

    async def get_feature_views(self, ctx: Context) -> list[FeatureView]:
        """Get all feature views in the project (latest version of each)."""
        await ctx.info("Retrieving feature views...")

        fs = self._get_feature_store()
        # Get all feature groups to find feature views via their queries
        # The feature store doesn't have a get_all_feature_views method,
        # so we need to list them differently
        try:
            # Try to get all feature views - this API may vary
            from hsfs.core import feature_view_api
            fv_api = feature_view_api.FeatureViewApi(fs.id)
            fvs = fv_api.get_all()
        except Exception:
            # Fallback: return empty if API not available
            return []

        # Group by name, keep latest version
        fv_by_name: dict[str, list] = {}
        for fv in fvs:
            fv_by_name.setdefault(fv.name, []).append(fv)

        result = []
        for name, versions in fv_by_name.items():
            latest = sorted(versions, key=lambda x: x.version, reverse=True)[0]
            result.append(
                FeatureView(
                    id=latest.id,
                    name=latest.name,
                    version=latest.version,
                )
            )
        return sorted(result, key=lambda fv: fv.name)

    async def get_feature_view_versions(self, ctx: Context, name: str) -> list[int]:
        """Get all versions of a feature view with the specified name."""
        await ctx.info(f"Retrieving versions of feature view {name}...")

        fs = self._get_feature_store()
        fvs = fs.get_feature_views(name=name)
        return sorted([fv.version for fv in fvs])

    async def get_feature_view_details(
        self,
        ctx: Context,
        name: str,
        version: int | None = None,
    ) -> FeatureView:
        """Get details of a feature view with the specified name and version (latest by default)."""
        await ctx.info(
            f"Retrieving details of {name}{f' v{version}' if version else ''} feature view..."
        )

        fv = self._get_feature_view(name, version)
        return FeatureView(
            id=fv.id,
            name=fv.name,
            version=fv.version,
            description=fv.description,
            labels=fv.labels,
            inference_helper_columns=fv.inference_helper_columns,
            training_helper_columns=fv.training_helper_columns,
        )

    async def preview_feature_view(
        self,
        ctx: Context,
        name: str,
        version: int | None = None,
        n: int = 10,
    ) -> dict[str, list[str | int | float | None]]:
        """Preview data from a feature view (first n rows, default 10).

        This reads batch data from the feature view's underlying query.
        """
        await ctx.info(
            f"Retrieving preview of {name}{f' v{version}' if version else ''} feature view..."
        )

        fv = self._get_feature_view(name, version)

        # Get batch data (all data, then limit)
        # Note: get_batch_data can be slow for large datasets
        try:
            df = fv.get_batch_data()
            # Limit rows
            if hasattr(df, 'head'):
                preview = df.head(n)
            else:
                preview = df[:n]
        except Exception as e:
            raise RuntimeError(f"Failed to get batch data: {e}") from e

        # Convert to dict format
        try:
            import pandas as pd

            if isinstance(preview, pd.DataFrame):
                return {
                    str(k): [
                        x if isinstance(x, (int, float, type(None))) else str(x)
                        for x in vs
                    ]
                    for k, vs in preview.to_dict(orient="list").items()
                }
        except ImportError:
            pass

        try:
            import polars as pl

            if isinstance(preview, pl.DataFrame):
                return {k: vs.to_list() for k, vs in preview.to_dict().items()}
        except ImportError:
            pass

        raise RuntimeError(
            f"Unable to convert preview to dictionary. Raw preview:\n{preview}"
        )

    async def create_feature_view(
        self,
        ctx: Context,
        name: str,
        version: int,
        feature_group_name: str,
        feature_group_version: int | None = None,
        selected_features: list[str] | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        joins: list[dict] | None = None,
    ) -> FeatureView:
        """Create a new feature view from one or more feature groups.

        Args:
            name: Name of the feature view.
            version: Version number for the feature view.
            feature_group_name: Name of the primary feature group for the query.
            feature_group_version: Version of the primary feature group (latest if not specified).
            selected_features: List of feature names to select from the primary FG (all if not specified).
            description: Description of the feature view (optional).
            labels: List of feature names to use as labels/targets for ML (optional).
            joins: List of feature groups to join. Each dict should have:
                   - feature_group_name (required)
                   - feature_group_version (optional, defaults to latest)
                   - selected_features (optional, defaults to all except join keys)
                   Example: [{"feature_group_name": "users", "selected_features": ["age", "country"]}]
        """
        await ctx.info(f"Creating feature view {name} v{version}...")

        fs = self._get_feature_store()

        # Get the primary feature group
        fg = fs.get_feature_group(name=feature_group_name, version=feature_group_version)

        # Build the query
        if selected_features:
            query = fg.select(selected_features)
        else:
            query = fg.select_all()

        # Add joins if specified
        if joins:
            for join_spec in joins:
                join_fg = fs.get_feature_group(
                    name=join_spec["feature_group_name"],
                    version=join_spec.get("feature_group_version"),
                )
                join_features = join_spec.get("selected_features")
                if join_features:
                    join_query = join_fg.select(join_features)
                else:
                    join_query = join_fg.select_all()
                query = query.join(join_query)

        # Create the feature view
        fv = fs.get_or_create_feature_view(
            name=name,
            version=version,
            query=query,
            description=description or "",
            labels=labels,
        )

        return FeatureView(
            id=fv.id,
            name=fv.name,
            version=fv.version,
            description=fv.description,
            labels=fv.labels,
        )
