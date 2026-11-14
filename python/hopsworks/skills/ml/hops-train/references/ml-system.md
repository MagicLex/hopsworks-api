# Training inside an ML system (`/hops-build`)

Read with `hops skills info hops-train references/ml-system.md` in the train phase of an ML system.

## Split policy: train, validation and test

Three parts: train, validation (selection) and test (acceptance), the last two
`validation_fraction` and `test_fraction` of the data, 15% each by default.

- `problem.generalises_to: new_periods` gives a **time-ordered** split with test
  last and validation before it; `new_entities` gives a split **grouped by the
  entity**, so no entity appears in two parts. Ask the user with
  `AskUserQuestion` when it is unclear which applies.
- Rows younger than `problem.label_maturity` have no final label and fall in no
  part; record the label cutoff.
- Transformation statistics are fitted on the train part only, which is how
  Hopsworks computes them for a training dataset with splits.
- Inside an ML system, the hops-reqs template's `evaluate.split_boundaries` computes the time boundaries for
  `fv.create_train_validation_test_split(train_start=..., ..., test_end=...)`;
  `evaluate.grouped_part` assigns an entity to a part by a stable hash.
- Selection reads validation only; the test part is scored once per candidate
  presented for acceptance.

## Pretrained first

When public models cover the task (text, embeddings, image, audio, speech,
forecasting or tabular foundation models, LLM tasks), shortlist at most three
Hugging Face Hub candidates by task, modality and a licence in
`requirements.models.licences`, recording the revision the Hub reports. Import
each into the registry server-side, so nothing lands on the laptop:

```python
mr = project.get_model_registry()
token = hopsworks.get_secrets_api().get_secret("hf_token").value   # requirements.models.token_secret
candidate = mr.hf_download("owner/repo", hf_token=token, revision="<sha>", timeout=1200)
# candidate.description names the commit the files came from
```

Score each on the validation part with the same metric, through the
`<slug>-eval` job (`evaluate.py --model <name>:<version> --split validation`).
One that meets the target goes to acceptance as-is; one that comes close is a
fine-tuning idea for the loop; otherwise train from scratch. Record the search
under `training.pretrained` either way, `searched: true, chosen: none` included.

## Three program modes

`training_pipeline.py` (from the hops-reqs system template) takes `--mode`:

| Mode | Trains on | Scores | Registers |
| --- | --- | --- | --- |
| `research` | train, within `per_run` | validation | the next version of `<ident>_research`, always |
| `accept` | train + validation, at a given commit | test, once | a version of `<ident>_model` only when the target holds |
| `retrain` | a new training dataset version with the recorded split policy, after the short checks | test | as `accept` |

Research versions never live under `<ident>_model`, so its newest version is
always an accepted one and a batch job reading the newest version is safe.

## Experiment loop

Inside `/hops-build`, the training agent improves the model with an auto-research
loop: `training_pipeline.py` is the one editable file, `evaluate.py`, the
training dataset version and the environment are frozen, one idea per run,
every run committed and registered, keep or revert, never pause to ask. The
protocol is [references/autoresearch.md](autoresearch.md). A system
that names its own copy in `training.autoresearch` follows that copy instead.

## Tests

The training row of [hops-reqs/references/tests.md](../../hops-reqs/references/tests.md):
unit tests for the split, the metrics, the feature list and the registration
rule of each mode; an integration test that runs `accept` on a small slice into
`<ident>_model_test_<run_id>` and deletes it.
