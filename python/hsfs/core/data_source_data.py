#
#   Copyright 2025 Hopsworks AB
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

import warnings
from typing import (
    TYPE_CHECKING,
    Any,
)

import humps
from hopsworks_apigen import public
from hsfs import util


if TYPE_CHECKING:
    from hsfs import feature


@public
class DataSourceData:
    """Metadata object used to provide Data source data information for a feature group."""

    def __init__(
        self,
        limit: int = 50,
        features: list[feature.Feature | dict[str, Any]] | None = None,
        preview: list[dict] | None = None,
        schema_fetch_failed: bool = False,
        schema_fetch_in_progress: bool = False,
        schema_fetch_logs: str = "",
        supported_resources: list[str] | None = None,
        **kwargs,
    ):
        # The backend returns features as raw JSON dicts and from_response_json passes
        # them straight into this constructor.
        # Build Feature objects so the features property matches its annotated type and
        # consumers such as infer_metadata can read feature.name / feature.type rather
        # than failing with AttributeError: 'dict' object has no attribute 'name'.
        # Feature objects passed by direct construction are kept as-is.
        self._limit = limit
        self._features = [
            self._source_feature(f) if isinstance(f, dict) else f
            for f in (features or [])
        ]
        # One short line for the whole source: the per-feature warning is for names a user types.
        message = self._rename_warning(
            [f["name"] for f in features or [] if isinstance(f, dict)]
        )
        if message:
            warnings.warn(message, stacklevel=2)
        self._preview = preview if preview else []
        self._schema_fetch_failed = schema_fetch_failed
        self._schema_fetch_in_progress = schema_fetch_in_progress
        self._schema_fetch_logs = schema_fetch_logs
        self._supported_resources = supported_resources if supported_resources else []

    @staticmethod
    def _rename_warning(names: list[str], examples: int = 3) -> str | None:
        """The warning for source columns whose feature names differ, or None.

        A CRM table renames nearly every column, so the line counts them and shows a
        few. Columns that become the same feature name are named in full: a user must
        rename those at the source.
        """
        renamed = [(n, util._autofix_feature_name(n)) for n in names]
        renamed = [(old, new) for old, new in renamed if old != new]
        if not renamed:
            return None
        by_feature: dict[str, list[str]] = {}
        for n in names:
            by_feature.setdefault(util._autofix_feature_name(n), []).append(n)
        collisions = {f: cols for f, cols in by_feature.items() if len(cols) > 1}
        shown = ", ".join(f"{old} to {new}" for old, new in renamed[:examples])
        more = len(renamed) - examples
        noun = "column is" if len(renamed) == 1 else "columns are"
        message = (
            f"{len(renamed)} source {noun} renamed for the feature store (lower case,"
            f" underscores): {shown}{f', and {more} more' if more > 0 else ''}."
        )
        if collisions:
            message += " These become one feature name: " + "; ".join(
                f"{', '.join(cols)} to {f}" for f, cols in collisions.items()
            ) + "."
        return message

    @staticmethod
    def _source_feature(json_dict: dict[str, Any]):
        """A source column as a Feature, its name sanitized without a warning of its own."""
        from hsfs import feature as feature_mod

        json_decamelized = humps.decamelize(json_dict)
        name = json_decamelized["name"]
        json_decamelized.setdefault("column_name", name)
        result = feature_mod.Feature(
            **{**json_decamelized, "name": util._autofix_feature_name(name)}
        )
        result._original_name = name
        return result

    @classmethod
    def from_response_json(cls, json_dict: dict[str, Any]) -> DataSourceData:
        if json_dict is None:
            return None

        json_decamelized: dict = humps.decamelize(json_dict)

        return cls(**json_decamelized)

    @public
    @property
    def limit(self) -> int:
        """The number of expected preview rows."""
        return self._limit

    @public
    @property
    def features(self) -> list[feature.Feature]:
        """The list of features in the data source."""
        return self._features

    @public
    @property
    def preview(self) -> list[dict]:
        """The preview of the data source."""
        return self._preview

    @public
    @property
    def schema_fetch_failed(self) -> bool:
        """Whether the schema fetch has failed."""
        return self._schema_fetch_failed

    @public
    @property
    def schema_fetch_in_progress(self) -> bool:
        """Whether the schema fetch is in progress."""
        return self._schema_fetch_in_progress

    @public
    @property
    def schema_fetch_logs(self) -> str:
        """The logs of the schema fetch."""
        return self._schema_fetch_logs

    @public
    @property
    def supported_resources(self) -> list[str]:
        """The list of supported resources."""
        return self._supported_resources
