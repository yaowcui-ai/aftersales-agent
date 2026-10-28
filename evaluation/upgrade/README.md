# Upgrade evidence

- `cases.json`, `manifest.json`, `schema.json`: default **v2**, 128 condition-distinct scenarios, 83 dev / 24 validation / 21 test. Language/form consistency is checked before execution.
- `catalogs/v1/`: untouched first frozen dataset, including the discovered U045 language/form conflict.
- `runs/b0-first-execution/`: v1 real Store + temporary SQLite, 128 independent cases / 136 runs, 135 pass. U082 error-code mismatch is retained.
- `runs/paired-mock-verified/`: v1 wiring check, 12 cases × four arms, 48/48. Scripted models, zero model HTTP requests.
- `runs/live-paired-six-frozen/`: v1 only, six paired cases, 24 runs including six B0 controls; 18 real-model runs, 37 HTTP requests, 54,790 reported tokens. B1 was a one-call diagnostic, so these results do **not** establish a fair graph advantage. U045 remains in the denominator. Original hashes, per-run failures, usage, source audit, and frozen source are retained.
- `runs/v2-live-core-repeat/`: one previously seen development condition U001, B3 only, three independent initial fixtures; 3/3 passed with 7 direct HTTP calls and 10,764 tokens. This is a narrow repetition check, not holdout accuracy or a fair four-arm comparison. Preflight budget is retained separately.
- `runs/v2-b0-final/`: default v2 offline full regression.
- `runs/v2-fair-mock-final/`: v2 B1 handwritten loop versus B2/B3, same six-call cap; U001/U016 each repeat three times for every arm. This is deterministic wiring stability, not stochastic model stability.

`initial-smoke`, `b0-first-execution`, `paired-mock-first`, and `b3-first-smoke` preserve early diagnostics; use the specific final evidence above for delivery. Do not select the best historical result or attribute v1 real-model scores to v2. No v2 live run was authorized/executed automatically.

Each run saves initial and final DB tables, tool arguments/results, persisted approval data, and assertion-by-assertion verdicts. `metadata.json` freezes the inputs before execution. New runners preserve source copies and report whether hashes changed by completion. Local SQLite execution does not validate MySQL, MCP HTTP, browser flows, or restart persistence.

From the project root, `python scripts/evaluate_upgrade.py --catalog-only` checks catalog quality. `--limit 12` runs a limited stratified offline sample; `--all --repeat-core` runs all offline scenarios. `--recompute evaluation/upgrade/runs/live-paired-six-frozen` recalculates aggregate metrics without executing tools or contacting a model.

Full protocol, limits, failure analysis and commands: [EXPERIMENT_DESIGN.md](../../docs/EXPERIMENT_DESIGN.md).
