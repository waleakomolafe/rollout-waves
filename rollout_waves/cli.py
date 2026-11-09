"""Command line entry point.

    rollout-waves spec.yaml                # plan + drain commands
    rollout-waves spec.yaml --format json  # machine-readable plan
    rollout-waves spec.yaml --check        # exit 1 if any pool is PDB-blocked
"""

import argparse
import json
import sys

import yaml

from .waves import Pool, drain_commands, plan


class SpecError(Exception):
    pass


def load_spec(path):
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("pools"), list) \
            or not doc["pools"]:
        raise SpecError("spec needs a non-empty 'pools' list")
    pools = []
    for row in doc["pools"]:
        missing = {"name", "nodes", "max_unavailable"} - set(row)
        if missing:
            raise SpecError("pool %r missing %s" % (row.get("name"), sorted(missing)))
        if int(row["nodes"]) <= 0:
            raise SpecError("pool %r needs nodes > 0" % row["name"])
        if int(row["max_unavailable"]) <= 0:
            raise SpecError("pool %r needs max_unavailable > 0" % row["name"])
        pools.append(Pool(
            name=str(row["name"]),
            nodes=int(row["nodes"]),
            max_unavailable=int(row["max_unavailable"]),
            min_available=int(row.get("min_available", 0)),
            replicas=int(row.get("replicas", 0)),
            labels=str(row.get("labels", "")),
        ))
    return pools


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="rollout-waves",
        description="Plan AKS/EKS node upgrades in PDB-safe waves, with "
                    "the drain commands to run.")
    ap.add_argument("spec", help="YAML: pools with nodes, max_unavailable, "
                                 "optional min_available/replicas/labels")
    ap.add_argument("--format", default="text", choices=["text", "json"])
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any pool cannot lose a node (PDB-blocked)")
    args = ap.parse_args(argv)

    try:
        pools = load_spec(args.spec)
    except (SpecError, yaml.YAMLError, OSError) as exc:
        print("FAIL spec: %s" % exc, file=sys.stderr)
        return 2

    waves = plan(pools)
    blocked = [w.pool for w in waves if w.size == 0]

    if args.format == "json":
        print(json.dumps({
            "pools": [p.__dict__ for p in pools],
            "waves": [{"index": w.index, "pool": w.pool, "size": w.size,
                       "nodes": w.nodes} for w in waves],
            "pdb_blocked_pools": blocked,
        }, indent=2))
    else:
        if blocked:
            for p in blocked:
                print("WARNING pool %s is PDB-blocked: its workload cannot "
                      "lose a node; upgrade needs a PDB change first" % p)
        for line in drain_commands(waves):
            print(line)
        print("# %d wave(s), %d node(s), largest wave %d node(s)"
              % (len(waves), sum(p.nodes for p in pools),
                 max((w.size for w in waves), default=0)))

    if args.check and blocked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
