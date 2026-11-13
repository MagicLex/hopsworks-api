---
description: Hopsworks app piece. Creates, edits, deletes or deploys an app through hops-app-builder.
argument-hint: "[instruction]"
model: haiku
---

You are running `/hops-piece-app` with arguments: `$ARGUMENTS`, reached from `/hops`. The rules, the pieces and the words of `/hops` hold here.

## App

1. `hops app list`. An instruction naming an existing app is an edit; "delete" is a delete;
   otherwise a new app. With several candidates, ask which.
2. **Create.** The description is the instruction; without one, ask what the app should show, what
   a person can do in it, and what it reads (feature groups, feature views, a deployment, a model).
   An app that shows scores, ranks or the chance of an outcome, with no model named, asks how to
   score first (Pieces in `/hops`, step 1). On Kumo, `hops score <name>` runs before the app, which reads
   `<name>_scores` rows of the current run (the `run_id` of the newest `<name>_score_runs` row, as
   the **hops-score** skill reads it) and trains nothing.
   Ask the name, proposing a slug from the description. When the working directory is a GitHub
   repository, ask whether the app is git-backed (a push redeploys it) or kept in HopsFS
   (recommended; no repository needed).
3. **Edit.** Ask what should change, in words, unless the instruction says it. A move between
   Python and Node.js is a new app under the same name: ask the user before
   `hops app delete <name> --yes --force`, then create it.
4. **Delete.** Ask the user to confirm the exact name.
5. Spawn **hops-app-builder** with the Agent tool and this prompt, then print what it returns:

   ```
   action: create | edit | delete
   name: <name>
   description: <the user's words>
   change: <for an edit, the user's words>
   runtime: <node when the request says Node or an npm framework, else python>
   source: <hopsfs | git>
   where: <Hopsworks terminal | external client>
   deploy: no
   ```
6. **Deploy.** Ask with `AskUserQuestion` whether to deploy it to Hopsworks, never marked
   "(Recommended)": the local run is where the app is iterated, and a redeploy in Hopsworks is
   slower. On yes, spawn **hops-app-builder** with `action: deploy`, `name: <name>`, `deploy: yes`.
   An edit is asked again before its deploy.
