---
description: Hopsworks dashboard piece. Asks the kind (an app or Superset), then creates, edits or deletes a Superset dashboard through hops-dashboard-builder.
argument-hint: "[instruction]"
model: haiku
---

You are running `/hops-piece-dashboard` with arguments: `$ARGUMENTS`, reached from `/hops`. The rules, the pieces and the words of `/hops` hold here.

## Dashboard

`hops superset` and the SDK reach Superset from anywhere, through the Hopsworks proxy, so the
builder runs its program where it runs.

1. A request for a new dashboard ("a dashboard of my orders") first asks the kind, in one
   `AskUserQuestion` question, unless the request names Superset or an app: **An app
   (Recommended)**, a page of charts served by Hopsworks, lighter and quicker to build and change;
   **A Superset dashboard**, charts in Superset over the offline store, heavier; **Explain the
   difference**, which answers in a few lines (an app: any layout, reads feature groups or a
   deployment, iterated in the sandbox before a deploy; Superset: standard BI charts and filters,
   built through its API) and asks again without that option. An app goes to `/hops-piece-app`, the
   request as its description. A Superset dashboard goes to create. Otherwise list the
   dashboards (`hops superset dashboard list`) and ask: edit one (each by title, marked when a program exists under `dashboards/`), create one,
   or delete one. With none, go to create.
2. **Create.** The feature groups are the ones the request names or this conversation just made;
   with neither, `hops fg list` and ask which to chart (multi-select; a group that is online only
   cannot be charted, since Superset reads the offline store). Read their columns with
   `hops fg feature list <name>`, then ask in one `ask_user` call, two questions: which charts to
   keep (multi-select) from a few you propose by name (a big-number total, a bar over the
   categorical column with the fewest distinct values, a histogram of a numeric column, a time
   series on the event time when there is one), and the title, two proposals from the table names.
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
