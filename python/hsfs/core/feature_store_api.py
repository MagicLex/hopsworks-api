#
#   Copyright 2020 Logical Clocks AB
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

import hsfs.feature_store
import humps
from hopsworks_common import client, util
from hopsworks_common.client import session_cache


_COUNTS = {
    "num_feature_groups",
    "num_training_datasets",
    "num_storage_connectors",
    "num_feature_views",
    "online_featurestore_size",
}


class FeatureStoreApi:
    def _get(self, identifier: int | str) -> hsfs.feature_store.FeatureStore:
        """Get feature store with specific id or name.

        Parameters:
            identifier: id or name of the feature store

        Returns:
            the featurestore metadata
        """
        _client = client._get_instance()
        path_params = ["project", _client._project_id, "featurestores", identifier]
        # The project's own store lives as long as the project; a shared one can be unshared.
        own = identifier == util._append_feature_store_suffix(
            getattr(_client, "_project_backing_name", None) or ""
        )
        store_json = session_cache.get(f"feature_store:{identifier}") if own else None
        if store_json is None:
            store_json = _client._send_request("GET", path_params)
            if own:
                # Counts change with every write; the cached store has none rather than old ones.
                session_cache.put(
                    f"feature_store:{identifier}",
                    {
                        k: v
                        for k, v in store_json.items()
                        if humps.decamelize(k) not in _COUNTS
                    },
                )
        return hsfs.feature_store.FeatureStore.from_response_json(store_json)

    def _get_all(self) -> list[hsfs.feature_store.FeatureStore]:
        """Get every feature store accessible from the current project.

        Includes the project's own feature store and any feature stores
        shared with it, mirroring what the UI lists. The backend has no
        project-wide feature-group endpoint, so callers that want to see
        feature groups across shared stores must union ``get_feature_groups``
        over this list.

        Returns:
            a list of featurestore metadata objects
        """
        _client = client._get_instance()
        path_params = ["project", _client._project_id, "featurestores"]
        return [
            hsfs.feature_store.FeatureStore.from_response_json(fs)
            for fs in _client._send_request("GET", path_params)
        ]
