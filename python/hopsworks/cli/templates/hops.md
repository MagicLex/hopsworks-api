---
description: Hopsworks menu. Explore data, make a table and look at it, start an ML system, build or edit a Superset dashboard or a Python app with a JavaScript UI, show status.
argument-hint: "[explore | ml | dashboard | app | status] [instruction]"
model: haiku
---

You are running `/hops` with arguments: `$ARGUMENTS`

This command runs on a fast model for menus and questions only. You never write code, a
dashboard program, an app or `system.yaml` here: the building goes to a sub-agent on a stronger
model, and an ML system is built by `/hops-build` after the `/hops-ml` interview.

Already known, no need to look again:
- ML systems in this directory: !`ls -d */system.yaml 2>/dev/null | head -5 || true`
- Where this runs: !`test -n "$HOPSFS_USER_HOME_DIR" && echo "Hopsworks terminal, home $HOPSFS_USER_HOME_DIR" || echo "external client, repository $(pwd)"`

## Rules

- Ask with `AskUserQuestion`, at once and before any lookup the question does not need: one call,
  up to four questions, two to four options each, the recommended one first and marked
  "(Recommended)". Never end a turn with a question in prose.
- Look things up with the `hops` CLI only when a question needs the answer, and in one Bash call
  when you need several listings.
- No secrets in arguments or output. Never delete anything the user has not confirmed by name.
- `where` in a builder's prompt is `Hopsworks terminal` only when `$HOPSFS_USER_HOME_DIR` is set
  (the line above says which; without it, run `test -n "$HOPSFS_USER_HOME_DIR"`). A laptop, a
  CI runner or a Hopsable sandbox is an `external client`.

## Dispatch on the first word

| First word | Do |
| --- | --- |
| none | Ask "What do you want to do?": **Explore data**, **Make a table and look at it**, **Build or resume an ML system**, **A dashboard or an app** (then ask which); with a system listed above, **Resume <slug>** replaces **Explore data**, first and recommended, and runs `/hops-build <slug>`. Then continue with that choice below. |
| `explore` | Explore (below). |
| an instruction to make something outside an ML system (a table, features, a model or candidates, scoring, a real-time endpoint for a model, a dashboard, an app, a notebook, an analytics layer), alone or several together | Pieces (below), whatever its first word. |
| `dashboard` | Dashboard (below), with the rest as the instruction. |
| `app` | App (below), with the rest as the instruction. |
| `status` | Status (below). |
| `ml` | Run the interview: invoke the `hops-ml` command with the Skill tool, passing the rest as its arguments, and follow it. |
| `reqs`, `data`, `features`, `train`, `infer`, `verify`, `stop`, `f`, `t`, `i`, with an ML system listed above | Reply with one line: building and running phases is `/hops-build` (for example `/hops-build train`, `/hops-build verify`), and stop. Without a system, `features`, `train` and `infer` are Pieces (below). |

Choosing **Build or resume an ML system** from the menu runs the interview the same way. When the
system's `requirements.status` is already `met`, reply with one line that `/hops-build` continues it.

## Explore

Read-only. Run what the user's words need: `hops fg list`, `hops fv list`, `hops datasource
list`, `hops search <term>`, `hops fg info <name>`, `hops fg feature list <name>`,
`hops fg preview <name> --limit 10`, `hops sql "<query>"` with a `LIMIT`. `hops sql` is Trino
SQL: `date_diff('day', a, b)`, `date_trunc('month', ts)`, `approx_percentile`, a table is
`<feature group>_<version>`. Summarise in a few lines and offer the next step with
`AskUserQuestion` (look deeper, a dashboard over it, an app over it, an ML system over it), the
one the user's words point to marked "(Recommended)".

## Pieces

A request outside an ML system names what to make. Each thing to make is a piece, and this table
is the whole dispatch:

| Piece | Needs | Makes | Exit check | Builder |
| --- | --- | --- | --- | --- |
| table | nothing, or a data source | feature group `<t>` with rows | `hops fg preview <t> --limit 1` prints a row | **hops-data-builder** |
| features | feature groups with rows, or a data source | feature group `<f>` with rows, job `<f>-features` | `hops fg preview <f> --limit 1` prints a row | **hops-features-builder** |
| model | feature view `<v>`, or feature groups with rows and the label | model `<m>` registered on `<v>` | `hops model info <m>` shows `Feature view` `<v> v<n>`; a model the user names is made, whatever its view | **hops-model-builder** |
| inference | model `<m>`, and the feature view the user names or the one its training dataset reads | feature group `<m>_predictions` with rows; job `<m>-score` only on a schedule | `hops fg list` shows a feature group whose name or description names `<m>`, and `hops fg preview <it> --limit 1` prints a row | **hops-infer-builder** |
| deployment | model `<m>:<v>` registered with `feature_view=` on `<v>`, with a training dataset version; every feature group `<v>` reads online | deployment `<d>` serving `<m>:<v>` with the default predictor, no script | `hops deployment info <d>` shows `RUNNING` and model version `<v>`, and `hops deployment predict <d>` for a known entity returns a prediction | **hops-deploy-builder** |
| dashboard | feature groups with rows | Superset dashboard `<title>` | `hops superset dashboard list` lists `<title>` | **hops-dashboard-builder** (Dashboard below) |
| app | what it reads: feature groups, or a deployment for predictions (never the model file) | app `<a>` serving | `hops app info <a>` shows `State` `RUNNING` | **hops-app-builder** (App below) |
| notebook | what it reads: feature groups or a feature view | notebook `<path>.ipynb` with its outputs | every `hops notebook run` of this turn exited 0 (each prints `ran as In [n]` or the cell's error); a notebook from an earlier turn: `hops notebook info <path>.ipynb` shows a `RUN` number on every code cell | you, no builder (Notebook below) |
| analytics layer | bronze: a source; silver: feature groups with rows, used as bronze by name, never copied; gold: silver tables | the layer's tables with rows | `hops fg preview <table> --limit 1` for each table its `system.yaml` lists | `/hops-bronze`, `/hops-silver`, `/hops-gold` |

1. **The goal.** Write down the pieces the request names, each with its asset name. Add a piece
   one of them needs only when that need's exit check fails: "a model on the deals" with no
   feature view makes the view inside the model piece; "score the deals daily with the best
   model" when a model exists is the inference piece alone. "Serve", "real-time", "an endpoint" or
   "an API for predictions" is the deployment piece; "score daily" or "in batch" is inference.
2. **Run what is missing, in needs order.** For each piece, run its exit check, unless the turn's
   inventory shows the piece missing: its line for that kind names every item (no `+N more`, not
   `not listed`, not a count alone), the piece is not among them, and no earlier step of this
   turn made or touched it. Passing: the piece exists; say so in one line and go on. Failing or
   missing: spawn its builder with the Agent tool and its prompt below, adding `touched: yes` when
   an earlier step of this turn made or touched the piece. The builder runs the same exit check
   last. It counts only when the tool log Hopsable adds under the report (`[Hopsable: the
   sub-agent's last commands ...]`) shows that command, `[exit code 0]` and its passing output;
   otherwise run the check yourself, never on the builder's words. Failing: stop and say which
   check failed and what the builder reported.
3. **A check is a command on the cluster**, run by you or shown by a builder's tool log with its
   output, never a claim without it.
4. **No `system.yaml`**, build lease or `set.py` for pieces. "Make it a system" runs `/hops-ml`,
   which starts from what the checks find.
5. **A question about the data** ("then show where revenue comes from by country"): once the table
   exists, answer from a few `hops sql "<query>"` calls (Trino SQL, as in Explore) over `<t>_1`,
   each with a `GROUP BY` and a `LIMIT`: a short table or a few lines per breakdown. Never a
   dashboard or an app unless the user asked for one by name.
6. **Then** offer the next step with `AskUserQuestion`: the next piece, a dashboard or an app over
   the result, or the whole ML system. "(Recommended)" marks only a step the request named and this
   turn did not make; with the request done, no option is marked, so the turn ends on what was
   asked (an Auto mode answer takes a marked option at once).

Ask only what the request leaves open, in one `AskUserQuestion` call. Every prompt ends with
`where: <Hopsworks terminal | external client>`.

When **hops-deploy-builder** stops on an offline feature group that this system made (in this
conversation), run the `hops fg update ... --online` it named and spawn it again, without asking,
its prompt adding `checked: yes` (its step 1 passed but for that group).
A group the user had before: ask once whether to turn it online ("Copies its rows to the online
store; the group stays as it is otherwise"), with no option marked "(Recommended)": it changes
the user's group, so the user picks it. On yes, run the command, then spawn it again. On no, stop:
no copy of the group.

For a model, read the label from the data rather than asking: the column the request names or
implies, or the one outcome-like column of the view (`hops fv info`, `hops fg preview`). Say the
choice in one line ("Predicting `status`, the order outcome") and go on. "Train X", "an
experiment (run)" or "compare models" without names is `candidates: 3`; asked while this system
builds a notebook, it is Notebook step 5, in that notebook. "Show the experiments" or "the runs"
of a model the project has is a read: `hops model runs <m>`, never a new sweep. "A (training)
pipeline" is the model piece with `job: yes`; "run it" afterwards is `hops job run <m>-train
--wait`, then `hops model runs <m>`. Ask only when two or more
columns fit the request equally.

| Builder | Prompt |
| --- | --- |
| hops-data-builder | `name`, `rows` (10000 unless the user said), `columns` (name and type, one per line), `story` (what the question should find), `source` (`synthetic` or a data source) |
| hops-features-builder | `name`, `sources` (feature groups or a data source), `features` (name, meaning, type), `grain` (the key and the event time), `schedule` (a Quartz cron, or none) |
| hops-model-builder | `name`, `feature_view` (name:version, or the feature groups and the label to make one), `label`, `task` (classification, regression), `candidates` (what the user named, or none), `metric`, `job` (`yes` when the user asks for a pipeline, a job or a schedule, else `no`) |
| hops-infer-builder | `name`, `model` (name:version, or the best by a metric), `feature_view` (name:version), `population` (the entities to score, such as open deals, and the feature group that lists them), `schedule` |
| hops-deploy-builder | `name` (the deployment, a letter first, then letters and digits), `model` (name:version, or the best by a metric), `feature_view` (name:version), `passed_features` (sent by the caller instead of looked up, or none), `known_entity` (a key to check with, or none), `cores` and `memory` (1 and 1024 unless the user said) |

## Status

With a system listed above, run `python <slug>/status.py` and `hops factory system plan <slug>`
and print their output verbatim: the plan says what the project already has and what is next. Without
one, list apps (`hops app list`) and dashboards (`hops superset dashboard list`, which works from
anywhere).

## Dashboard

`hops superset` and the SDK reach Superset from anywhere, through the Hopsworks proxy, so the
builder runs its program where it runs.

1. List the dashboards (`hops superset dashboard list`) and ask: edit one (each by title, marked when a program exists under `dashboards/`), create one,
   or delete one. With none, go to create.
2. **Create.** `hops fg list` and ask which feature groups to chart (multi-select; a group that is
   online only cannot be charted, since Superset reads the offline store). Read their columns with
   `hops fg feature list <name>`, then ask which charts to keep from a few you propose by name: a
   big-number total, a bar over the categorical column with the fewest distinct values, a
   histogram of a numeric column, a time series on the event time when there is one. Ask the title,
   proposing one from the table names.
3. **Edit.** Ask which dashboard and what should change, in words.
4. **Delete.** Ask the user to confirm the exact title.
5. Spawn **hops-dashboard-builder** with the Agent tool and this prompt, then print what it returns:

   ```
   action: create | edit | delete
   title: <title>
   feature_groups: [<name>:<version>, ...]
   charts: <the charts the user kept, one per line, with the columns they use>
   change: <for an edit, the user's words>
   program: <dashboards/<slug>.py, or the ML system's <slug>/dashboards/<slug>.py>
   where: <Hopsworks terminal | external client>
   ```

## Notebook

A notebook is cells run on the user's own Jupyter kernel and saved with their outputs, in an
`.ipynb` that opens in the user's JupyterLab. Write it yourself, with no builder:

1. Run `hops notebook start`: it prints the Python environment the cells run in and the user's
   Jupyter folder (`Users/<username>`). The notebook is `<folder>/<slug>.ipynb`, named after what
   it shows. A package the environment lacks: the `hops-notebook` skill, never `pip install` in a cell.
2. Write one cell at a time and run it before writing the next:
   `hops notebook run <path> --code -` with the cell on stdin. The first cell is
   `import hopsworks` then `fs = hopsworks.login().get_feature_store()`. Each section starts with
   its markdown cell, `hops notebook run <path> --markdown --code "## <what it shows>"`, then one
   code cell per step: the read (`fs.get_feature_group("<t>", version=<n>).read()`), a look at the
   rows, one chart each.
3. A model's predictions: load the model from the registry and score the test part of the training
   dataset it was trained on, with `<n>` from `Training dataset` in `hops model info <m>`:
   `fv.get_training_data(training_dataset_version=<n>, primary_key=True)`, then the
   `split_by_key` call of the training code (**hops-train** section 1, or the pipeline's
   `train.py`), which keeps one entity's rows in one part and gives the same test part. Never
   another split. A model whose training code split another way: show the test metrics its
   `run.json` records instead.
4. A cell that raised, or one the user asks to change: fix it in place with `--cell N`, never with
   a new cell after it. `N` is the number its `hops notebook run` printed (`Cell N of ...`); no
   `hops notebook info` to find it, since `--cell` refuses a cell the user edited.
5. An experiment, candidates, a training run or a model asked for in the notebook: the cells
   run the code and **hops-train** is the contract, its "In a notebook" section (read it with
   `hops skills info hops-train` once, when you reach this step). Every candidate is a run in
   the model registry, the chosen model registered, both checked with `hops model runs <m>` and
   `hops model info <m>` (the check is these commands, not a download). Data in a feature group
   trains on a feature view of it that leaves out the primary key and event time, so the view
   serves model inputs only (`hops fv create <v> --feature-group <fg>:<n> --except
   <key>,<event time> --labels <label>`, then
   `split_by_key` of **hops-train** section 1 in a cell) and registers
   with `feature_view=` and `training_dataset_version=`, so it can be deployed; only data the
   notebook made itself registers without. "Register the model" afterwards is the best run,
   already registered: say its version. Then offer once per notebook, no option marked
   "(Recommended)", to make it a training pipeline: the model piece (Pieces above), on a feature
   group of the notebook's data.
6. A question about the data while this system builds a notebook is answered in it: a cell for the
   table, a cell per chart, never `hops sql` beside it.
7. Never change a cell you did not write unless the user asks: the user may have the notebook open
   in JupyterLab.
8. A model the project already has (a pipeline's, a builder's) in a notebook: load it from the
   registry and plot or score what it holds (step 3, its `run.json` and `images/`); never train it
   again there.

## App

1. `hops app list`. An instruction naming an existing app is an edit; "delete" is a delete;
   otherwise a new app. With several candidates, ask which.
2. **Create.** The description is the instruction; without one, ask what the app should show, what
   a person can do in it, and what it reads (feature groups, feature views, a deployment, a model).
   Ask the name, proposing a slug from the description. When the working directory is a GitHub
   repository, ask whether the app is git-backed (a push redeploys it) or kept in HopsFS
   (recommended; no repository needed).
3. **Edit.** Ask what should change, in words, unless the instruction says it.
4. **Delete.** Ask the user to confirm the exact name.
5. Spawn **hops-app-builder** with the Agent tool and this prompt, then print what it returns:

   ```
   action: create | edit | delete
   name: <name>
   description: <the user's words>
   change: <for an edit, the user's words>
   source: <hopsfs | git>
   where: <Hopsworks terminal | external client>
   ```
