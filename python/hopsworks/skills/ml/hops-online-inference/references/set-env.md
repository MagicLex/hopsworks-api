# Change a deployment's environment variables

```bash
hops deployment env <name>                                       # KEY=VALUE lines; --json for an object
hops deployment set-env <name> KEY=VALUE ... [--unset KEY ...]   # merged, saved as a new version
hops deployment versions <name>                                  # versions, active one, env key names and digest
hops deployment rollback <name> <version>                        # back to an earlier version
hops deployment predict <name> --data '{...}' --via-api --timing --json   # {status, ms, response}
```

- `set-env` rolls a running deployment to the new version without stopping it; the old instances serve until it is Running. `--timeout` (default 300 s, 120 s for `rollback`) bounds the whole command, every request included: a request still running then is cut off (a write cut off that way reports `unknown`). A stopped one stays stopped. Nothing differs: it says "unchanged" and writes nothing. Variables Hopsworks sets (`SERVING_*`) are refused.
- Hopsworks can refuse a save whose new version does not fit in the project's quota (`Project quota exceeded`, nothing saved). Right after another rollout that refusal can be HTTP 429: the old pods still hold quota for a few seconds, so retry after 15 s.
- `--json` on `set-env` and `rollback`: `result` is `unchanged`, `applied`, `refused`, `not_ready` or `unknown`. `refused`: nothing changed (the write was never sent, or Hopsworks answered it with an error). `unknown`: the write was sent and its outcome is not known; it may still apply, so read `hops deployment versions <name>` before writing again. `accepted` is true once a read shows the target. Exit code 0 only for `unchanged` and `applied`. Values are never printed, only key names and digests.
- `--via-api` sends the prediction through the Hopsworks API, for a machine that reaches Hopsworks but not the model serving ingress.
- These verbs work with an API key that has only the SERVING scope; such a key cannot read the project by name, so give its id: `HOPSWORKS_PROJECT_ID=<id>`.
