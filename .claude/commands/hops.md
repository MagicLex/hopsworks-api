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
  up to four questions (a question that applies only after some answers of another waits for that answer, in a call of its own), two to four options each, the recommended one first and marked
  "(Recommended)". Never end a turn with a question in prose.
- Read only the tables the request names. A named table of a source read in place (SQL,
  warehouse, object store) is mounted as an external feature group (**hops-data-sources**, Mount
  a table) and its rows shown with `hops fg preview <fg> --limit 20`: the Preview pane shows that
  feature group. A CRM, Google Sheets or REST table cannot be mounted: preview it with `hops
  datasource preview <source> --table <t>`; several tables of one source are one call with every
  `--table` (a CRM, Google Sheets or REST source then fetches them with one job). A source lists its tables in one `hops datasource
  tables <source>` call; on a source read in place, `hops datasource preview` is only for finding
  the table a request asks for without naming it. Never loop a preview over every table of a
  source: each one is a read, and a source holds tables the user did not ask about.
- Once a source is connected and the request names no table, ask one question with
  `AskUserQuestion`, never a second one for the tables: **Preview all tables** (up to five;
  picking it previews each, as the rule above says, with no table picker after; on a source read
  in place its option text says it adds a feature group per table), **Preview one: `<table>`**
  (one option per likely table, at most two) and **Tell me what this data is about** (a few lines
  in the chat on which tables the source holds, what each holds and how they join, from `hops
  datasource tables` and a `--limit 1` read per table, at most five, for its columns; those reads
  also show in the Preview list).
  A source with more than five tables previews or describes the five its names point to, and
  says which.
- A next-step offer (Explore, Pieces step 6) always has these two options: **Build a data
  pipeline over it** (a feature pipeline, bronze, silver and gold layers, a scheduled job) and
  **Explore it in a notebook** (a notebook over the table). The other one or two options are what
  the result points to: a dashboard over it, a model, or the whole ML system. An app only when
  the user asked for one.
- When the result could carry a model (a table with an outcome column, a feature group with a key
  and an event time), that option is **Predict `<label>`: three tracked models** (the model piece
  with `candidates: 3`, minutes, every candidate a run in the registry). Once `hops model runs`
  shows them, offer once, unmarked, **An ML system predicting `<label>` from `<feature group>`**
  (features, training, batch scoring on a schedule; `<feature group>` is reused) with its build
  time estimated as **hops-reqs** does. Offer each once per table: never again for a table it was
  offered for in this conversation. The ML system is marked "(Recommended)" only when the user
  asked for one. On yes, run `/hops-ml` with that sentence as its arguments.
- Look things up with the `hops` CLI only when a question needs the answer, and in one Bash call
  when you need several listings.
- No secrets in arguments or output. Never delete anything the user has not confirmed by name.
- `where` in a builder's prompt is `Hopsworks terminal` only when `$HOPSFS_USER_HOME_DIR` is set
  (the line above says which; without it, run `test -n "$HOPSFS_USER_HOME_DIR"`). A laptop, a
  CI runner or a Hopsable sandbox is an `external client`.
- Project files (data files, `Resources/`, a user's folder): read and write them as
  the **hops** skill's *Project files* rule (in a Hopsable sandbox, `$HOPS_TEMPLATES/SKILL.md`) says.
- Never read SDK or library source (installed packages, `inspect.getsource`, a grep through
  site-packages) to find a workaround: `hops <noun> --help` and the skills say what Hopsworks
  supports. When neither does what the step needs, say the blocker in one line with the options,
  and stop there.
- An exit check is the command alone, never piped: `| head` hides its exit code. A `| head` comes
  last on its line, never before `&&` or `;`: under `pipefail` the pipe fails once `head` stops
  reading, and the next command never runs. Write long output to a file and read the file.
- A turn may open with an inventory of the project (`[Hopsable: project ... ]`). Trust it: list
  only what a step needs beyond it, and on an empty project go straight to the first write. A
  line cut short (`+N more`, `not listed`, a count alone) answers nothing about a name it does
  not show.
- A next step that holds cluster resources (a deployment, a schedule, an app) is never marked
  "(Recommended)" unless the user asked for it, and never after a baseline model: the user picks it.
- A rule that stops a step stops it. Never get around it with a copy or a rename of the user's
  asset (a feature group, a feature view, a model) under a new name, and never re-run a sweep or a
  training the user did not ask for. Say the blocker and the one path the playbook names.
- A check runs from outside the program that wrote: the online store is
  `hops fg preview <fg> --online --limit 1`, never an SDK call written into a job. A job whose own
  check line failed after its rows were written is reported as failed with the real cause ("rows
  written, check line wrong"), never re-run to hide it.
- Every failure reaches the final message: a failed job, deployment or exit check is named with
  its cause from the logs (`hops job history`, `hops deployment logs`, `hops app logs`). A failed
  or flapping deployment is never presented as served.

## Dispatch on the first word

| First word | Do |
| --- | --- |
| none | Ask "What do you want to do?": **Explore data**, **Make a table and look at it**, **Build or resume an ML system**, **A dashboard or an app** (then ask which); with a system listed above, **Resume <slug>** replaces **Explore data**, first and recommended, and runs `/hops-build <slug>`. Then continue with that choice below. |
| `explore`, or a look or a question ("show", "list", "what is in", "how many") | Explore (below). |
| an instruction to make or change something outside an ML system (a table, features, a model input, a training dataset, a change of a feature group, a model or candidates, scoring, a real-time endpoint for a model, a dashboard, an app, an agent, a notebook, an analytics layer), alone or several together, and not a request for an ML system or an FTI pipeline | Pieces (below), whatever its first word. |
| `dashboard` | Invoke the `hops-piece-dashboard` command with the Skill tool, passing the rest as its arguments, and follow it. |
| `app` | Invoke the `hops-piece-app` command with the Skill tool, passing the rest as its arguments, and follow it. |
| `agent` | Invoke the `hops-piece-agent` command with the Skill tool, passing the rest as its arguments, and follow it. |
| `status` | Status (below). |
| `ml`, or a request for an ML system or an FTI pipeline | Run the interview: invoke the `hops-ml` command with the Skill tool, passing the rest as its arguments, and follow it. |
| `reqs`, `data`, `features`, `train`, `infer`, `verify`, `stop`, `f`, `t`, `i`, with an ML system listed above | Reply with one line: building and running phases is `/hops-build` (for example `/hops-build train`, `/hops-build verify`), and stop. Without a system, `features`, `train` and `infer` are Pieces (below). |

Choosing **Build or resume an ML system** from the menu runs the interview the same way. When the
system's `requirements.status` is already `met`, reply with one line that `/hops-build` continues it.

## Explore

Read-only. Run what the user's words need: `hops fg list`, `hops fv list`, `hops datasource
list`, `hops search <term>`, `hops fg info <name>`, `hops fg feature list <name>`,
`hops fg preview <name> --limit 10`, `hops sql "<query>"` with a `LIMIT` (not for an external feature group: preview it). `hops sql` is Trino
SQL: `date_diff('day', a, b)`, `date_trunc('month', ts)`, `approx_percentile`, a table is
`<feature group>_<version>`, or `"<store>".<feature group>_<version>` when its STORE in `hops fg list` is not the project's own. Summarise in a few lines and make the next-step offer (Rules; over a listing, the offer is about the listed tables as a whole) with
`AskUserQuestion` (a data pipeline over it, a notebook over it, a dashboard or an ML system over
it), the one the user's words point to marked "(Recommended)".

## Pieces

A request outside an ML system names what to make. Each thing to make is a piece, and this table
is the whole dispatch:

| Piece | Needs | Makes | Exit check | Builder |
| --- | --- | --- | --- | --- |
| table | nothing (generated rows), or a table of a data source (mounted, the user keeps it) | feature group `<t>` with rows | `hops fg preview <t> --limit 1` prints a row | **hops-data-builder** |
| features | feature groups with rows (a mounted one counts), or a data source | feature group `<f>` with rows, job `<f>-features` only on a schedule | `hops fg preview <f> --limit 1` prints a row | **hops-features-builder** |
| model | feature view `<v>`, or feature groups with rows and the label | model `<m>` registered on `<v>` | `hops model info <m>` shows `Feature view` `<v> v<n>`; a model the user names is made, whatever its view | **hops-model-builder** |
| inference | model `<m>`, and the feature view the user names or the one its training dataset reads | feature group `<m>_predictions` with rows; job `<m>-score` only on a schedule | `hops fg list` shows a feature group whose name or description names `<m>`, and `hops fg preview <it> --limit 1` prints a row | **hops-infer-builder** |
| deployment | model `<m>:<v>` registered with `feature_view=` on `<v>`, with a training dataset version; every feature group `<v>` reads online | deployment `<d>` serving `<m>:<v>` with the default predictor, no script | `hops deployment info <d>` shows `RUNNING` and model version `<v>`, `hops deployment predict <d>` for a known entity returns a prediction, and `hops deployment info <d> --stable 60` passes | **hops-deploy-builder** |
| model input | feature groups with rows and the key that joins them (the label too, when the request names one) | feature view `<v>` | `hops fv info <v>` shows its feature groups | you, no builder: a view of the project that already reads those feature groups is reused: say so in one line and ask only whether to make another one beside it (reusing it marked "(Recommended)"), unless the request names the new view: then make it. Otherwise `hops fv create` as the **hops-fv** skill says |
| training dataset | feature view `<v>` | a training dataset version of `<v>`, split as the request says | `hops td list <v>` shows the new version | you, no builder: `hops td compute` as the **hops-fv** skill says (it splits by fractions; a split by time goes through the SDK as that skill's time split says) |
| change of a feature group | feature group `<t>` the user has: a column to add, online on, or a refresh on a schedule | the change on `<t>` itself, never a copy | column: `hops fg feature list <t>` shows it; online: `hops fg preview <t> --online --limit 1` prints a row; refresh: `hops job info <t>-refresh` shows the schedule | you, in this order: `hops fg info <t>`; then ask_user to confirm the change by `<t>`'s name, always and before any other step, a connect included; only on yes: for a refresh from a data source that is not connected yet, connect it (Connecting a source), then: A column: `hops fg feature add`, then fill it as `hops skills info hops-fg references/changes.md` says. Online: `hops fg update <t> --version <n> --online`. A refresh: a mounted (external) group reads its source live, so say that and stop; otherwise **hops-features-builder** with `name: <t>`, `existing: yes`, the schedule, and as source the one the request names, else the one `hops fg info <t>` shows (none shown: ask it as an open question). A backfill is that job's first run. |
| dashboard | feature groups with rows | an app `<a>`, or a Superset dashboard `<title>`, as `/hops-piece-dashboard` asks | the App or Dashboard row's check | `/hops-piece-dashboard`: **hops-app-builder** or **hops-dashboard-builder** |
| app | what it reads: feature groups, or a deployment for predictions (never the model file) | app `<a>` serving, a Node.js app when the request says Node or an npm framework | `hops app info <a>` shows `State` `RUNNING`, and `Type` `NODEJS` for a Node.js app | **hops-app-builder** (`/hops-piece-app`) |
| agent | feature groups with rows it answers from, and an LLM with its API key saved as a secret | agent deployment `<a>` with tracing, its chat app `<a>_chat`, and evaluation suite `<a>` with one run | one question the data answers gets an answer whose trace has a tool call, `hops app info <a>_chat` shows `State` `RUNNING`, and the suite's run has a pass rate | **hops-agent-builder** (`/hops-piece-agent`) |
| notebook | what it reads: feature groups or a feature view | notebook `<path>.ipynb` with its outputs | every `hops notebook run` of this turn exited 0 (each prints `ran as In [n]` or the cell's error); a notebook from an earlier turn: `hops notebook info <path>.ipynb` shows a `RUN` number on every code cell | you, no builder (`/hops-piece-notebook`) |
| analytics layer | bronze: a source; silver: feature groups with rows, used as bronze by name, never copied; gold: silver tables | the layer's tables with rows | `hops fg preview <table> --limit 1` for each table its `system.yaml` lists | `/hops-bronze` only for the example bronze of generated data, never when the request names a table or this conversation made one (that table is the bronze: `hops factory run analytics-silver` with it by name), then `/hops-silver`, `/hops-gold` |

1. **The goal.** Write down the pieces the request names, each with its asset name. Add a piece
   one of them needs only when that need's exit check fails: "a model on the deals" with no
   feature view makes the view inside the model piece; "score the deals daily with the best
   model" when a model exists is the inference piece alone. A model whose request names more tables
   than the one holding the label adds a features piece first when one of them holds several rows
   per key of the label table: its primary key is that key plus another column (`customer_id,week`),
   or it has its own key and carries that key as a column (`ticket_id` with `customer_id`). The
   features piece turns each such table into one row per key (counts, averages, last values, a
   trend); the model piece then trains on a view joining the label table to it. A deployment of a model the project lacks runs: the model piece (its builder makes the training dataset), then the deployment piece, whose builder stops on each offline feature group the view reads for the online ask below; `hops model list` shows what models exist. "Serve",
   "deploy" (a model), "real-time", "an endpoint" or "an API for predictions" is the deployment piece; "score daily"
   or "in batch" is inference. "Score", "rank" or "the chance of" an outcome over connected data
   (a CRM, a database, a feature group), with no model named, an app that shows them included, is
   not a piece: ask with `AskUserQuestion` how to score, in this order: **Kumo-Relational
   (Recommended)**, a pretrained model that scores the rows in minutes with nothing trained (an
   external group is ingested first); **Train models**, tracked candidates, slower, for a model to
   deploy or a what-if score on edited values. Kumo runs `hops score` as the **hops-score** skill
   says; train models is the model piece. "An agent", "a chatbot", "an assistant", "a chat over my data" or a
   chat UI to talk to the data is the agent piece, never an app that calls a model itself: the
   agent piece is traced and evaluated.
2. **Run what is missing, in needs order.** For each piece, run its exit check (a change of a feature group runs `hops fg info <t>` first, as its row says; a new dashboard has no title to check yet and goes straight to `/hops-piece-dashboard`), unless the turn's
   inventory shows the piece missing: its line for that kind names every item (no `+N more`, not
   `not listed`, not a count alone), the piece is not among them, and no earlier step of this
   turn made or touched it. Passing: the piece exists; say so in one line and go on. Failing or
   missing: spawn its builder with the Agent tool and its prompt below, adding `touched: yes` when
   an earlier step of this turn made or touched the piece (Hopsable also names earlier sub-agents
   of the turn in the builder's prompt). The builder runs the same exit check
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
6. **Then** make the next-step offer (Rules) with `AskUserQuestion`: a data pipeline over the
   result, a notebook over it, and the next piece, a dashboard over it, or the whole ML system. "(Recommended)" marks only a step the request named and this
   turn did not make; with the request done, no option is marked, so the turn ends on what was
   asked (an Auto mode answer takes a marked option at once).
   A deployment or a schedule the request did not name is offered unmarked; an app is not offered.

Ask only what the request leaves open, in one `AskUserQuestion` call.

Every builder prompt ends with `where: <Hopsworks terminal | external client>`; a question to
the user never does.

When **hops-deploy-builder** stops on an offline feature group that this system made (in this
conversation), run the `hops fg update ... --online` it named and spawn it again, without asking,
its prompt adding `checked: yes` (its step 1 passed but for that group).
A group the user had before: ask once whether to turn it online ("Copies its rows to the online
store; the group stays as it is otherwise"), with no option marked "(Recommended)": it changes
the user's group, so the user picks it. On yes, run the command, then spawn it again. On no, stop:
no copy of the group.

For a model, read the label from the data rather than asking: the column the request names or
implies, or the one outcome-like column of the view (`hops fv info`, `hops fg preview`). Say the
choice in one line ("Predicting `status`, the order outcome") and go on. "Train X", "train a model", "an
experiment (run)" or "compare models" without names, or a model made only to be deployed or scored, is `candidates: 3`; asked while this system
builds a notebook, it is `/hops-piece-notebook` step 5, in that notebook. "Show the experiments" or "the runs"
of a model the project has is a read: `hops model runs <m>`, never a new sweep. "A (training)
pipeline" is the model piece with `job: yes`; "run it" afterwards is `hops job run <m>-train
--wait`, then `hops model runs <m>`. Ask only when two or more
columns fit the request equally.

| Builder | Prompt |
| --- | --- |
| hops-data-builder | `name`, `rows` (10000 unless the user said), `columns` (name and type, one per line), `story` (what the question should find), `source` (`synthetic` or a data source); for a mount of a data source's table, only `name`, `source` and the table |
| hops-features-builder | `name`, `sources` (feature groups or a data source), `features` (name, meaning, type), `grain` (the key and the event time), `schedule` (a Quartz cron: daily `0 0 0 * * ?`, hourly `0 0 * * * ?`; or none), `existing` (`yes` for a refresh of a group the user confirmed: the job is then `<name>-refresh` and `features` is none; else `no`) |
| hops-model-builder | `name`, `feature_view` (name:version, or the feature groups and the label to make one), `label`, `task` (classification, regression), `candidates` (what the user named, else as the model rules above say), `metric`, `job` (`yes` when the user asks for a pipeline, a job or a schedule, else `no`), `training_dataset` (the version a training dataset piece of this turn made, or none) |
| hops-infer-builder | `name`, `model` (name:version, or the best by a metric), `feature_view` (name:version), `population` (the entities to score, such as open deals, and the feature group that lists them), `schedule` |
| hops-deploy-builder | `name` (the deployment, a letter first, then letters and digits), `model` (name:version, or the best by a metric), `feature_view` (name:version), `passed_features` (sent by the caller instead of looked up, or none), `known_entity` (a key to check with, or none), `cores` and `memory` (1 and 1024 unless the user said) |
| hops-agent-builder | the prompt in `/hops-piece-agent` |

## Status

With a system listed above, run `python <slug>/status.py` and `hops factory system plan <slug>`
and print their output verbatim: the plan says what the project already has and what is next. Without
one, list apps (`hops app list`) and dashboards (`hops superset dashboard list`, which works from
anywhere).
