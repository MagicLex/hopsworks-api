"""The build playbook registers a system only where register can find its code."""

from __future__ import annotations

from pathlib import Path

from hopsworks.cli import scaffold


def test_a_local_repository_is_uploaded_before_it_is_registered():
    text = " ".join(
        (Path(scaffold.__file__).resolve().parent / "templates" / "hops-build.md")
        .read_text(encoding="utf-8")
        .split()
    )
    local = text.index("With `system.repo.url: local` and `<slug>/` outside `/Projects/<project>`")
    upload = text.index("hops files upload <slug>", local)
    register = text.index("register once with `--location", local)
    assert upload < register
    assert "do not register at the start" in text[local:register]
    # A sandbox that mounts the project still keeps the work tree outside it.
    assert "whether or not it mounts the project" in text[local:register]
