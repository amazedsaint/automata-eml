
#!/usr/bin/env python3
"""
Automata-flavored demo of exact search in the EML language.

Core idea
---------
Take one binary primitive

    E(a, b) = exp(a) - ln(b)

and build every formula as the same kind of binary tree:

    S -> 1 | x | y | E(S, S)

Why the automata angle?
-----------------------
Because every internal node has the same symbol, the search space becomes
one uniform ranked tree language instead of a messy grammar with many
operators of different arities.

That lets us do a simple bottom-up search:

1. Enumerate all trees up to a size budget.
2. Propagate a small semantic state upward for each subtree:
      - sampled values on a grid
      - a conservative interval
      - a rounded semantic signature
3. Reject invalid branches early (for ln, the right child must stay > 0).
4. Deduplicate subtrees that behave the same on the grid.
5. Search the retained library for exact formulas.
6. Reuse exact formulas as macros and compose them into larger exact ones.

What this file demonstrates
---------------------------
Unary exact recovery:
    exp(x), log(x), x^2, 1/x, x+1

Bivariate exact recovery:
    x*y, x/y, y/x, log(x)+log(y)

Macro closure:
    exp(x+y)  from  exp(x), exp(y), and multiplication
    x+y       from  log(exp(x+y))

Run:
    python automata_eml_demo.py

The file prints a small story, runs the demo, and then runs assertions.
"""

from __future__ import annotations

import math
from functools import lru_cache
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Tuple

import numpy as np
import sympy as sp


MAX_ABS = 1e6
MIN_POS = 1e-12
MAX_EXP_ARG = math.log(MAX_ABS)

X_SYM, Y_SYM = sp.symbols("x y", positive=True)


def eml(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        return np.exp(a) - np.log(b)


def valid_values(vals: np.ndarray) -> bool:
    return np.all(np.isfinite(vals)) and float(np.max(np.abs(vals))) <= MAX_ABS


def signature(vals: np.ndarray) -> bytes:
    return np.rint(vals * 1e6).astype(np.int64).tobytes()


def safe_interval_eml(l_lo: float, l_hi: float, r_lo: float, r_hi: float) -> Tuple[float, float] | None:
    if r_lo <= MIN_POS or l_hi > MAX_EXP_ARG:
        return None
    try:
        lo = math.exp(l_lo) - math.log(r_hi)
        hi = math.exp(l_hi) - math.log(r_lo)
    except (OverflowError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    if max(abs(lo), abs(hi)) > MAX_ABS:
        return None
    return lo, hi


def catalan(n: int) -> int:
    return math.comb(2 * n, n) // (n + 1)


def split_top_level(s: str) -> Tuple[str, str]:
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            return s[:i], s[i + 1 :]
    raise ValueError(f"Malformed expression: {s[:120]}")


@lru_cache(maxsize=None)
def parse_expr(s: str):
    s = s.strip()
    if s in ("1", "x", "y"):
        return ("leaf", s)
    if not (s.startswith("E(") and s.endswith(")")):
        raise ValueError(f"Unsupported expression: {s}")
    left_s, right_s = split_top_level(s[2:-1])
    return ("node", parse_expr(left_s), parse_expr(right_s))


def eval_parsed(ast, x: np.ndarray, y: np.ndarray | None = None) -> np.ndarray:
    kind = ast[0]
    if kind == "leaf":
        token = ast[1]
        if token == "1":
            return np.ones_like(x)
        if token == "x":
            return x
        if y is None:
            raise ValueError("Encountered y in unary evaluation.")
        return y
    left = eval_parsed(ast[1], x, y)
    right = eval_parsed(ast[2], x, y)
    return eml(left, right)


def eval_expr(expr: str, x: np.ndarray, y: np.ndarray | None = None) -> np.ndarray:
    return eval_parsed(parse_expr(expr), x, y)


def sympy_parse_expr(s: str):
    s = s.strip()
    if s == "1":
        return sp.Integer(1)
    if s == "x":
        return X_SYM
    if s == "y":
        return Y_SYM
    left_s, right_s = split_top_level(s[2:-1])
    return sp.exp(sympy_parse_expr(left_s)) - sp.log(sympy_parse_expr(right_s))


def substitute_expr(s: str, mapping: Dict[str, str]) -> str:
    s = s.strip()
    if s in mapping:
        return mapping[s]
    if s in ("1", "x", "y"):
        return s
    left_s, right_s = split_top_level(s[2:-1])
    return f"E({substitute_expr(left_s, mapping)},{substitute_expr(right_s, mapping)})"


def count_size(s: str) -> int:
    s = s.strip()
    if s in ("1", "x", "y"):
        return 0
    left_s, right_s = split_top_level(s[2:-1])
    return 1 + count_size(left_s) + count_size(right_s)


@dataclass
class State:
    expr: str
    vals: np.ndarray
    lo: float
    hi: float
    size: int


@dataclass
class TargetSpec:
    fn: Callable
    sym: sp.Expr


def make_leaf(name: str, values: np.ndarray) -> State:
    vals = np.asarray(values, dtype=np.float64)
    return State(expr=name, vals=vals, lo=float(np.min(vals)), hi=float(np.max(vals)), size=0)


def enumerate_library(
    leaf_values: Dict[str, np.ndarray],
    max_size: int,
) -> Tuple[Dict[int, List[State]], List[dict]]:
    leaves = [make_leaf(name, vals) for name, vals in leaf_values.items()]
    by_size: Dict[int, List[State]] = {0: leaves}
    rows = [{
        "size": 0,
        "structural_count": len(leaves),
        "retained": len(leaves),
        "interval_pruned": 0,
        "duplicates": 0,
        "sample_invalid": 0,
    }]

    num_leaf_symbols = len(leaves)

    for n in range(1, max_size + 1):
        seen: Dict[bytes, State] = {}
        interval_pruned = 0
        duplicates = 0
        sample_invalid = 0

        for lsize in range(n):
            rsize = n - 1 - lsize
            lefts = by_size[lsize]
            rights = by_size[rsize]
            for left in lefts:
                for right in rights:
                    iv = safe_interval_eml(left.lo, left.hi, right.lo, right.hi)
                    if iv is None:
                        interval_pruned += 1
                        continue
                    vals = eml(left.vals, right.vals)
                    if not valid_values(vals):
                        sample_invalid += 1
                        continue
                    key = signature(vals)
                    if key in seen:
                        duplicates += 1
                        continue
                    seen[key] = State(
                        expr=f"E({left.expr},{right.expr})",
                        vals=vals,
                        lo=iv[0],
                        hi=iv[1],
                        size=n,
                    )
        by_size[n] = list(seen.values())
        rows.append({
            "size": n,
            "structural_count": catalan(n) * (num_leaf_symbols ** (n + 1)),
            "retained": len(by_size[n]),
            "interval_pruned": interval_pruned,
            "duplicates": duplicates,
            "sample_invalid": sample_invalid,
        })

    cumulative_structural = 0
    cumulative_retained = 0
    for row in rows:
        cumulative_structural += row["structural_count"]
        cumulative_retained += row["retained"]
        row["cumulative_structural"] = cumulative_structural
        row["cumulative_retained"] = cumulative_retained
        row["kept_fraction"] = row["retained"] / row["structural_count"]
        row["cumulative_pruned_fraction"] = 1.0 - cumulative_retained / cumulative_structural
    return by_size, rows


def search_targets(
    by_size: Dict[int, List[State]],
    targets: Dict[str, TargetSpec],
    x_test: np.ndarray,
    y_test: np.ndarray | None = None,
):
    best = {name: {"train_rmse": float("inf"), "expr": None, "size": None} for name in targets}

    train_env_x = by_size[0][0].vals if by_size[0][0].expr in ("1", "x") else None
    # Better: infer from first non-constant leaf
    # Not used directly; target functions are evaluated below with the explicit grids.

    # Reconstruct the training grids from the leaf states.
    leaf_map = {st.expr: st.vals for st in by_size[0]}
    x_train = leaf_map["x"]
    y_train = leaf_map.get("y", None)

    for n in sorted(by_size):
        exprs = by_size[n]
        vals_mat = np.stack([st.vals for st in exprs], axis=0)
        for name, spec in targets.items():
            if y_train is None:
                y_target = spec.fn(x_train)
            else:
                y_target = spec.fn(x_train, y_train)
            mse = np.mean((vals_mat - y_target) ** 2, axis=1)
            idx = int(np.argmin(mse))
            rmse = float(np.sqrt(mse[idx]))
            if rmse < best[name]["train_rmse"]:
                best[name] = {"train_rmse": rmse, "expr": exprs[idx].expr, "size": exprs[idx].size}

    results = []
    for name, spec in targets.items():
        expr = best[name]["expr"]
        pred_test = eval_expr(expr, x_test, y_test)
        y_true = spec.fn(x_test) if y_test is None else spec.fn(x_test, y_test)
        test_rmse = float(np.sqrt(np.mean((pred_test - y_true) ** 2)))
        exact = best[name]["train_rmse"] < 1e-12
        simplified_expr = None
        symbolic_verified = None
        if exact:
            simplified = sp.simplify(sympy_parse_expr(expr))
            simplified_expr = str(simplified)
            symbolic_verified = bool(sp.simplify(simplified - spec.sym) == 0)
        results.append({
            "target": name,
            "size": best[name]["size"],
            "train_rmse": best[name]["train_rmse"],
            "test_rmse": test_rmse,
            "exact": exact,
            "symbolic_verified": symbolic_verified,
            "expr": expr,
            "simplified_expr": simplified_expr,
        })
    return results


def unary_targets() -> Dict[str, TargetSpec]:
    x = X_SYM
    return {
        "exp(x)": TargetSpec(fn=lambda t: np.exp(t), sym=sp.exp(x)),
        "log(x)": TargetSpec(fn=lambda t: np.log(t), sym=sp.log(x)),
        "x^2": TargetSpec(fn=lambda t: t ** 2, sym=x ** 2),
        "1/x": TargetSpec(fn=lambda t: 1 / t, sym=1 / x),
        "x+1": TargetSpec(fn=lambda t: t + 1.0, sym=x + 1),
        "sin(x)": TargetSpec(fn=lambda t: np.sin(t), sym=sp.sin(x)),
    }


def bivariate_targets() -> Dict[str, TargetSpec]:
    x = X_SYM
    y = Y_SYM
    return {
        "x*y": TargetSpec(fn=lambda a, b: a * b, sym=x * y),
        "x/y": TargetSpec(fn=lambda a, b: a / b, sym=x / y),
        "y/x": TargetSpec(fn=lambda a, b: b / a, sym=y / x),
        "log(x)+log(y)": TargetSpec(fn=lambda a, b: np.log(a) + np.log(b), sym=sp.log(x) + sp.log(y)),
        "x+y": TargetSpec(fn=lambda a, b: a + b, sym=x + y),
        "exp(x+y)": TargetSpec(fn=lambda a, b: np.exp(a + b), sym=sp.exp(x + y)),
    }


def find_result(results: List[dict], target: str) -> dict:
    for row in results:
        if row["target"] == target:
            return row
    raise KeyError(target)


def build_macros(biv_results: List[dict]) -> List[dict]:
    exprs = {row["target"]: row["expr"] for row in biv_results}

    # exp(x+y) = exp(x) * exp(y), where multiplication itself has an exact EML expression
    exp_x = "E(x,1)"
    exp_y = "E(y,1)"
    mul_expr = exprs["x*y"]
    exp_sum_expr = substitute_expr(mul_expr, {"x": exp_x, "y": exp_y})

    # x+y = log(exp(x+y))
    log_x_expr = "E(1,E(E(1,x),1))"
    add_expr = substitute_expr(log_x_expr, {"x": exp_sum_expr})

    test_xs = np.linspace(0.35, 2.15, 6)
    test_ys = np.linspace(0.4, 1.9, 6)
    XG, YG = np.meshgrid(test_xs, test_ys, indexing="ij")
    x_test = XG.ravel()
    y_test = YG.ravel()

    macro_specs = [
        ("exp(x+y)", exp_sum_expr, sp.exp(X_SYM + Y_SYM)),
        ("x+y", add_expr, X_SYM + Y_SYM),
    ]
    rows = []
    for name, expr, sym in macro_specs:
        pred = eval_expr(expr, x_test, y_test)
        truth = np.array([float(sym.subs({X_SYM: a, Y_SYM: b})) for a, b in zip(x_test, y_test)], dtype=np.float64)
        test_rmse = float(np.sqrt(np.mean((pred - truth) ** 2)))
        simplified = sp.simplify(sympy_parse_expr(expr))
        rows.append({
            "target": name,
            "size": count_size(expr),
            "test_rmse": test_rmse,
            "symbolic_verified": bool(sp.simplify(simplified - sym) == 0),
            "expr": expr,
            "simplified_expr": str(simplified),
        })
    return rows


def pretty_table(rows: List[dict], cols: List[str]) -> str:
    text_rows = []
    widths = {c: len(c) for c in cols}
    for row in rows:
        text_row = {}
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                if abs(v) < 1e-4 and v != 0:
                    s = f"{v:.2e}"
                else:
                    s = f"{v:.6g}"
            else:
                s = str(v)
            text_row[c] = s
            widths[c] = max(widths[c], len(s))
        text_rows.append(text_row)

    lines = []
    header = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    lines.append(header)
    lines.append(sep)
    for row in text_rows:
        lines.append(" | ".join(row[c].ljust(widths[c]) for c in cols))
    return "\n".join(lines)


def run_demo():
    print("=" * 72)
    print("EML as an automata-like search language")
    print("=" * 72)
    print("Grammar: S -> 1 | x | y | E(S, S)")
    print("Read this as: every formula is either a leaf or one identical binary node.")
    print("That uniformity is why bottom-up state propagation becomes natural.\n")

    # Unary demo
    train_x = np.linspace(0.25, 2.0, 25)
    test_x = np.linspace(0.3, 2.3, 41)

    unary_lib, unary_stats = enumerate_library({"1": np.ones_like(train_x), "x": train_x}, max_size=9)
    unary_results = search_targets(unary_lib, unary_targets(), x_test=test_x)
    u_last = unary_stats[-1]

    print("Unary demo")
    print("----------")
    print(f"Raw trees through size 9: {u_last['cumulative_structural']:,}")
    print(f"Retained semantic states: {u_last['cumulative_retained']:,}")
    print(f"Compression: {100*u_last['cumulative_pruned_fraction']:.2f}%")
    print(pretty_table(
        [
            {k: row[k] for k in ["target", "size", "train_rmse", "test_rmse", "exact", "simplified_expr"]}
            for row in unary_results
        ],
        ["target", "size", "train_rmse", "test_rmse", "exact", "simplified_expr"],
    ))
    print()

    # Bivariate demo
    train_xs = np.linspace(0.25, 2.0, 4)
    train_ys = np.linspace(0.3, 1.8, 4)
    test_xs = np.linspace(0.35, 2.15, 6)
    test_ys = np.linspace(0.4, 1.9, 6)

    XG_train, YG_train = np.meshgrid(train_xs, train_ys, indexing="ij")
    XG_test, YG_test = np.meshgrid(test_xs, test_ys, indexing="ij")
    train_x_biv = XG_train.ravel()
    train_y_biv = YG_train.ravel()
    test_x_biv = XG_test.ravel()
    test_y_biv = YG_test.ravel()

    biv_lib, biv_stats = enumerate_library(
        {"1": np.ones_like(train_x_biv), "x": train_x_biv, "y": train_y_biv},
        max_size=8,
    )
    biv_results = search_targets(biv_lib, bivariate_targets(), x_test=test_x_biv, y_test=test_y_biv)
    b_last = biv_stats[-1]

    print("Bivariate demo")
    print("--------------")
    print(f"Raw trees through size 8: {b_last['cumulative_structural']:,}")
    print(f"Retained semantic states: {b_last['cumulative_retained']:,}")
    print(f"Compression: {100*b_last['cumulative_pruned_fraction']:.2f}%")
    print(pretty_table(
        [
            {k: row[k] for k in ["target", "size", "train_rmse", "test_rmse", "exact", "simplified_expr"]}
            for row in biv_results
        ],
        ["target", "size", "train_rmse", "test_rmse", "exact", "simplified_expr"],
    ))
    print()

    macro_results = build_macros(biv_results)
    print("Macro closure")
    print("-------------")
    print("Once the library has exact formulas for exp(x), exp(y), multiplication, and log,")
    print("it can build larger exact formulas by pure substitution.")
    print(pretty_table(
        [
            {k: row[k] for k in ["target", "size", "test_rmse", "symbolic_verified", "simplified_expr"]}
            for row in macro_results
        ],
        ["target", "size", "test_rmse", "symbolic_verified", "simplified_expr"],
    ))
    print()

    print("Practical takeaway")
    print("------------------")
    print("This is symbolic regression viewed as a language problem.")
    print("The searcher builds a library of exact symbolic parts, then reuses them as macros.")
    print("That is much closer to how a compiler or bottom-up automaton reasons about trees")
    print("than to how a neural net guesses free-form formulas.\n")

    return {
        "unary_stats": unary_stats,
        "unary_results": unary_results,
        "bivariate_stats": biv_stats,
        "bivariate_results": biv_results,
        "macro_results": macro_results,
    }


def run_tests(results_bundle):
    unary_results = results_bundle["unary_results"]
    bivariate_results = results_bundle["bivariate_results"]
    macro_results = results_bundle["macro_results"]
    unary_stats = results_bundle["unary_stats"]
    bivariate_stats = results_bundle["bivariate_stats"]

    for target in ["exp(x)", "log(x)", "x^2", "1/x", "x+1"]:
        row = find_result(unary_results, target)
        assert row["exact"], f"{target} should be exact in unary search"
        assert row["symbolic_verified"], f"{target} should verify symbolically"
        assert row["test_rmse"] < 1e-10, f"{target} should generalize exactly"

    assert not find_result(unary_results, "sin(x)")["exact"]

    for target in ["x*y", "x/y", "y/x", "log(x)+log(y)"]:
        row = find_result(bivariate_results, target)
        assert row["exact"], f"{target} should be exact in bivariate search"
        assert row["symbolic_verified"], f"{target} should verify symbolically"
        assert row["test_rmse"] < 1e-10, f"{target} should generalize exactly"

    assert not find_result(bivariate_results, "x+y")["exact"]
    assert not find_result(bivariate_results, "exp(x+y)")["exact"]

    for target in ["exp(x+y)", "x+y"]:
        row = next(r for r in macro_results if r["target"] == target)
        assert row["symbolic_verified"], f"{target} should verify after macro closure"
        assert row["test_rmse"] < 1e-10, f"{target} should evaluate exactly on the test grid"

    assert unary_stats[-1]["cumulative_pruned_fraction"] > 0.90
    assert bivariate_stats[-1]["cumulative_pruned_fraction"] > 0.95

    print("All tests passed.")


def main():
    results_bundle = run_demo()
    run_tests(results_bundle)


if __name__ == "__main__":
    main()
