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
"""Spark lane: the Delta checkpoint interval on real local Spark with delta-spark.

Not part of the unit suite. Run from the python directory:

    pytest tests_integration/spark

pyspark and delta-spark are required: a missing one fails the run. DELTA_SPARK_JARS, a comma
separated list of jars, replaces the Maven resolution of the delta-spark package offline.
"""

import os
import pathlib
import subprocess
import sys


try:
    import delta  # noqa: F401
    import pyspark  # noqa: F401
except ImportError as err:
    raise RuntimeError(
        "The Spark lane needs pyspark and delta-spark installed (see the dev extra in pyproject.toml)."
    ) from err


def test_spark_paths_set_the_checkpoint_interval():
    """Run delta_engine_spark_checks.py in its own bounded process.

    Spark keeps one JVM per process and fixes its jars when it starts, so a session
    with Delta cannot be started once an earlier test started one without it.
    """
    checks = pathlib.Path(__file__).with_name("delta_engine_spark_checks.py")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(checks)],
        cwd=checks.parents[2],
        env={
            **os.environ,
            "PYSPARK_PYTHON": sys.executable,
            "PYSPARK_DRIVER_PYTHON": sys.executable,
        },
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
