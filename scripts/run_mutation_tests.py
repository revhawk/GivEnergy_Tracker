#!/usr/bin/env python3
"""AST Mutation Testing Runner for GivEnergy Tariff Optimiser.

Injects comparison (<, <=, >, >=, ==, !=), boolean (and/or), and arithmetic (+/-)
mutations into core decision modules, runs targeted pytest suites against each mutant
(always restoring the original file in a finally block), and reports mutation kill rates.

Usage:
    python scripts/run_mutation_tests.py
    python scripts/run_mutation_tests.py --module simulation_helpers
    python scripts/run_mutation_tests.py --module hive --min-score 80
"""

import argparse
import ast
import copy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CMP_SWAP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}

BOOL_SWAP = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}

BINOP_SWAP = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
}

# Authoritative mutation test suites (module_key -> (rel_path, test_files, line_range, max_mutants))
SUITES = {
    "hive": ("ha-addon/hive.py", ["tests/test_hive.py"], None, 40),
    "planner": ("ha-addon/planner.py", ["tests/test_planner.py"], None, 45),
    "axle": ("ha-addon/axle.py", ["tests/test_axle.py"], None, 45),
    "octoplus": ("ha-addon/octoplus.py", ["tests/test_helpers.py", "tests/test_e2e_guardrails.py"], (450, 545), 45),
    "tariffs": ("ha-addon/tariffs.py", ["tests/test_api_parsing.py", "tests/test_contracts.py"], None, 45),
    "rewards": ("ha-addon/rewards.py", ["tests/test_helpers.py", "tests/test_rewards.py"], None, 40),
    "simulation_helpers": (
        "ha-addon/simulation.py",
        ["tests/test_optimization.py", "tests/test_e2e_guardrails.py"],
        (365, 425),
        45,
    ),
    "simulation_monitors": (
        "ha-addon/simulation.py",
        ["tests/test_optimization.py", "tests/test_e2e_guardrails.py"],
        (80, 265),
        45,
    ),
}


def collect_mutants(source: str, line_range=None):
    """Yield (description, lineno, mutated_source) for each AST mutant."""
    tree = ast.parse(source)
    lines = source.splitlines()

    targets = []
    for idx, node in enumerate(ast.walk(tree)):
        lineno = getattr(node, "lineno", None)
        if lineno is None:
            continue
        if line_range and not (line_range[0] <= lineno <= line_range[1]):
            continue
        line_text = lines[lineno - 1] if 1 <= lineno <= len(lines) else ""
        if "logging." in line_text or line_text.strip().startswith("#"):
            continue

        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op_cls = type(node.ops[0])
            if op_cls in CMP_SWAP:
                targets.append((idx, "cmp", op_cls, CMP_SWAP[op_cls], lineno, line_text.strip()))
        elif isinstance(node, ast.BoolOp):
            op_cls = type(node.op)
            if op_cls in BOOL_SWAP:
                targets.append((idx, "bool", op_cls, BOOL_SWAP[op_cls], lineno, line_text.strip()))
        elif isinstance(node, ast.BinOp) and type(node.op) in BINOP_SWAP:
            if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)) and \
               not (isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)):
                op_cls = type(node.op)
                targets.append((idx, "binop", op_cls, BINOP_SWAP[op_cls], lineno, line_text.strip()))

    mutants = []
    for idx, kind, old_cls, new_cls, lineno, line_text in targets:
        m_tree = copy.deepcopy(tree)
        for cur_idx, cur_node in enumerate(ast.walk(m_tree)):
            if cur_idx == idx:
                if kind == "cmp":
                    cur_node.ops[0] = new_cls()
                elif kind == "bool":
                    cur_node.op = new_cls()
                elif kind == "binop":
                    cur_node.op = new_cls()
                break
        ast.fix_missing_locations(m_tree)
        mutants.append((f"L{lineno} ({old_cls.__name__}->{new_cls.__name__}): {line_text[:72]}", lineno, ast.unparse(m_tree)))
    return mutants


def run_module_mutations(rel_file: str, test_files: list, line_range=None, max_mutants=40):
    """Run AST mutations on rel_file using test_files; always restores original source."""
    target = ROOT / rel_file
    original = target.read_text(encoding="utf-8")
    mutants = collect_mutants(original, line_range=line_range)[:max_mutants]
    print(f"\n=== Mutation Testing: {rel_file} ({len(mutants)} mutants) ===", flush=True)

    killed = 0
    survived = []
    try:
        for desc, _lineno, m_src in mutants:
            target.write_text(m_src, encoding="utf-8")
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-x", "--tb=no"] + test_files,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                is_killed = (res.returncode != 0)
            except subprocess.TimeoutExpired:
                is_killed = True
            if is_killed:
                killed += 1
            else:
                survived.append(desc)
                print(f"  [SURVIVED] {desc}", flush=True)
    finally:
        target.write_text(original, encoding="utf-8")

    score = (killed / len(mutants) * 100.0) if mutants else 100.0
    print(f"Result for {rel_file}: {killed}/{len(mutants)} killed ({score:.1f}%) | {len(survived)} survived", flush=True)
    return killed, len(mutants), survived


def main():
    parser = argparse.ArgumentParser(description="Run AST mutation tests on core GivEnergy Tracker modules.")
    parser.add_argument(
        "--module",
        choices=list(SUITES.keys()) + ["smoke", "all"],
        default="all",
        help="Target module suite to mutate ('smoke' runs fast pre-commit subset; default: all)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Fail with exit code 1 if overall kill rate is below this percentage.",
    )
    args = parser.parse_args()

    if args.module == "all":
        selected = list(SUITES.keys())
    elif args.module == "smoke":
        selected = ["hive", "simulation_helpers", "rewards"]
    else:
        selected = [args.module]
    total_k = 0
    total_m = 0
    for key in selected:
        rel_file, test_files, lrange, max_m = SUITES[key]
        k, m, _ = run_module_mutations(rel_file, test_files, lrange, max_mutants=max_m)
        total_k += k
        total_m += m

    overall = (total_k / total_m * 100.0) if total_m else 100.0
    print(f"\nOVERALL MUTATION SCORE ({', '.join(selected)}): {total_k}/{total_m} killed ({overall:.1f}%)")
    if overall < args.min_score:
        sys.exit(1)


if __name__ == "__main__":
    main()
