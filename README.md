# rollout-waves

[![selftest](https://github.com/waleakomolafe/rollout-waves/actions/workflows/selftest.yml/badge.svg)](https://github.com/waleakomolafe/rollout-waves/actions/workflows/selftest.yml)

Plans a Kubernetes node upgrade in waves that the cluster can actually
keep up with, and emits the drain commands. A wave is only as safe as the
tightest PodDisruptionBudget on the nodes it takes down — this tool makes
that arithmetic explicit instead of something you do in your head at 2am.

## The rule it enforces

A PDB with `minAvailable: M` on a workload with `R` replicas allows
`R - M` of its nodes to be down simultaneously. A pool's wave capacity is
the tighter of:

- the upgrade config's own `maxUnavailable`, and
- the smallest PDB allowance of any workload pinned to that pool.

A pool whose workload cannot lose a single node (`R = M`) is reported as
**PDB-blocked**: surfaced as a loud warning and a zero-size wave, never
planned around silently — the fix is a PDB change, not a braver upgrade.

Smallest pool goes first: a 3-node canary pool proves the image before a
40-node pool commits to it.

## The spec

    pools:
      - name: system
        nodes: 3
        max_unavailable: 1
      - name: user
        nodes: 6
        max_unavailable: 2
        min_available: 4     # a real PDB: 5 replicas, 4 must stay up
        replicas: 5
      - name: observability
        nodes: 4
        max_unavailable: 2   # no PDB: the cap rules

`min_available` + `replicas` describe the workload that pins the pool.
Pools without them carry no PDB assumption.

## Use

    pip install .
    rollout-waves spec.yaml                 # plan + drain commands
    rollout-waves spec.yaml --format json   # machine-readable
    rollout-waves spec.yaml --check         # exit 1 on a PDB-blocked pool

The text output is the runbook: wave headers, `kubectl drain` per node
with `--ignore-daemonsets --delete-emptydir-data`, and the `uncordon`
that belongs after the node returns upgraded. Exit codes: 0 planned, 1
blocked pool under `--check`, 2 bad spec.

## Selftest

CI runs the tests, plans this repo's own `examples/aks_prod.yaml`, checks
it clean, and audits the workflows with
[pipeline-sentinel](https://github.com/waleakomolafe/pipeline-sentinel).

## Scope and limits

Static arithmetic over the spec — it does not call the cluster. Node names
are generated (`<pool>-node-NN`); swap them for real names in the emitted
commands, or generate the spec from `kubectl get nodes` in your own glue.
One PDB-bearing workload per pool in v0.1; multi-workload pools need the
min of allowances, which the spec can express by pre-computing the floor.

## License

MIT
