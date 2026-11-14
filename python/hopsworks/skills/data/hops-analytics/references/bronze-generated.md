# Bronze layers of generated data (`/hops-bronze`)

## Bronze layers of generated data

A bronze layer built by the Factory is generated data, for trying silver and gold layers without a source system: `system.yaml` has `layer.kind: bronze`, and its `generator` names the program copied from a directory of these references (`reference_code`), with the jobs that run it per cadence in `schedule.cadences` and the tables it writes in `tables`.
The clickstream example, [references/clickstream_bronze/](clickstream_bronze/clickstream.py), writes a web shop's `clickstream_customers`, `clickstream_products`, `clickstream_orders` and `clickstream_clicks`: a backfill of the 30 days up to the last midnight (10,000 customers, 1,000 products, 20,000 orders, 1,000,000 clicks), 10,000 clicks an hour, and each day's new and changed customers, products and orders.
Its data is raw on purpose: about 0.001% of clicks arrive twice (the same `click_id` under a new `ingest_id`, the bronze primary key), customers, products and orders arrive again whole when they change (`updated_at` is the event time), and an order's lines are a JSON array in `items`, which a silver layer normalizes into an order lines table.
Every job writes the window `[HOPS_START_TIME, HOPS_END_TIME)` from a generator seeded with the window's start, so a replayed window rewrites the same rows.
`/hops-bronze <slug>` tests the generator, runs the backfill job, deploys and schedules a job per cadence from the backfill's end with catch-up, and verifies a run; the generator tags each table `{"layer": "bronze"}` when it creates it.
Deleting a bronze layer deletes its jobs and the bronze tables it wrote, which no other layer ever deletes.
