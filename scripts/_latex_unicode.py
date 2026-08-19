"""Transliterate the report's LaTeX display equations into readable Unicode.

Word has no LaTeX renderer, so the DOCX edition of the full report shows each
display equation as centred Unicode in Cambria Math. This module is the
converter, kept separate so it can be tested on its own:

    python scripts/_latex_unicode.py        # self-test over the report's forms

It is deliberately narrow: it handles the constructs this project's equations
actually use (\\frac, \\hat, \\overline, \\boxed, \\text, sub/superscripts,
Greek letters and the common relation symbols), not arbitrary LaTeX.

Conversion order matters. Greek letters and sub/superscripts are resolved
*before* fractions, so that a numerator such as ``\\tau^2`` has already become
the single token ``τ²`` by the time the fraction code decides whether it needs
parentheses.
"""

from __future__ import annotations

import re
import unicodedata

GREEK = {
    "varepsilon": "ε", "epsilon": "ε", "alpha": "α", "beta": "β",
    "gamma": "γ", "delta": "δ", "theta": "θ", "lambda": "λ",
    "mu": "μ", "rho": "ρ", "sigma": "σ", "tau": "τ",
    "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Delta": "Δ", "Sigma": "Σ", "Pi": "Π", "Omega": "Ω",
    "Gamma": "Γ", "Theta": "Θ",
}

#: order matters - longer macros first so \\geq is not eaten by \\ge
SYMBOLS: list[tuple[str, str]] = [
    (r"\\Longrightarrow", "  ⟹  "),
    (r"\\Rightarrow", " ⟹ "),
    (r"\\longrightarrow", " ⟶ "),
    (r"\\rightarrow", " → "),
    (r"\\leftarrow", " ← "),
    (r"\\Leftrightarrow", " ⟺ "),
    (r"\\approx", " ≈ "),
    (r"\\equiv", " ≡ "),
    (r"\\geq", " ≥ "), (r"\\ge", " ≥ "),
    (r"\\leq", " ≤ "), (r"\\le", " ≤ "),
    (r"\\neq", " ≠ "), (r"\\ne", " ≠ "),
    (r"\\times", " × "), (r"\\cdot", "·"),
    (r"\\pm", " ± "), (r"\\mp", " ∓ "),
    (r"\\partial", "∂"), (r"\\nabla", "∇"),
    (r"\\sum", "Σ"), (r"\\prod", "∏"), (r"\\int", "∫"),
    (r"\\infty", "∞"),
    (r"\\notin", " ∉ "), (r"\\in", " ∈ "),
    (r"\\subseteq", " ⊆ "), (r"\\subset", " ⊂ "),
    (r"\\wedge", " ∧ "), (r"\\vee", " ∨ "),
    (r"\\ldots", "…"), (r"\\dots", "…"), (r"\\cdots", "⋯"),
    (r"\\sim", " ~ "), (r"\\propto", " ∝ "),
    (r"\\mid", " | "), (r"\\to", " → "),
    (r"\\arg\\!?\\max", "argmax"), (r"\\argmax", "argmax"),
    (r"\\log", "log"), (r"\\ln", "ln"), (r"\\exp", "exp"),
    (r"\\max", "max"), (r"\\min", "min"), (r"\\var", "var"),
    (r"\\pi", "π"), (r"\\ell", "ℓ"),
    (r"\\sin", "sin"), (r"\\cos", "cos"), (r"\\tan", "tan"),
    (r"\\bigg/", "/"), (r"\\big/", "/"),
    (r"\\lfloor", "⌊"), (r"\\rfloor", "⌋"),
    (r"\\lceil", "⌈"), (r"\\rceil", "⌉"),
    (r"\\cup", " ∪ "), (r"\\cap", " ∩ "),
    (r"\\colon", ":"), (r"\\vert", "|"),
    (r"\\%", "%"), (r"\\&", "&"), (r"\\#", "#"),
    (r"\\\$", "$"),
]

SUP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼",
    "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ",
}
SUB = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "−": "₋", "=": "₌",
    "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "i": "ᵢ", "o": "ₒ", "r": "ᵣ",
    "u": "ᵤ", "v": "ᵥ", "x": "ₓ", "n": "ₙ", "s": "ₛ",
    "t": "ₜ", "h": "ₕ", "k": "ₖ", "l": "ₗ", "m": "ₘ",
    "p": "ₚ", "j": "ⱼ",
}

#: replacements after which a trailing source space is only the macro
#: terminator (unlike prefix operators such as ∂, Σ, ∫, log)
TIGHT = {"·", "%", "&", "#", "_", "$", "…", "⋯"}

COMBINING_HAT = "̂"
COMBINING_BAR = "̄"

SUPERS = "".join(SUP.values())
SUBS = "".join(SUB.values())
LETTERS = "A-Za-zͰ-Ͽ"


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def _arg(s: str, i: int) -> tuple[str, int]:
    """Read one LaTeX argument starting at ``i``: ``{...}`` or a single token."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        j = i + 1
        while j < len(s) and s[j].isalpha():
            j += 1
        return s[i:max(j, i + 2)], max(j, i + 2)
    return s[i], i + 1


def _macro(s: str, name: str, fmt) -> str:
    """Replace every ``\\name{...}`` with ``fmt(inner)``, innermost-first."""
    token = "\\" + name
    out, i = [], 0
    while i < len(s):
        if s.startswith(token, i) and not (
            i + len(token) < len(s) and s[i + len(token)].isalpha()
        ):
            inner, j = _arg(s, i + len(token))
            out.append(fmt(_macro(inner, name, fmt)))
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# sub / superscripts
# ---------------------------------------------------------------------------
def _scripts(s: str) -> str:
    """Convert ``^{...}`` / ``_{...}`` to Unicode super/subscripts where possible."""
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch in "^_":
            arg, j = _arg(s, i + 1)
            table = SUP if ch == "^" else SUB
            if arg and all(c in table for c in arg):
                out.append("".join(table[c] for c in arg))
            elif arg == "*":
                out.append("*")
            elif arg and re.fullmatch(rf"[{LETTERS}0-9]+", arg):
                out.append(ch + arg)            # e.g. _GP, ^hist
            elif arg:
                out.append(f"{ch}({arg})")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# fractions
# ---------------------------------------------------------------------------
#: a single symbol: one base letter/digit run plus any attached scripts
_SINGLE = re.compile(
    rf"^[{LETTERS}0-9][{SUPERS}{SUBS}{COMBINING_HAT}{COMBINING_BAR}]*"
    rf"(?:[_^][{LETTERS}0-9]+)?$"
)


def _wrapped(x: str) -> bool:
    """True when the whole of ``x`` sits inside one matching pair of brackets.

    ``(a)/(b)`` also starts with "(" and ends with ")", but those are two
    different brackets, so it is *not* already wrapped.
    """
    if not (x.startswith("(") and x.endswith(")")):
        return False
    depth = 0
    for i, ch in enumerate(x):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i == len(x) - 1
    return False


def _needs_parens(x: str, *, denominator: bool) -> bool:
    """Decide whether a fraction operand must be bracketed.

    A denominator is stricter than a numerator: ``(a+bc)/2b`` would be read as
    ``((a+bc)/2)·b``, so anything that is not a single symbol gets brackets,
    while a numerator only needs them when it contains an operator or a space.
    """
    x = x.strip()
    if not x:
        return False
    if _wrapped(x):
        return False
    if _SINGLE.match(x):
        return False
    if "/" in x:
        return True                       # a nested quotient must be bracketed
    if denominator:
        # "dp", "qty", "100" are single quantities; "2b" is a product
        return not re.fullmatch(rf"(?:[{LETTERS}]{{1,4}}|[0-9]+)[{SUPERS}{SUBS}]*", x)
    return bool(re.search(r"[+\-−×·/=<>≤≥≠ ]", x)) or not re.fullmatch(
        rf"[{LETTERS}0-9{SUPERS}{SUBS}{COMBINING_HAT}{COMBINING_BAR}]+", x)


def _fracs(s: str) -> str:
    """Rewrite ``\\frac{A}{B}`` (recursively) as ``A/B`` or ``(A)/(B)``."""
    out, i = [], 0
    while i < len(s):
        hit = next((m for m in ("\\dfrac", "\\tfrac", "\\frac") if s.startswith(m, i)), None)
        if hit:
            num, j = _arg(s, i + len(hit))
            den, j = _arg(s, j)
            num, den = _fracs(num).strip(), _fracs(den).strip()
            n = f"({num})" if _needs_parens(num, denominator=False) else num
            d = f"({den})" if _needs_parens(den, denominator=True) else den
            frac = f"{n}/{d}"
            # "a / b/c" is ambiguous: bracket a quotient that follows a divide
            if "".join(out).rstrip().endswith("/"):
                frac = f"({frac})"
            out.append(frac)
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
#: stands in for a literal underscore while sub/superscripts are resolved, so
#: that an identifier such as effective\_unit\_price is not read as scripts
_US = ""
_LB = ""        # a literal \{ that must survive brace stripping
_RB = ""        # a literal \}
_BND = ""       # an invisible macro-name boundary from \, \; \: \!


def latex_to_unicode(src: str) -> str:
    """Transliterate one LaTeX expression (or an aligned block) to Unicode.

    Symbol and Greek substitution happens *before* macro expansion, while every
    macro name is still preceded by a backslash. Doing it the other way round
    lets ``\\cdot\\hat{Q}`` become ``\\cdotQ̂``, where the macro name and its
    neighbour have fused and can no longer be told apart.
    """
    s = src.strip().strip("$").strip()

    s = s.replace(r"\_", _US).replace(r"\{", _LB).replace(r"\}", _RB)

    # \left and \right are sizing hints, but the pattern must not swallow the
    # head of \leftarrow / \rightarrow
    for junk in ("left", "right", "Bigg", "bigg", "Big", "big"):
        s = re.sub(r"\\" + junk + r"(?![a-zA-Z])", "", s)
    # Thin spaces vanish visually but still terminate the macro name they follow,
    # so "\pi\,w" must not fuse into "\piw". They become an invisible boundary
    # that is deleted once every macro has been resolved.
    for thin in (r"\!", r"\,", r"\;", r"\:"):
        s = s.replace(thin, _BND)
    s = s.replace(r"\qquad", "      ").replace(r"\quad", "    ")
    s = s.replace("\\\\", "\n")

    # A macro name is terminated by the following space, which is syntax rather
    # than spacing: "\Delta Q" is one symbol beside another, not "Δ  Q".
    for name, ch in GREEK.items():
        s = re.sub(r"\\" + name + r"(?![a-zA-Z]) ?", ch, s)
    for pat, ch in SYMBOLS:
        # After an infix/punctuation symbol the surrounding space is only the
        # macro terminator and should go; after a prefix operator such as
        # \partial or \sum it separates operator from operand and must stay.
        if ch in TIGHT:
            s = re.sub(r" ?" + pat + r"(?![a-zA-Z]) ?", ch, s)
        else:
            s = re.sub(pat + r"(?![a-zA-Z])", ch, s)

    s = _macro(s, "boxed", lambda x: f"⟦ {x} ⟧")
    for name in ("texttt", "textrm", "textit", "textbf", "text",
                 "mathrm", "mathit", "mathsf", "mathbf", "operatorname", "mbox"):
        s = _macro(s, name, lambda x: x)
    s = _macro(s, "mathcal", lambda x: {"F": "\U0001d4d5"}.get(x.strip(), x))
    s = _macro(s, "mathbb", lambda x: {"E": "\U0001d53c", "P": "ℙ", "R": "ℝ"}.get(x.strip(), x))
    for name in ("widehat", "hat"):
        s = _macro(s, name, lambda x: x + COMBINING_HAT)
    for name in ("overline", "bar"):
        s = _macro(s, name, lambda x: x + COMBINING_BAR if len(x) == 1 else f"mean({x})")
    s = _macro(s, "sqrt", lambda x: f"√({x})")

    s = _scripts(s)                # before _fracs, so operands are single tokens
    s = _fracs(s)

    s = re.sub(r"\\[a-zA-Z]+", "", s)      # unknown macros, BEFORE brace removal
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    s = s.replace(_US, "_").replace(_LB, "{").replace(_RB, "}").replace(_BND, "")

    lines = []
    for line in s.split("\n"):
        line = re.sub(r"\s*&\s*", " ", line)
        # one space around binary relations, whichever side the source lost
        line = re.sub(r"\s*([=<>≤≥≠≈≡])\s*", r" \1 ", line)
        line = re.sub(r"[ \t]{5,}", "     ", line)
        line = re.sub(r"\s+([,.;])", r"\1", line)
        line = re.sub(r"\(\s+", "(", line)
        line = re.sub(r"\s+\)", ")", line)
        lines.append(re.sub(r"[ \t]{2,}(?=\S)", lambda m: m.group(0), line).strip())
    # compose combining marks where a precomposed glyph exists (y + U+0302 -> ŷ);
    # bases with no precomposed form, such as Q̂, keep the combining mark.
    return unicodedata.normalize("NFC", "\n".join(x for x in lines if x))


_FIXTURES = [
    (r"p^{*} = \frac{a + bc}{2b}", "p* = (a + bc)/(2b)"),
    (r"p_R^{*} = \frac{a}{2b}", "p_R* = a/(2b)"),
    (r"p^{*}_{GP} - p^{*}_{R} = \frac{c}{2} > 0", "p*_GP - p*_R = c/2 > 0"),
    (r"E = \frac{\%\Delta Q}{\%\Delta P}", "E = (%ΔQ)/(%ΔP)"),
    (r"w_i = \frac{\tau^2}{\tau^2 + se_i^2}", "wᵢ = τ²/(τ² + seᵢ²)"),
    (r"Q(p) = \hat{Q}(p_0)\left(\frac{p}{p_0}\right)^{\varepsilon}",
     "Q(p) = Q̂(p₀)(p/p₀)^ε"),
    (r"\text{WAPE} = \frac{\sum_i |y_i - \hat{y}_i|}{\sum_i y_i}",
     "WAPE = (Σᵢ |yᵢ - ŷᵢ|)/(Σᵢ yᵢ)"),
    (r"\frac{\partial \log Q}{\partial \log P} = \beta_1",
     "(∂ log Q)/(∂ log P) = β₁"),
    (r"GP(p) = (p - c)\cdot \hat{Q}(p)", "GP(p) = (p - c)·Q̂(p)"),
    (r"p^{*} = c\cdot\frac{\varepsilon}{1+\varepsilon}", "p* = c·ε/(1+ε)"),
]


def _selftest() -> int:
    bad = 0
    for src, want in _FIXTURES:
        got = latex_to_unicode(src)
        ok = got == want
        bad += 0 if ok else 1
        print(f"  [{'ok ' if ok else 'FAIL'}] {src}")
        print(f"         -> {got}")
        if not ok:
            print(f"    expected {want}")
    print(f"{len(_FIXTURES) - bad}/{len(_FIXTURES)} fixtures matched")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
