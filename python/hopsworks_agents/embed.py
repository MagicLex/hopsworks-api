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
"""Embed text with a Hopsworks embedding deployment, as the caller.

The deployment is named by configuration, never by a key: the project that
serves it, the deployment and the model it serves.

    HOPSABLE_EMBEDDING_PROJECT     the project serving the deployment
    HOPSABLE_EMBEDDING_DEPLOYMENT  the deployment's name
    HOPSABLE_EMBEDDING_MODEL       the model name sent in each request

The call goes through the Istio ingress the way ``deployment.predict`` does,
with the caller's own credentials: in a job or a deployment that is its
serving API key (``Authorization: ApiKey``); outside a cluster it falls back to
the Hopsworks inference route of the serving project. No other key is read.

The request is OpenAI's embeddings body inside KServe's envelope::

    {"instances": [{"model": "<model>", "input": ["text", ...]}]}
    {"predictions": [{"data": [{"embedding": [...]}, ...]}]}

``hops fg embed`` writes groups with these vectors, and the agent ``hops agent
init --search`` writes embeds its queries with :func:`embed_query`, so both
sides use one model.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

from hopsworks_apigen import public


PROJECT_ENV = "HOPSABLE_EMBEDDING_PROJECT"
DEPLOYMENT_ENV = "HOPSABLE_EMBEDDING_DEPLOYMENT"
MODEL_ENV = "HOPSABLE_EMBEDDING_MODEL"

#: Texts per request; the deployment refuses more than 256.
BATCH = 64
#: Seconds to connect and to read each request's answer.
TIMEOUT_S = 120

_routes: dict[tuple[str, str], tuple] = {}


@public
def endpoint(
    project: str | None = None,
    deployment: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str]:
    """The (project, deployment, model) to embed with: the arguments, else the environment.

    Raises:
        ValueError: A value is neither passed nor set in the environment.
    """
    values = (
        project or os.environ.get(PROJECT_ENV),
        deployment or os.environ.get(DEPLOYMENT_ENV),
        model or os.environ.get(MODEL_ENV),
    )
    missing = [
        name
        for name, value in zip(
            (PROJECT_ENV, DEPLOYMENT_ENV, MODEL_ENV), values, strict=True
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "No embedding endpoint is configured: set " + ", ".join(missing) + "."
        )
    return values


@public
def embed_texts(
    texts: list[str],
    *,
    project: str | None = None,
    deployment: str | None = None,
    model: str | None = None,
) -> list[list[float]]:
    """One vector per text, in order, from the configured embedding deployment.

    Texts go in requests of ``BATCH``. An answer with another count of vectors
    raises, so a vector is never paired with the wrong text.

    Raises:
        ValueError: No endpoint is configured, or the answer is not one vector per text.
        hopsworks.client.exceptions.RestAPIError: The deployment refused the call.
    """
    project, deployment, model = endpoint(project, deployment, model)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        batch = list(texts[start : start + BATCH])
        answer = _predict(
            project, deployment, {"instances": [{"model": model, "input": batch}]}
        )
        vectors.extend(_vectors(answer, len(batch)))
    return vectors


@public
def embed_query(
    text: str,
    *,
    project: str | None = None,
    deployment: str | None = None,
    model: str | None = None,
) -> list[float]:
    """The vector of one query text; see :func:`embed_texts`."""
    return embed_texts([text], project=project, deployment=deployment, model=model)[0]


def _vectors(answer: dict, count: int) -> list[list[float]]:
    try:
        data = answer["predictions"][0]["data"]
    except (KeyError, IndexError, TypeError):
        raise ValueError(
            "The embedding deployment answered without predictions[0].data."
        ) from None
    if len(data) != count:
        raise ValueError(
            f"The embedding deployment returned {len(data)} vectors for {count} texts."
        )
    if all("index" in item for item in data):
        data = sorted(data, key=lambda item: item["index"])
    return [list(item["embedding"]) for item in data]


def _predict(project: str, deployment: str, body: dict) -> dict:
    from hopsworks_common import client

    namespace, name, project_id = _route(project, deployment)
    istio = client.istio._get_instance()
    if istio is not None:
        from hsml.core.serving_api import ServingApi

        path = ServingApi()._get_istio_inference_path(
            SimpleNamespace(project_namespace=namespace, name=name)
        )
        sender, with_base = istio, False
    else:
        path = ["project", project_id, "inference", "models", name + ":predict"]
        sender, with_base = client._get_instance(), True
    return sender._send_request(
        "POST",
        path,
        headers={"content-type": "application/json"},
        data=json.dumps(body),
        with_base_path_params=with_base,
        timeout=TIMEOUT_S,
        retry_while_unavailable=False,
    )


def _route(project: str, deployment: str) -> tuple[str, str, int]:
    """The path segment the ingress routes the serving project by, the deployment and the project id, read once per process.

    The segment is the project's backing name, read from the project; the
    deployment is named as configured, never looked up in the caller's project.
    """
    key = (project, deployment)
    if key not in _routes:
        from hopsworks_common import client
        from hopsworks_common.project import Project

        info = client._get_instance()._send_request(
            "GET", ["project", "getProjectInfo", project]
        )
        serving = Project.from_response_json(info)
        _routes[key] = (serving.backing_name, deployment, serving.id)
    return _routes[key]
