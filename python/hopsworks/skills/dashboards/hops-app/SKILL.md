---
name: hops-app
description: Use when writing Streamlit, Flask, Gradio, Node.js or custom apps
  for Hopsworks, deploying from HopsFS or Git, migrating legacy apps off
  `APP_BASE_URL_PATH`, or managing app routing/readiness, monitoring, and
  public sharing. Auto-invoke when user wants to create a dashboard, deploy a
  Python or Node.js app to Hopsworks, configure app routing/readiness, or
  access the feature store from an app. Input an app source + env + memory;
  output a running app and its URL.
---

# Hopsworks apps

Hopsworks runs web apps as managed apps backed by a Hopsworks job, sourced from
HopsFS or a Git repository. The app type (`app_kind`) decides how it starts:

| Kind | App file | Started as | Port |
|---|---|---|---|
| `STREAMLIT` | `.py` | `streamlit run <file>` | 8501, fixed |
| `FLASK` (5.2+) | `.py` with an `app` object | `flask --app <file> run --host 0.0.0.0 --port $APP_PORT` | `app_port`, default 8080 |
| `GRADIO` (5.2+) | `.py` that calls `demo.launch()` | `python <file>`; `GRADIO_SERVER_NAME`/`GRADIO_SERVER_PORT`/`GRADIO_ROOT_PATH` are set | `app_port`, default 8080 |
| `NODEJS` (5.2+) | `.js`, `.mjs`, `.cjs`, `.ts`, `.mts` | `node <file>`; `PORT` and `HOST` are set | `app_port`, default 8080 |
| `CUSTOM` (default) | optional | your `entrypoint_command` | `app_port`, default 8080 |

Every kind but `CUSTOM` is **launched from its app file** with a command Hopsworks
generates: no `entrypoint_command`, and a git-backed app gives the file as
`entrypoint_script` relative to the repository root. A Streamlit app is a
**UI / consumer of the FTI pipelines**, not a pipeline: it reads features and
predictions written by the feature, training, and inference pipelines and presents
them, or embeds a downloaded model to predict locally. A custom app is any other
web service (FastAPI, Django, any WSGI/ASGI server) that binds to `0.0.0.0` and the
injected `$APP_PORT`. Node.js apps get their dependencies from the npm packages
installed in the environment (see [hops-environments](../../platform/hops-environments/SKILL.md),
*npm packages*): nothing is `npm install`ed at app start. `hops app deploy`
installs package.json `dependencies`, at package-lock.json's versions, into
`<name>-app-env`, and never uploads `node_modules`. `<name>-app-env` is a clone of
`python-agent-pipeline`, an agent environment: it runs apps, and Hopsworks
refuses a job on it (error 300021).

`hops app deploy` puts the app in `Resources/apps/<name>/`, so any member of the
project can start it, and records a hash of the folder in the app; a start runs
a copy of the folder only when it matches, so a file someone else writes there
fails the next start until a redeploy. An app runs as whoever starts it, like a
job. It reads its folder once at start: keep data in the project's datasets or
the feature store, never in the app's folder.

## Contract

- **Input:** an app (Streamlit, Flask, Gradio, Node.js or custom) + environment + memory.
- **Output:** a running app and its URL.
- **Pre-condition:** the app source is available in HopsFS or Git (project-relative path for the SDK, HopsFS-absolute for the CLI).

## Smoke-test (cheap pre/post-flight)

```bash
hops app list                 # apps + state; verify RUNNING/serving after
hops app info <name>          # detail (id, type, source, monitoring state and routes, public link on/off)
hops app url <name>           # the app URL
hops app start <name> / stop <name>
hops app logs <name>          # tail logs (a running app is directed to the live logs in the UI)
hops app delete <name> --yes  # non-interactive
```

`hops app create <name> --path /Projects/<project>/Users/<user>/app.py --entrypoint-command "python app.py" --start`
creates, starts, and waits for serving in one call. It takes a **HopsFS-absolute**
path, while the SDK `create_app(app_path=...)` takes a **project-relative** one
(`Users/<username>/app.py`); each surface rejects the other's form. Routing and
readiness are `--app-base-path` and `--readiness-probe-path`. `logs` / `redeploy`
may be absent on older deployed `hops` binaries.

## Ask the user (only when state is ambiguous)

- Does the app need **custom libraries** not in `python-app-pipeline`? If so, clone the env and install `app-requirements.txt` ([references/libraries.md](references/libraries.md)).
- Does the app come from **HopsFS or Git**? If Git, ask for `git_url`, `git_provider`, and (if needed) `git_branch` plus the entrypoint.
- Should a git-backed app **auto-redeploy** on new commits (`git_auto_redeploy=True`)? It only applies to git-backed apps.
- What **memory / cores** should the app get? Unset = the cluster default (`pythonapp_memory`/`pythonapp_cores`).
- Does the app still depend on `APP_BASE_URL_PATH`? Then it is a **migration** task (see **Routing and readiness**); confirm whether the code can switch to root routing.
- Does the app need **monitoring** narrowed to specific routes? Monitoring is on by default and routes are optional.
- Does the app need **public access**? Public sharing of any app kind is feature-flagged; only ask for it when the platform has it enabled.
- Does the app keep **its own state** (sessions, notes, agent memory)? Database access is on by default (`db_access=True`): the project's online feature store database is created at start and `MYSQL_*` variables are injected. The app connects as its own database user, reads the project database, and writes only the tables it creates with `app.create_table()` (Hopsworks runs the DDL and keeps them on disk), which are dropped with the app; whoever starts it, it can do the same. Pass `db_access=False` for an app that needs no database. Details in **hops-app-db**.
- **Before deleting** — `app.delete()` / `hops app delete --yes` tears down the app irreversibly; confirm the exact name with the user, and never tear down an app you created as a side effect (temp or test ones included) unless they asked.

## Routing and readiness

The browser URL is always the proxy mount point
`/hopsworks-api/pythonapp/{projectName}/{appName}/`. Hopsworks owns that prefix
and forwards requests to the app container, so **write new apps as if they run
at `/`** and do not read `APP_BASE_URL_PATH`.

- The proxy forwards the browser mount, rewrites some HTML/CSS links, and preserves redirects. It does **not** rewrite arbitrary JavaScript string literals or client-side `fetch("/api/...")` calls; build API and asset URLs from the app mount, or derive the base path from `X-Forwarded-Prefix` / framework support (Next.js: a mount-aware `basePath` / `assetPrefix`).
- Readiness is separate from routing: Streamlit probes `/_stcore/health`, every other kind probes `/`, and `readinessProbePath` overrides the probe path (`/health` for a Flask or Node.js app that does not serve `/`).

**Legacy apps** (created before the `App base path` migration, or still reading
`APP_BASE_URL_PATH`) can stay on `Compatibility prefix` only as a bridge. To
migrate: make route decorators use `/` (or the app's own subpath) instead of
`APP_BASE_URL_PATH` and build browser URLs from the mount; in the app settings
dialog set `Proxy routing mode` to `Root routing` and `App base path` to the
public mount you want (`/` or `/myapp`); keep the readiness path separate (custom
apps `/` or `/health`, Streamlit `/_stcore/health` or `/<base>/_stcore/health`);
then delete any code that still builds absolute links from `APP_BASE_URL_PATH`.

## References

Read the one a step needs, with `hops skills info hops-app references/<file>` (or from this
skill's `references/` directory):

- [references/hops-app-built.md](references/hops-app-built.md): the app `/hops app` and
  **hops-app-builder** make from [references/app_skeleton/](references/app_skeleton/), and
  `hops app deploy`.
- [references/data.md](references/data.md): reading feature groups (Trino by default), a model or
  a deployment from the app, and Trino from a JavaScript app.
- [references/look.md](references/look.md): the hops-design tokens and components in the
  skeleton's `app.css`, and the look check.
- [references/frameworks.md](references/frameworks.md): writing a Streamlit, Flask, Gradio,
  Node.js or custom app by hand, the Streamlit theme, and Streamlit caching.
- [references/libraries.md](references/libraries.md): a library the environment lacks, or a
  pickled model.
- [references/git.md](references/git.md): an app whose source is a git repository.

## A chat app for an agent (5.3+)

For a chat page in front of an **agent deployment**, do not write one: every agent can have a ready-made chat app, and it already handles what a hand-written one gets wrong (who is chatting, keeping each person's conversations apart, no serving key in the browser, the agent's questions, feedback, attachments, waking an agent that scales to zero).

```python
agent = project.get_agent_serving().get_agent("support")
app = agent.create_chat_app()  # app "support_chat", started; or deploy_agent(..., chat_app=True)
```

Members open it with their Hopsworks login; `app.make_public()` shares it with anyone holding the link. Branding without code: the app's `CHAT_TITLE`, `CHAT_WELCOME`, `CHAT_ACCENT_COLOR`, `CHAT_LOGO_URL` and `CHAT_THEME_DIR` variables (details in **hops-agent-deployment**).

To change its page, layout or server, **customize** it rather than starting over:

```python
app = agent.customize_chat_app()  # runs from Resources/chat-apps/support_chat
```

Gradio: call `demo.launch()` with no `server_port` or `root_path`; Hopsworks sets
`GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT` and `GRADIO_ROOT_PATH`, which `launch()`
reads, and a hardcoded port breaks the readiness probe.

Node.js: listen on `process.env.PORT`; `import` resolves the environment's npm
packages (install them first, e.g. `env.install_npm(["express@latest"])`).

```javascript
// Users/<username>/server.mjs  — app_kind="NODEJS"
import express from "express";
const app = express();
app.get("/health", (req, res) => res.json({ status: "ok" }));
app.get("/", (req, res) => res.json({ status: "ready" }));
app.listen(process.env.PORT, process.env.HOST ?? "0.0.0.0");
```

### Custom app

Bind to `0.0.0.0` and the injected `APP_PORT`:

```python
# Users/<username>/app.py
import os
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def index():
    return {"status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["APP_PORT"]))
```

### A chat app for an agent (5.3+)

For a chat page in front of an **agent deployment**, do not write one: every agent can have a ready-made chat app, and it already handles what a hand-written one gets wrong (who is chatting, keeping each person's conversations apart, no serving key in the browser, the agent's questions, feedback, attachments, waking an agent that scales to zero).

```python
agent = project.get_agent_serving().get_agent("support")
app = agent.create_chat_app()  # app "support_chat", started; or deploy_agent(..., chat_app=True)
```

Members open it with their Hopsworks login; `app.make_public()` shares it with anyone holding the link. Branding without code: the app's `CHAT_TITLE`, `CHAT_WELCOME`, `CHAT_ACCENT_COLOR`, `CHAT_LOGO_URL` and `CHAT_THEME_DIR` variables (details in **hops-agent-deployment**).

To change its page, layout or server, **customize** it rather than starting over:

```python
app = agent.customize_chat_app()  # runs from Resources/chat-apps/support_chat
```

The app copies its template into that project folder (a `server.mjs` on the image's `@hopsworks/agent-chat` package, a React page in `web/`, `theme.css`) and runs from it. **Read the folder's `AGENTS.md` before changing anything**: it lists the extension points (`<HopsworksChat />` slots and parts, `startChatApp()` options, a `routes` hook given who is asking) and what must stay the package's. The package is in the app's image, not the terminal's, so try a change with `app.redeploy()` and `app.get_logs()`; a folder that fails to build is logged and the default page is served. Removing the app's `CHAT_APP_DIR` variable goes back to the default app. An app that already has a page of its own can hold the chat instead (`create_app(..., agent=agent)` + `hopsworks-agent-chat` beside its server): see **hops-agent-chat**.

### Reading Hopsworks data from the app

The SDK calls are the same as anywhere (hops-fg / hops-fv / hops-online-inference);
three things are app-specific:

- **A just-created feature group is not queryable via Trino/`hops sql` immediately.** The offline table syncs into the Trino catalog with a short lag, so a `SELECT ... FROM <fresh_fg>` right after `insert` can return `TABLE_NOT_FOUND`. Online feature-vector reads are available before the Trino table is, so make the app not-found-safe (warn on an empty online vector) instead of trusting a range from a fresh query.
- **Embedded model** (predict locally instead of calling a deployment): `model_dir = mr.get_model("fraud_model", version=1).download()`; cache the loaded model and its feature view in `@st.cache_resource` so the download happens once, and read through the feature view so the same MDTs/ODTs the model saw in training are applied (no training/serving skew).
- **An app over a batch ML system** reads the prediction feature group the batch job writes and
  never calls a deployment, because a batch system has none; a what-if score uses the embedded
  model above.
- **Calling a deployment** (real-time systems only): check `deployment.is_running()` before `predict`, and surface a message rather than blocking (see cold start below).
- **App state and SQL over the online store:** the pod gets `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DB`, `MYSQL_USER` and `MYSQL_PASSWORD_SECRET_NAME` for the project's online feature store database (created on demand at start). Create and alter app tables with `app.create_table()` and `app.alter_table()`, which keep them on disk in RonDB (the app's own user cannot run `CREATE` or `ALTER TABLE`), and read online feature group tables (`<fg>_<version>`) by primary key. Connection helpers for Python, SQL and Node.js, table registration, and RonDB table rules: **hops-app-db**.

### Reading Trino from a JavaScript app

The browser cannot query Trino itself: `trino.service.consul` resolves only
inside the cluster, Trino takes passwords only over HTTPS (port 8080 answers
`403 Authentication over HTTP is not enabled`), and the password must not reach
the page. A Node server in the app holds the connection and answers the UI's
relative `fetch("api/...")` calls with fixed queries, so the browser sends
parameters, never SQL. `python-agent-pipeline` ships Node.

The connection comes from the SDK: `trino_env.py` logs in, which writes the
cluster CA to `/tmp/ca_chain.pem`, and writes the coordinator URL, the user
`<project>__<username>`, its password from the secrets store and the schema
`<project>_featurestore` to `/tmp/trino.json`, readable only by the app's user.
Never print the password or export it as an environment variable: both leak it
into logs and every child process. A feature group is the table
`<name>_<version>` in catalog `delta` (or `hudi`/`iceberg`, per its format).

```javascript
import { readFileSync } from "node:fs";
import pkg from "trino-client";
const { Trino, BasicAuth } = pkg;

const conn = JSON.parse(readFileSync("/tmp/trino.json", "utf8")); // from trino_env.py
const trino = Trino.create({
  server: conn.server, // https://coordinator.trino.service.consul:8443
  catalog: "delta",
  schema: conn.schema, // <project>_featurestore
  auth: new BasicAuth(conn.user, conn.password),
  ssl: { ca: readFileSync(conn.ca) }, // the cluster CA, not in Node's trust store
});

const query = await trino.query("SELECT * FROM customers_1 LIMIT 100");
for await (const result of query) {
  if (result.error) throw new Error(result.error.message);
  console.log(result.columns?.map((c) => c.name), result.data); // rows arrive in pages
}
```

The whole app (server with `/health`, `/api/rows` and the static UI) is
[references/trino_js/](references/trino_js/). Install `trino-client` into the
app directory before the upload, so the app starts without the network, and
copy `app.css` from the skeleton (`<skills>` is
`/opt/hopsworks-api/python/hopsworks/skills` in a Hopsworks terminal):

```bash
cp -r <skills>/dashboards/hops-app/references/trino_js apps/customers
cp <skills>/dashboards/hops-app/references/app_skeleton/static/app.css apps/customers/static/
(cd apps/customers && npm install --no-audit --no-fund)
hops files upload apps/customers Users/<user>/apps/ --overwrite
hops app create customers --path /Projects/<project>/Users/<user>/apps/customers/server.js \
  --app-kind custom --app-port 8080 --readiness-probe-path /health \
  --environment python-agent-pipeline --start \
  --entrypoint-command "bash -lc 'python trino_env.py && APP_TABLE=customers_1 exec node server.js'"
```

### Streamlit caching and cold start

Heavy work at the top of the script (`login()`, `init_serving()`, a call to an
online deployment) runs on every cold load and blocks the first paint: the app
shows RUNNING but the page hangs. Keep the top of the script cheap:

- `@st.cache_resource` for the connection, feature store handle, initialised feature view (`fv.init_serving(...)` inside the cached function) and deployment handle, so they initialise once, not per rerun.
- `@st.cache_data(ttl=...)` for data reads.
- Never call a deployment at import time. Trigger it from a button / form submit behind `st.spinner(...)`, and guard on `is_running()`:

```python
@st.cache_resource
def get_deployment():
    return project.get_model_serving().get_deployment("fraud_predictor")

if st.button("Score"):
    dep = get_deployment()
    if dep.is_running():
        with st.spinner("Scoring…"):
            st.write(dep.predict(inputs=[{"id": user_id}]))
    else:
        st.warning("Deployment is starting — try again shortly.")
```

### Hopsworks look & feel

Apps render as a bare default Streamlit page unless you theme them. Drop in the
Hopsworks palette so a shipped app reads as part of the platform — brand accents
only, don't restyle every widget.

`.streamlit/config.toml` — **must sit in the same directory as the app script**,
not in `~/.streamlit/`. Streamlit reads config from the script's own directory
(and CWD), so an app under `customer_spend_fti/app.py` needs
`customer_spend_fti/.streamlit/config.toml`. Copy it per app directory.

```toml
[server]
fileWatcherType = "none"   # REQUIRED on HopsFS/FUSE: the watcher stats the
headless = true            # script over FUSE on the event loop and blocks the
runOnSave = false          # readiness probe, making the managed app flap
                           # serving<->running. Without this the app never holds.

[browser]
gatherUsageStats = false

[theme]
primaryColor = "#1EB182"           # Hopsworks teal-green
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#1A1F2B"
textColor = "#FAFAFA"
font = "sans serif"
```

A brand header + accents, injected once at the top of the app:
```python
import streamlit as st

st.set_page_config(page_title="My Hopsworks App", layout="wide")
st.markdown(
    """
    <style>
      .hw-band {background:linear-gradient(90deg,#0E1117,#1A1F2B);
                border-left:6px solid #1EB182; padding:0.75rem 1rem;
                border-radius:6px; margin-bottom:1rem;}
      .hw-band h1 {color:#FAFAFA; margin:0; font-size:1.4rem;}
      div[data-testid="stMetricValue"] {color:#1EB182;}
      .stButton>button {background:#1EB182; color:#0E1117; border:none; font-weight:600;}
    </style>
    <div class="hw-band"><h1>⬡ My Hopsworks App</h1></div>
    """,
    unsafe_allow_html=True,
)
```

A full dashboard (statistics, monitoring history, data sample) is in
[references/monitoring_dashboard.md](references/monitoring_dashboard.md).

---

## Apps built by `/hops app`

`/hops app` turns a description in natural language into a running app from
[references/app_skeleton/](references/app_skeleton/):

- **Shape.** A `custom` app: `app.py` (FastAPI) serves a JSON API under `/api`
  and a static UI from `static/` (`index.html`, `app.js` as plain ES modules,
  `app.css`) in one process on `0.0.0.0:$APP_PORT`, with `/health` for the
  readiness probe. No build step and no CDN, because the app runs air-gapped;
  every URL in the UI is relative (`fetch("api/top")`, `src="static/app.js"`),
  and `index.html` adds the trailing slash relative URLs need under the proxy
  mount. A framework that needs a build (React, Vue, Svelte) only for a
  git-backed app whose repository can hold the built assets. Streamlit when the
  description is a data view with no custom interaction.
- **Nothing moves on interaction.** A list or panel that reloads (a filter, a
  selection) keeps its current content, dimmed with `aria-busy`, until the new
  content replaces it; placeholders only on the first load. The skeleton's
  `app.css` reserves the scrollbar gutter, so content that shrinks for a moment
  never shifts the page sideways.
- **Nothing spills sideways.** A table goes in the skeleton's `.table-wrap`, which
  scrolls it inside its own frame (`contain: inline-size` keeps its width out of
  the layout around it), and every grid the app adds gives its columns
  `minmax(0, …)` (`grid-template-columns: minmax(0, 1fr)`): a plain `1fr` or
  implicit column grows to its widest content and pushes the page past the right
  edge of the Hopsworks app panel. Check the app at about 900px wide.
- **The description is the module docstring** of `app.py`, updated on every
  edit, so the next edit starts from what the app is now.
- **Each request reads only what it returns.** The skeleton's routes query the
  feature group through Trino: the latest row per entity with
  `ROW_NUMBER() OVER (PARTITION BY <id> ORDER BY <ts> DESC)`, the top k with
  `ORDER BY ... LIMIT ?`, one entity with `WHERE <id> = ? ORDER BY <ts> DESC
  LIMIT 1`. Values are bound as `?` parameters, never formatted into the SQL;
  `limit` is capped (`MAX_LIMIT`), and results are cached for `CACHE_SECONDS`.
  Never read a whole feature group into pandas to sort or filter it per request.
- **Where the source lives.** `Users/<user>/apps/<name>/` (`~/apps/<name>/` in a
  terminal; `./apps/<name>/` from a laptop, mirrored with `hops files upload`),
  or a git-backed app when the working directory is a GitHub repository
  (`--git-url`, `--git-branch`, `--git-auto-redeploy`, so a push is a
  redeploy). An ML system's app keeps its source in the system's repository
  under `<slug>/app/`.
- **Environment.** `python-agent-pipeline` ships FastAPI and uvicorn; the
  skeleton's `app-requirements.txt` adds the Trino client where it is missing. A library neither base has goes into
  `app-requirements.txt` and a clone `<name>-app-env` (see **Custom libraries**).

```bash
find apps/customer-lookup -name __pycache__ -prune -exec rm -rf {} +
hops files mkdir Users/<user>/apps                    # the parent must exist; the upload copies the directory
hops files upload apps/customer-lookup Users/<user>/apps/
hops app create customer-lookup --path /Projects/<project>/Users/<user>/apps/customer-lookup/app.py \
  --app-kind custom --entrypoint-command "python app.py" --app-port 8080 \
  --readiness-probe-path /health --environment python-agent-pipeline --start
hops app url customer-lookup
```

**Smoke tests** go to the app itself, not the proxy URL, which needs a browser
session (an API key gets a 401). `--start` returning `serving=yes` means the
readiness probe on `/health` passed. In a Hopsworks terminal, whose `kubectl`
reaches the project namespace, then call the page and each API route the
description implies:

```bash
POD=$(kubectl get pods -o name | grep customer-lookup)
kubectl port-forward "$POD" 18080:8080 &
curl -s localhost:18080/health; curl -s localhost:18080/ | head; curl -s localhost:18080/api/top
```

From an external client without `kubectl`, the readiness probe is the smoke
test, and the URL is handed to the user to open.

## Fix loop

When the app fails to start, stops serving, or a smoke test fails, fix it
without asking, at most five attempts:

1. `hops app logs <name>`: read the error and name the cause in one line.
2. Change the source. A missing library is an environment fix (a pin in
   `app-requirements.txt` installed into the clone), never a workaround in the
   code. Out of memory: raise `--memory` once and say so.
3. `hops app deploy <name> <dir>` (push, for a git-backed app), and rerun the
   smoke tests.

After the fifth attempt, stop with the last lines of the logs and what was tried.

**Edit** changes the source as asked, updates the docstring, runs
`hops app deploy` and reruns the smoke tests. **Delete** confirms the exact name, runs
`hops app delete <name> --yes`, and removes the source directory. Hopsworks
removes an app's cloned environment once nothing uses it.

## Creating and running the app

```python
import hopsworks

project = hopsworks.login()
apps = project.get_app_api()

app = apps.create_app(
    name="my_dashboard",
    app_path="Users/<username>/app.py",   # project-relative (CLI --path is HopsFS-absolute)
    entrypoint_command="python app.py",   # a CUSTOM app, the default kind
    environment="python-app-pipeline",    # or the clone from references/libraries.md
    memory=2048,                          # MB
    cores=1.0,
    db_access=True,                       # default: project database created at start, MYSQL_* injected
)

app.run(await_serving=True)   # blocks until ready; await_serving=False returns immediately
if app.serving:
    print(f"App URL: {app.app_url}")
```

A git-backed app leaves `app_path` unset: [references/git.md](references/git.md).

## Lifecycle and states

```python
app.run(await_serving=True)
print(app.get_url())
app.redeploy()                # re-clone / re-read the source and restart
app.stop()
app.delete()

apps.get_apps()               # list; each has .job_id, .name, .state, .serving
app = apps.get_app("my_dashboard")
```

| State | Meaning |
|---|---|
| `INITIALIZING` | starting up |
| `RUNNING` | container running; check `serving` for readiness |
| `STOPPED` / `FINISHED` | ended normally (stopped by the user / exited) |
| `KILLED` / `FAILED` | ended abnormally (the SDK treats both, plus `INITIALIZATION_FAILED` etc., as failures) |

An app is accessible only when `state == "RUNNING"` **and** `serving == True`.

## Monitoring

Monitoring is enabled by default. Leave routes empty for the default
ignored-path behaviour, or add routes to narrow the signal:

```python
monitoringConfig = {
    "enabled": True,
    "routes": [
        {"path": "/api", "matchType": "prefix"},     # matchType: prefix | exact
        {"path": "/predict", "matchType": "exact"},
    ],
}
```

Streamlit ignores `/_stcore/health`, `/_stcore/host-config`, `/_stcore/stream`
and `/static` by default. Custom apps should leave framework noise (`/static`,
`/docs`, `/openapi.json`, `/redoc`, `/favicon.ico`) out of the monitored routes
unless you want it counted.

## Next Steps

- Read features in the app: **hops-fg** / **hops-fv**. Query via SQL: **hops-trino-sql**.
- App state, or SQL against the online store from inside the pod: **hops-app-db**.
- Dashboards instead of an app: **hops-superset**.
