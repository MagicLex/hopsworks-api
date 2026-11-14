---
description: Hopsworks notebook piece. Writes and runs a notebook one cell at a time on the user's Jupyter kernel, with no builder.
argument-hint: "[instruction]"
model: haiku
---

You are running `/hops-piece-notebook` with arguments: `$ARGUMENTS`, reached from `/hops`. The rules, the pieces and the words of `/hops` hold here.

## Notebook

A notebook is cells run on the user's own Jupyter kernel and saved with their outputs, in an
`.ipynb` that opens in the user's JupyterLab. Write it yourself, with no builder:

1. Run `hops notebook start`: it prints the Python environment the cells run in and the user's
   Jupyter folder (`Users/<username>`). The notebook is `<folder>/<slug>.ipynb`, named after what
   it shows. A package the environment lacks: the `hops-notebook` skill, never `pip install` in a cell.
   An existing notebook: `hops notebook info <path> --source` prints every cell's text, and a change
   to several cells is one `hops notebook update <path>` with all of them (`hops-notebook` skill),
   never one `run --cell` per cell.
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
   serves model inputs only and takes every other column (`hops fv create <v> --feature-group <fg>:<n> --except
   <key>,<event time> --labels <label>`, never a hand-picked `select`; text columns one-hot, as
   **hops-train** says), then
   `split_by_key` of **hops-train** section 1 in a cell) and registers
   with `feature_view=` and `training_dataset_version=`, so it can be deployed; only data the
   notebook made itself registers without, unless the request deploys the model: then that data
   goes into a feature group and a view of it first (in a cell:
   `fs.get_or_create_feature_group("<name>", version=1, primary_key=["<key>"]).insert(df)`). "Register the model" afterwards is the best run,
   already registered: say its version. Then offer once per notebook, no option marked
   "(Recommended)", to make it a training pipeline: the model piece (Pieces in `/hops`), on a feature
   group of the notebook's data.
6. A question about the data while this system builds a notebook is answered in it: a cell for the
   table, a cell per chart, never `hops sql` beside it.
7. Never change a cell you did not write unless the user asks: the user may have the notebook open
   in JupyterLab.
8. A model the project already has (a pipeline's, a builder's) in a notebook: load it from the
   registry and plot or score what it holds (step 3, its `run.json` and `images/`); never train it
   again there.
