"""Tests for ``hops skills``: skill discovery, and the shipped skills themselves."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from hopsworks.cli.main import cli


SKILLS_ROOT = Path(__file__).resolve().parents[2] / "hopsworks" / "skills"


SKILL = """---
name: hops-demo
description: Demonstrates a thing.
---

Body.
"""


def _write(root, relative, body=SKILL):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_list_reads_the_bucketed_layout(tmp_path, monkeypatch):
    _write(tmp_path, "data/hops-demo/SKILL.md")
    monkeypatch.setenv("HOPS_SKILLS_DIR", str(tmp_path))

    result = CliRunner().invoke(cli, ["skills", "list"])

    assert result.exit_code == 0, result.output
    assert "hops-demo" in result.output
    assert "data" in result.output


def test_list_reads_the_flat_layout(tmp_path, monkeypatch):
    # A user's project home holds skills flat, because one level is what a
    # coding agent discovers. Pointed at that directory, the listing has to
    # show what they actually have rather than nothing at all.
    _write(tmp_path, "hops-demo/SKILL.md")
    monkeypatch.setenv("HOPS_SKILLS_DIR", str(tmp_path))

    result = CliRunner().invoke(cli, ["skills", "list", "--json"])

    assert result.exit_code == 0, result.output
    skills = json.loads(result.output)
    assert [s["name"] for s in skills] == ["hops-demo"]
    assert skills[0]["bucket"] == ""


def test_info_prints_a_file_of_the_skill(tmp_path, monkeypatch):
    _write(tmp_path, "data/hops-demo/SKILL.md")
    _write(tmp_path, "data/hops-demo/references/part.md", "The part.\n")
    _write(tmp_path, "data/secret.md", "Outside the skill.\n")
    monkeypatch.setenv("HOPS_SKILLS_DIR", str(tmp_path))

    done = CliRunner().invoke(
        cli, ["skills", "info", "hops-demo", "references/part.md"]
    )
    assert done.exit_code == 0, done.output
    assert "The part." in done.output and "Body." not in done.output

    for wrong in ("references/none.md", "../secret.md"):
        done = CliRunner().invoke(cli, ["skills", "info", "hops-demo", wrong])
        assert done.exit_code != 0
        assert "it has references/part.md" in done.output
        assert "Outside" not in done.output


@pytest.mark.parametrize(
    "skill_md", sorted(SKILLS_ROOT.glob("*/*/SKILL.md")), ids=lambda p: p.parent.name
)
def test_shipped_skill_frontmatter_loads_in_every_agent(skill_md):
    # Agents disagree about how strict a YAML parser to use, and the lenient
    # ones hide breakage. An unquoted value containing ": " is a nested mapping
    # to a strict parser, so a description like "Knowledge skill: the sidebar"
    # loads under claude and fails the whole file under copilot with
    # "mapping values are not allowed in this context".
    #
    # Checked by rule rather than by parsing, because no YAML library is a hard
    # dependency of this package: PyYAML is only present transitively and only
    # on some interpreter versions, so importing it would make this test pass by
    # being skipped on exactly the runs that matter.
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{skill_md} has no frontmatter block"

    fields = {}
    for line in match.group(1).split("\n"):
        field = re.match(r"([A-Za-z][\w-]*): (.*)$", line)
        if not field:
            continue
        key, value = field.group(1), field.group(2).strip()
        fields[key] = value
        quoted = len(value) > 1 and value[0] == value[-1] and value[0] in "\"'"
        assert quoted or ": " not in value, (
            f"{skill_md} field {key!r} is an unquoted value containing ': ', "
            f"which a strict YAML parser reads as a nested mapping. Rephrase it "
            f"or quote the value."
        )

    assert fields.get("name"), f"{skill_md} has no name"
    assert fields.get("description"), f"{skill_md} has no description"
    # The directory name is how every agent addresses the skill.
    assert fields["name"] == skill_md.parent.name, (
        f"{skill_md} declares name={fields['name']!r} but lives in "
        f"{skill_md.parent.name!r}"
    )


def test_every_skill_file_a_playbook_cites_exists():
    cite = re.compile(r"hops skills info (hops-[a-z-]+) (references/[\w./-]+)")
    texts = [
        *(SKILLS_ROOT.parent / "cli" / "templates").glob("*.md"),
        *SKILLS_ROOT.rglob("*.md"),
    ]
    cited = {m.groups() for p in texts for m in cite.finditer(p.read_text("utf-8"))}
    assert cited
    folders = {p.parent.name: p.parent for p in SKILLS_ROOT.glob("*/*/SKILL.md")}
    missing = [(s, f) for s, f in cited if not (folders[s] / f).is_file()]
    assert not missing


def test_info_puts_its_path_header_on_stdout_for_a_pipe(tmp_path, monkeypatch):
    path = _write(tmp_path, "data/hops-demo/SKILL.md")
    monkeypatch.setenv("HOPS_SKILLS_DIR", str(tmp_path))

    done = CliRunner().invoke(cli, ["skills", "info", "hops-demo"])
    assert done.exit_code == 0, done.output
    lines = done.stdout.splitlines()
    assert lines[0].startswith("# hops-demo")
    assert lines[1] == f"# {path}"


def test_playbook_mounts_named_tables_read_in_place_and_previews_crm_tables():
    root = SKILLS_ROOT.parents[2]
    playbooks = [root / ".claude" / "commands" / "hops.md", SKILLS_ROOT.parent / "cli" / "templates" / "hops.md"]
    for p in playbooks:
        text = " ".join(p.read_text("utf-8").split())
        assert "is mounted as an external feature group" in text, p
        assert "A CRM, Google Sheets or REST table cannot be mounted: preview it with `hops datasource preview <source> --table <t>`" in text, p
    skill = " ".join((SKILLS_ROOT / "data" / "hops-data-sources" / "SKILL.md").read_text("utf-8").split())
    assert "These sources cannot be mounted" in skill
    assert "`hops datasource preview` reads a named table's rows here" in skill


def test_playbook_asks_one_question_after_a_connect():
    root = SKILLS_ROOT.parents[2]
    for p in [root / ".claude" / "commands" / "hops.md", SKILLS_ROOT.parent / "cli" / "templates" / "hops.md"]:
        text = " ".join(p.read_text("utf-8").split())
        assert "never a second one for the tables" in text, p
        for option in ("**Preview all tables**", "**Preview one: `<table>`**", "**Tell me what this data is about**"):
            assert option in text, (p, option)


@pytest.mark.parametrize("name", ["hops", "hops-build", "hops-gold", "hops-silver", "hops-bronze"])
def test_playbook_asks_a_follow_up_only_after_the_answer_it_depends_on(name):
    root = SKILLS_ROOT.parents[2]
    for p in [root / ".claude" / "commands" / f"{name}.md", SKILLS_ROOT.parent / "cli" / "templates" / f"{name}.md"]:
        text = " ".join(p.read_text("utf-8").split())
        assert "applies only after some answers of another waits for that answer" in text, p


def test_app_deploy_waits_for_the_users_yes():
    root = SKILLS_ROOT.parents[2]
    for d in [root / ".claude" / "agents", SKILLS_ROOT.parent / "cli" / "templates"]:
        text = " ".join((d / "hops-app-builder.md").read_text("utf-8").split())
        assert "`deploy: no` (the default) stops before the deploy step" in text, d
        assert "only with `deploy: yes`, deploy" in text and "with `deploy: yes` only, `hops app deploy" in text, d
    for d in [root / ".claude" / "commands", SKILLS_ROOT.parent / "cli" / "templates"]:
        text = " ".join((d / "hops.md").read_text("utf-8").split())
        assert "whether to deploy it to Hopsworks, never marked \"(Recommended)\"" in text, d
        assert "spawn **hops-app-builder** with `action: deploy`" in text, d
