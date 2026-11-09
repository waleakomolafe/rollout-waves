"""Wave math for node rollouts.

A node upgrade is only as safe as the WORST wave: too many nodes at once
and the PDBs the platform team wrote get violated by the drain, not by the
incident. This module turns (pools, per-pod disruption budgets) into an
ordered wave plan where every wave's simultaneous down-node count stays
inside the global budget the PDBs can absorb.

Terminology follows Kubernetes: a PDB with minAvailable: M on a workload
with R replicas allows (R - M) pods to be down AT THE SAME TIME. Across
every workload that pins nodes of a pool, the smallest allowance any
node-pinning workload offers is the pool's safe concurrent-down bound.
"""

from dataclasses import dataclass, field


@dataclass
class Pool:
    name: str
    nodes: int
    max_unavailable: int          # the upgrade config's own cap per wave
    min_available: int = 0        # PDB floor of the workload living here
    replicas: int = 0             # replicas of that workload
    labels: str = ""              # node selector the drain must respect

    @property
    def pdb_allowance(self):
        """How many of this pool's nodes can be down at once without
        breaking the workload's PDB. Zero when no PDB applies (bare nodes)."""
        if self.replicas <= 0:
            return self.nodes
        return max(self.replicas - self.min_available, 0)

    @property
    def wave_capacity(self):
        """Nodes per wave: the tighter of the upgrade cap and the PDB."""
        return max(min(self.max_unavailable, self.pdb_allowance), 0)


@dataclass
class Wave:
    index: int
    pool: str
    nodes: list = field(default_factory=list)

    @property
    def size(self):
        return len(self.nodes)


def plan(pools):
    """Ordered waves for every pool. Pools with the fewest nodes go first —
    a canary pool proves the image before a big one commits to it. Within a
    pool, waves are numbered node-by-node so the emitted plan is stable."""
    if not pools:
        return []
    out = []
    idx = 1
    for p in sorted(pools, key=lambda x: (x.nodes, x.name)):
        cap = p.wave_capacity
        if cap == 0:
            # A workload that cannot lose a single pod blocks its pool.
            # Surface it as a zero-capacity wave rather than silently
            # planning an unsafe one.
            out.append(Wave(idx, p.name, []))
            idx += 1
            continue
        names = ["%s-node-%02d" % (p.name, i + 1) for i in range(p.nodes)]
        for start in range(0, p.nodes, cap):
            out.append(Wave(idx, p.name, names[start:start + cap]))
            idx += 1
    return out


def drain_commands(waves, drain_grace="120s"):
    """The commands an operator (or automation) runs per wave, in order."""
    cmds = []
    for w in waves:
        if not w.nodes:
            cmds.append(("# wave %d: pool %s is PDB-blocked (0 down allowed)"
                         % (w.index, w.pool)))
            continue
        cmds.append("# wave %d: pool %s (%d node(s))" % (w.index, w.pool, w.size))
        for n in w.nodes:
            cmds.append(
                "kubectl drain %s --ignore-daemonsets --delete-emptydir-data "
                "--grace-period=%s --timeout=10m" % (n, drain_grace))
            cmds.append("kubectl uncordon %s  # after the node returns upgraded" % n)
    return cmds
