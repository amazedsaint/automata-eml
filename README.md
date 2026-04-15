# Automata-Style Exact Search over a One-Operator Language

## What this is

t is a tiny symbolic math engine built around one strange-looking operation.

Instead of giving the system a big toolbox with +, -, *, /, log, exp, and so on, it gives it just one building block based on this paper https://alphaxiv.org/abs/2603.21852v2
E(a, b) = exp(a) - ln(b)

Then we explore if we can we build useful formulas just by combining that one block over and over


Instead of building symbolic formulas from a large set of operators like `+`, `-`, `*`, `/`, `log`, and `exp`, we use just one primitive:

```text
E(a, b) = exp(a) - ln(b)
```

Using only this primitive and a few leaves like `1`, `x`, and `y`, every formula has the same tree shape.

```text
S -> 1 | x | y | E(S, S)
```

That makes the search space much more uniform. You can then search formulas bottom up like a small automaton or compiler over trees:

- build small expressions first
- reject invalid branches early
- keep only semantically distinct expressions
- reuse exact expressions as macros
- compose them into larger exact formulas

## Why this is interesting

Most symbolic regression systems search over a messy mix of operator types.
This demo shows that if the language is uniform enough, exact search becomes surprisingly practical on small bounded domains.

In the demo, raw search can exactly recover expressions like:

- `exp(x)`
- `log(x)`
- `x^2`
- `1/x`
- `x+1`
- `x*y`
- `x/y`
- `y/x`

Then composition takes over.
Once the system has exact formulas for pieces like `exp(x)`, `exp(y)`, multiplication, and `log`, it can build larger exact formulas such as:

- `exp(x+y)`
- `x+y`



## How the demo works

The code does three things.

### 1. Exact search over expression trees

It enumerates EML trees up to a size limit.
For each candidate expression, it:

- checks whether the log input stays positive
- evaluates the expression on a grid
- discards invalid or duplicate behaviors
- keeps exact winners for target functions

### 2. Macro composition

After exact base formulas are found, the demo composes them.
For example:

- build `exp(x+y)` from exact `exp(x)`, `exp(y)`, and exact multiplication
- build `x+y` by applying exact `log` to exact `exp(x+y)`

### 3. Tests

The demo includes assertions that verify:

- exact recovery for core unary targets
- exact recovery for core bivariate targets
- successful macro composition for `exp(x+y)` and `x+y`
- symbolic simplification where expected

## Run the demo

```bash
python automata_eml_demo.py
```

The script prints the discovered formulas and runs its tests as part of execution.

## What the tests are checking

The tests are not checking a vague “looks good” condition.
They check that:

- the recovered formula matches the target on a held-out test grid
- the exact cases reduce symbolically to the intended expression
- the macro-composed formulas are exact when evaluated

If an expected recovery fails, the script raises an assertion error.

## Limitations

This is a small bounded demo, not a general symbolic math engine.

Important limits:

- only small trees are searched directly
- exactness is shown on bounded domains
- semantic deduplication is empirical, not a formal proof
- deep exact formulas can still be numerically unstable in floating point

That last point matters: a formula can be symbolically exact and still be unpleasant to execute numerically because intermediate `exp` terms may blow up before later `log` terms cancel them.

## Why the automata viewpoint matters

The real payoff is conceptual.

Because every internal node is the same symbol, the whole formula language looks like a regular tree language rather than a heterogeneous symbolic grammar. That is why bottom-up state propagation, pruning, reuse, and composition become so natural.

In plain terms:

This is symbolic regression reimagined as exact search over a tiny tree language.

## Next directions

A few natural extensions are:

- better normalization and simplification of deep macro expressions
- typed or domain-aware pruning
- larger variable sets
- richer macro libraries
- stability-aware execution for deep exact formulas
