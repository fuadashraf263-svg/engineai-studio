#!/usr/bin/env python3
"""
mathsolver.py -- the exam-maths tier. SymPy, with every answer CHECKED.

WHY A SEPARATE TIER
-------------------
Handbook covers engineering FORMULAS. But a student's exam also has raw
maths: "solve 3x^2 - 5x + 2 = 0", "integrate x sin(x)", "solve the ODE
y'' + 4y = 0", "invert this 3x3". Those are not physics -- they are the
symbolic manipulation underneath it, and getting them wrong quietly
poisons every physics answer built on top.

SymPy solves them exactly. The value this file adds over calling SymPy
directly is the CHECK: every solution is substituted back into the
original problem and the residual reported. A root that does not zero the
polynomial is not a root, and this says so instead of printing it.

    solve      -> substitute each root, residual must be 0
    integrate  -> differentiate the result, must return the integrand
    ode        -> substitute into the differential equation, must satisfy it
    linsolve   -> multiply A x, must return b

That round-trip is the same discipline as the solver domains: never trust
an answer you have not checked against the thing it claims to solve.

Run:  python mathsolver.py --selftest
      python mathsolver.py solve "3*x**2 - 5*x + 2"
      python mathsolver.py integrate "x*sin(x)" x
      python mathsolver.py ode "Derivative(y(x),x,2) + 4*y(x)"
"""

from __future__ import annotations

import sys

import sympy as sp

try:
    # SymPy's sympify evaluates Python expressions. Never pass a request to it
    # before the shared EngineAI expression gate has rejected code-like input.
    from owner import safe_expr, Unsafe
except Exception:  # A broken hardening module must fail closed.
    class Unsafe(ValueError):
        pass

    def safe_expr(_s, extra_names=(), maxlen=400):
        raise Unsafe("owner.py is required before evaluating expressions")


def _sym(name="x"):
    return sp.Symbol(name)


def _parse(expr_str: str, *, symbols=(), functions=None) -> sp.Expr:
    """Parse a user expression only after the EngineAI expression allow-list."""
    names = {str(name) for name in symbols}
    safe_expr(expr_str, extra_names=names)
    local = {name: sp.Symbol(name) for name in names}
    local.update({
        "pi": sp.pi, "E": sp.E, "I": sp.I,
        "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
        "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
        "sqrt": sp.sqrt, "log": sp.log, "exp": sp.exp,
        "abs": sp.Abs, "Abs": sp.Abs,
    })
    if functions:
        local.update(functions)
    return sp.sympify(expr_str.replace("^", "**"), locals=local)


# ------------------------------------------------------------- algebra ----

def solve_polynomial(expr_str: str, var: str = "x") -> dict:
    """Solve expr = 0. Each root is substituted back; residual must vanish."""
    x = _sym(var)
    expr = _parse(expr_str, symbols=(var,))
    roots = sp.solve(expr, x)
    checked = []
    max_resid = 0.0
    for r in roots:
        resid = sp.simplify(expr.subs(x, r))
        rn = complex(sp.N(resid))
        max_resid = max(max_resid, abs(rn))
        checked.append({"root": str(r), "root_numeric": str(sp.N(r, 8)),
                        "residual": str(resid),
                        "verified": abs(rn) < 1e-9})
    return {"op": "solve", "expr": str(expr), "roots": checked,
            "all_verified": all(c["verified"] for c in checked),
            "max_residual": max_resid, "provenance": "MATH (checked)"}


# ------------------------------------------------------------- calculus ---

def integrate(expr_str: str, var: str = "x") -> dict:
    """Indefinite integral, checked by differentiating the result."""
    x = _sym(var)
    expr = _parse(expr_str, symbols=(var,))
    F = sp.integrate(expr, x)
    back = sp.simplify(sp.diff(F, x) - expr)
    return {"op": "integrate", "integrand": str(expr),
            "result": str(F) + " + C",
            "check_derivative_minus_integrand": str(back),
            "verified": back == 0, "provenance": "MATH (checked)"}


def differentiate(expr_str: str, var: str = "x", order: int = 1) -> dict:
    x = _sym(var)
    expr = _parse(expr_str, symbols=(var,))
    d = sp.diff(expr, x, order)
    return {"op": "differentiate", "expr": str(expr), "order": order,
            "result": str(sp.simplify(d)), "provenance": "MATH"}


def definite_integral(expr_str, var, lo, hi) -> dict:
    x = _sym(var)
    expr = _parse(expr_str, symbols=(var,))
    lower = _parse(str(lo), symbols=(var,))
    upper = _parse(str(hi), symbols=(var,))
    val = sp.integrate(expr, (x, lower, upper))
    return {"op": "definite_integral", "integrand": str(expr),
            "limits": [str(lo), str(hi)], "exact": str(val),
            "numeric": str(sp.N(val, 10)), "provenance": "MATH"}


# ------------------------------------------------------------------ ode ---

def solve_ode(expr_str: str, func: str = "y", var: str = "x") -> dict:
    """Solve expr = 0 as an ODE in func(var). Checked by substitution."""
    x = _sym(var)
    y = sp.Function(func)
    expr = _parse(expr_str, symbols=(var, func),
                  functions={func: y, "Derivative": sp.Derivative})
    sol = sp.dsolve(sp.Eq(expr, 0), y(x))
    lhs = expr.subs(y(x), sol.rhs).doit()
    resid = sp.simplify(lhs)
    return {"op": "ode", "equation": str(expr) + " = 0",
            "solution": str(sol), "check_residual": str(resid),
            "verified": resid == 0, "provenance": "MATH (checked)"}


# --------------------------------------------------------------- linear ---

def linsolve(A_rows: list, b: list) -> dict:
    """Solve A x = b, checked by computing A x - b."""
    A = sp.Matrix(A_rows)
    bb = sp.Matrix(b)
    if A.det() == 0:
        return {"op": "linsolve", "error": "singular matrix (det = 0); no "
                "unique solution", "verified": False,
                "provenance": "MATH (checked)"}
    x = A.solve(bb)
    resid = sp.simplify(A * x - bb)
    return {"op": "linsolve", "solution": [str(v) for v in x],
            "numeric": [str(sp.N(v, 8)) for v in x],
            "check_Ax_minus_b": str(resid.T),
            "verified": resid == sp.zeros(*bb.shape),
            "provenance": "MATH (checked)"}


def eigenvalues(A_rows: list) -> dict:
    A = sp.Matrix(A_rows)
    ev = A.eigenvals()
    return {"op": "eigenvalues",
            "eigenvalues": {str(k): int(v) for k, v in ev.items()},
            "numeric": [str(sp.N(k, 8)) for k in ev],
            "provenance": "MATH"}


# ------------------------------------------------------------ selftest ----

def selftest() -> int:
    G, R, O = "\033[32m", "\033[31m", "\033[0m"
    fails = 0

    def chk(label, cond):
        nonlocal fails
        fails += (not cond)
        print(f"  {(G+'PASS'+O) if cond else (R+'FAIL'+O)}  {label}")

    print("\n== MATHSOLVER SELF-TEST " + "=" * 42)
    print("  every answer round-tripped against its own problem\n")

    r = solve_polynomial("3*x**2 - 5*x + 2")
    chk(f"quadratic roots verified: "
        f"{[c['root'] for c in r['roots']]}", r["all_verified"])
    chk("  roots are 1 and 2/3",
        {c["root"] for c in r["roots"]} == {"1", "2/3"})

    r = solve_polynomial("x**2 + 1")
    chk(f"complex roots verified (x^2+1): "
        f"{[c['root'] for c in r['roots']]}", r["all_verified"])

    r = integrate("x*sin(x)")
    chk(f"integral of x sin x checked: {r['result']}", r["verified"])

    r = integrate("1/x")
    chk("integral of 1/x checked", r["verified"])

    r = integrate("exp(x**2)")   # non-elementary -> erf; still must check
    chk("integral of exp(x^2) (erfi) checked", r["verified"])

    r = differentiate("x**3 + 2*x", "x")
    chk(f"d/dx(x^3+2x) = {r['result']}", r["result"] == "3*x**2 + 2")

    r = definite_integral("x**2", "x", 0, 3)
    chk(f"definite int x^2 from 0 to 3 = {r['exact']}", r["exact"] == "9")

    r = solve_ode("Derivative(y(x),x,2) + 4*y(x)")
    chk(f"ODE y''+4y=0 solution checked: {r['solution']}", r["verified"])

    r = solve_ode("Derivative(y(x),x) - y(x)")
    chk("ODE y'-y=0 (exp) checked", r["verified"])

    r = linsolve([[2, 1], [1, 3]], [3, 5])
    chk(f"2x2 linear system checked: x={r['numeric']}", r["verified"])
    chk("  solution is [4/5, 7/5]", r["solution"] == ["4/5", "7/5"])

    r = linsolve([[1, 2], [2, 4]], [1, 2])
    chk("singular system rejected", not r["verified"] and "error" in r)

    r = eigenvalues([[2, 0], [0, 3]])
    chk("eigenvalues of diag(2,3)",
        set(r["eigenvalues"]) == {"2", "3"})

    try:
        solve_polynomial("__import__('os').system('id')")
        chk("code-like input blocked", False)
    except Unsafe:
        chk("code-like input blocked", True)

    print(f"\n  {'ALL CHECKS PASS' if not fails else str(fails)+' FAILURE(S)'}")
    return 1 if fails else 0


# ----------------------------------------------------------------- cli ----

def _show(d):
    for k, v in d.items():
        print(f"  {k:32s} {v}")


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__.split("Run:")[-1])
        return 0
    if a[0] == "--selftest":
        return selftest()
    try:
        if a[0] == "solve":
            _show(solve_polynomial(a[1], a[2] if len(a) > 2 else "x"))
        elif a[0] == "integrate":
            _show(integrate(a[1], a[2] if len(a) > 2 else "x"))
        elif a[0] == "diff":
            _show(differentiate(a[1], a[2] if len(a) > 2 else "x"))
        elif a[0] == "ode":
            _show(solve_ode(a[1]))
        else:
            print("commands: solve | integrate | diff | ode | --selftest")
            return 1
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
