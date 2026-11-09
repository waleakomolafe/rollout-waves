import textwrap

import pytest

from rollout_waves import cli
from rollout_waves.waves import Pool, drain_commands, plan


def P(**kw):
    base = dict(name="p", nodes=6, max_unavailable=3)
    base.update(kw)
    return Pool(**base)


def test_wave_capacity_is_min_of_cap_and_pdb():
    assert P(replicas=5, min_available=4).wave_capacity == 1  # PDB tighter
    assert P(replicas=9, min_available=4).wave_capacity == 3  # cap tighter
    assert P().wave_capacity == 3  # no PDB: the cap rules


def test_no_pdb_means_all_nodes_allowed():
    assert P(nodes=10, max_unavailable=10).pdb_allowance == 10


def test_pdb_allowance_never_negative():
    assert P(replicas=2, min_available=5).pdb_allowance == 0


def test_waves_partition_the_pool():
    waves = plan([P(nodes=7, max_unavailable=3)])
    sizes = [w.size for w in waves]
    assert sizes == [3, 3, 1]


def test_smaller_pools_go_first_in_node_order():
    waves = plan([P(name="big", nodes=9, max_unavailable=3),
                  P(name="small", nodes=3, max_unavailable=1)])
    assert waves[0].pool == "small"
    assert [w.pool for w in waves] == ["small"] * 3 + ["big"] * 3
    # same size: name breaks the tie, so the plan is stable
    waves = plan([P(name="b", nodes=2, max_unavailable=2),
                  P(name="a", nodes=2, max_unavailable=2)])
    assert [w.pool for w in waves] == ["a", "b"]


def test_zero_capacity_pool_surfaces_as_blocked_not_unsafe():
    waves = plan([P(replicas=2, min_available=2)])
    assert len(waves) == 1 and waves[0].size == 0


def test_drain_commands_carry_cordon_and_grace():
    cmds = drain_commands(plan([P(nodes=2, max_unavailable=1)]))
    assert any("kubectl drain p-node-01" in c for c in cmds)
    assert any("kubectl uncordon p-node-01" in c for c in cmds)
    assert any("--grace-period=120s" in c for c in cmds)


def test_blocked_pool_gets_a_comment_not_drains():
    cmds = drain_commands(plan([P(replicas=2, min_available=2)]))
    assert any("PDB-blocked" in c for c in cmds)
    assert not any("kubectl drain" in c for c in cmds)


SPEC = """
    pools:
      - name: user
        nodes: 6
        max_unavailable: 2
        min_available: 4
        replicas: 5
      - name: system
        nodes: 3
        max_unavailable: 1
    """


def _spec(tmp_path, body=SPEC):
    f = tmp_path / "spec.yaml"
    f.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(f)


def test_cli_text_plan(tmp_path, capsys):
    assert cli.main([_spec(tmp_path)]) == 0
    out = capsys.readouterr().out
    # pools run in node-count order: system(3), observability(4), user(6).
    # user's PDB (5 replicas, 4 up) allows ONE node down: 1-node waves.
    assert "wave 1: pool system (1 node(s))" in out
    assert "kubectl drain user-node-01" in out
    # SPEC: system 3x1 + user 6x1 = 9 waves over 9 nodes; the user PDB
    # (5 replicas, 4 up) allows one node down at a time.
    assert "9 wave(s), 9 node(s), largest wave 1 node(s)" in out


def test_cli_json(tmp_path, capsys):
    assert cli.main([_spec(tmp_path), "--format", "json"]) == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert d["pdb_blocked_pools"] == []
    assert sum(w["size"] for w in d["waves"]) == 9


def test_cli_check_fails_on_blocked_pool(tmp_path, capsys):
    body = """
        pools:
          - name: solo
            nodes: 2
            max_unavailable: 1
            min_available: 3
            replicas: 3
        """
    assert cli.main([_spec(tmp_path, body), "--check"]) == 1
    assert "PDB-blocked" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [
    "pools: []",
    "pools:\n  - name: x\n    nodes: 0\n    max_unavailable: 1",
    "pools:\n  - name: x\n    nodes: 1\n    max_unavailable: 0",
    "pools:\n  - nodes: 1\n    max_unavailable: 1",
    "not: a spec",
])
def test_cli_rejects_bad_specs(tmp_path, bad):
    f = tmp_path / "bad.yaml"
    f.write_text(bad, encoding="utf-8")
    assert cli.main([str(f)]) == 2


def test_cli_missing_file():
    assert cli.main(["/nonexistent/spec.yaml"]) == 2
