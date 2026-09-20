"""Convert OMML (Office Math Markup Language) to LaTeX.

PowerPoint and Word store equations as OMML, and in a ``.pptx`` that OMML
usually sits inside an ``mc:AlternateContent`` element that ``python-pptx``'s
shape iterator walks straight past. The cost of not reading it is not
cosmetic: one numerical-analysis deck in the author's vault keeps 168
equations, the full statement and proof of Rolle's Theorem and Taylor's
formula with integral remainder in exactly that place, and the extractor
returned nothing but the slide titles. Every study guide, exam and deck
generated from it was therefore written about a table of contents.

The conversion is deliberately partial. OMML is large and lecture decks use a
small corner of it, so this handles that corner well and degrades everything
else to its own text rather than raising -- a half-converted equation is worth
more to a reader than a dropped one, and an unreadable deck is the failure
this module exists to prevent.
"""

from __future__ import annotations

import unicodedata
from typing import Any

#: The OMML namespace. Exported because callers build XPath against it.
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

#: Wrapper elements that carry formatting, not content. Their text is either
#: empty or a stray artefact of whatever wrote the file, and either way it
#: must not reach the reader.
_PROPERTY_TAGS = frozenset(
    {
        "accPr", "argPr", "barPr", "borderBoxPr", "boxPr", "ctrlPr", "dPr",
        "eqArrPr", "fPr", "funcPr", "groupChrPr", "limLowPr", "limUppPr",
        "mPr", "mcPr", "mcs", "naryPr", "nor", "phantPr", "rPr", "radPr",
        "sPrePr", "sSubPr", "sSubSupPr", "sSupPr",
    }
)

#: Symbols OMML stores as literal Unicode. KaTeX renders most of them, but a
#: vault is also read in Obsidian and diffed in git, where a LaTeX command is
#: far easier to search for and to correct than an astral codepoint.
_SYMBOLS = {
    "−": "-", "–": "-", "—": "-", "∗": "*",
    "×": r"\times", "÷": r"\div", "±": r"\pm",
    "∓": r"\mp", "⋅": r"\cdot", "·": r"\cdot",
    "∙∙∙": r"\cdots", "⋯": r"\cdots", "…": r"\dots",
    "∴": r"\therefore", "≤": r"\leq", "≥": r"\geq",
    "≠": r"\neq", "≈": r"\approx", "≡": r"\equiv",
    "≅": r"\cong", "∼": r"\sim", "∝": r"\propto",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset",
    "⊆": r"\subseteq", "⊃": r"\supset", "⊇": r"\supseteq",
    "∪": r"\cup", "∩": r"\cap", "∅": r"\emptyset",
    "∀": r"\forall", "∃": r"\exists", "∄": r"\nexists",
    "∧": r"\wedge", "∨": r"\vee", "¬": r"\neg",
    "→": r"\to", "←": r"\leftarrow", "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow", "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow", "↦": r"\mapsto", "∞": r"\infty",
    "∂": r"\partial", "∇": r"\nabla", "′": "'",
    "″": "''", "°": r"^{\circ}", "ℝ": r"\mathbb{R}",
    "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}",
    "ℂ": r"\mathbb{C}", "α": r"\alpha", "β": r"\beta",
    "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon",
    "ϵ": r"\epsilon", "ζ": r"\zeta", "η": r"\eta",
    "θ": r"\theta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu",
    "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma",
    "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}

#: Characters LaTeX reads as markup. A deck writing "50% of cases" or ``a_b``
#: as prose inside an equation must not silently become a comment or a
#: subscript.
_ESCAPES = {"%": r"\%", "&": r"\&", "#": r"\#", "_": r"\_"}

#: ``m:nary`` names its operator by character. These are the ones that carry
#: limits, so they need sub/sup treatment rather than a bare substitution.
_NARY = {
    "∫": r"\int", "∬": r"\iint", "∭": r"\iiint",
    "∮": r"\oint", "∑": r"\sum", "∏": r"\prod",
    "∐": r"\coprod", "⋃": r"\bigcup", "⋂": r"\bigcap",
    "⋁": r"\bigvee", "⋀": r"\bigwedge",
}

#: Function names that must render as upright operators. Written as three
#: literal letters, ``lim`` is the product of three variables.
_FUNCTIONS = frozenset(
    {
        "arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "csc",
        "deg", "det", "dim", "exp", "gcd", "inf", "ker", "lim", "ln", "log",
        "max", "min", "sec", "sin", "sinh", "sup", "tan", "tanh",
    }
)

#: ``m:acc`` accents, keyed by the character the file names them with.
#: A macron maps to ``\overline`` rather than ``\bar`` so that it matches what
#: the ``m:bar`` branch emits: one deck writes the same x-bar both ways, and
#: two spellings of one variable read as two variables.
_ACCENTS = {
    "̅": r"\overline", "¯": r"\overline", "̄": r"\overline",
    "̂": r"\hat", "̃": r"\tilde", "̇": r"\dot",
    "̈": r"\ddot", "⃗": r"\vec", "→": r"\vec",
}

#: LaTeX spellings for delimiter characters ``m:d`` names literally.
_FENCES = {
    "{": r"\{", "}": r"\}", "⌊": r"\lfloor", "⌋": r"\rfloor",
    "⌈": r"\lceil", "⌉": r"\rceil", "⟨": r"\langle",
    "⟩": r"\rangle", "‖": r"\|",
}

_TRUE = frozenset({"1", "true", "on"})


def _tag(element: Any) -> str:
    """The local name of an element, with its namespace stripped."""
    return str(element.tag).rsplit("}", 1)[-1]


def _val(element: Any) -> str:
    """The ``m:val`` attribute of a property element, or ``''``."""
    return str(element.get(f"{{{M_NS}}}val") or "")


def _fold_combining(text: str) -> str:
    """Turn a base character plus a combining mark into a LaTeX accent.

    Not every accent in a deck arrives as ``m:acc``. PowerPoint also writes
    x-bar as a literal ``x`` followed by U+0305, which NFKC leaves alone
    because there is no precomposed character for it -- and which reaches a
    reader as a macron floating beside the variable rather than over it.

    A combining mark with nothing to combine with is dropped: it is an
    artefact of the run splitting, and a stray accent command would be worse
    than none.
    """
    out: list[str] = []
    for char in text:
        if unicodedata.category(char) != "Mn":
            out.append(char)
            continue
        accent = _ACCENTS.get(char)
        base = out.pop().strip() if out and out[-1].strip() else ""
        if accent and base:
            out.append(f"{accent}{{{base}}}")
        elif base:
            out.append(base)
    return "".join(out)


def _normalise_text(raw: str) -> str:
    """Fold OMML's literal Unicode into LaTeX a human can read and grep.

    NFKC is what turns the mathematical-italic codepoints OMML emits -- ``f``
    as U+1D453 rather than U+0066 -- back into ASCII. Without it every
    variable in a recovered equation is an astral character that KaTeX renders
    but nobody can type into a search box.
    """
    text = _fold_combining(unicodedata.normalize("NFKC", raw))
    for source, target in _ESCAPES.items():
        text = text.replace(source, target)
    # Longest first, so U+2219 x3 is not consumed three times as one \cdot.
    for symbol in sorted(_SYMBOLS, key=len, reverse=True):
        if symbol not in text:
            continue
        replacement = _SYMBOLS[symbol]
        # A control word needs a space after it or the next letter joins it:
        # "\inX" is an unknown control sequence, "\in X" is set membership.
        # The leading space is for the human -- "x \to a" reads better in an
        # Obsidian pane and in a diff than "x\to a" -- and the surplus is
        # collapsed once, at the end, by omml_to_latex.
        if replacement.startswith("\\") and replacement[-1].isalpha():
            replacement = f" {replacement} "
        text = text.replace(symbol, replacement)
    return text


def _children(element: Any) -> list[Any]:
    """Content children, with the formatting wrappers dropped."""
    return [child for child in element if _tag(child) not in _PROPERTY_TAGS]


def _part(element: Any, name: str) -> str:
    """Convert the first child with the given local name, or ``''``.

    Dispatches through :func:`_convert` rather than :func:`_concat`, because
    some of the names looked up here carry meaning of their own -- ``m:fName``
    is what turns three literal letters into an upright operator -- and
    concatenating its children would skip that.
    """
    for child in element:
        if _tag(child) == name:
            # Stripped so a padded control word does not push whitespace
            # inside the braces a caller is about to put around it.
            return _convert(child).strip()
    return ""


def _wrap(latex: str) -> str:
    """Brace a script so it groups.

    Always braced, never bare: ``x^{n+1}`` and ``x^n+1`` are different
    expressions, and one consistent shape is easier to read in a diff than a
    rule about when the braces were felt to be unnecessary.
    """
    return "{" + latex.strip() + "}"


def _operator(text: str) -> str:
    """Upright-operator spelling for a bare function name, else the text.

    ``lim`` reaches us as three literal characters in two different places --
    inside ``m:fName``, and as the base of an ``m:limLow`` -- and in both it
    would otherwise render as the product of three variables.
    """
    stripped = text.strip()
    return f"\\{stripped}" if stripped in _FUNCTIONS else text


def _concat(element: Any) -> str:
    return "".join(_convert(child) for child in _children(element))


def _convert(element: Any) -> str:  # noqa: PLR0911, PLR0912 - one branch per node
    """Convert one OMML element, degrading unknown nodes to their text."""
    name = _tag(element)

    if name == "t":
        return _normalise_text(element.text or "")
    if name == "r":
        return "".join(
            _normalise_text(node.text or "") for node in element.iter(f"{{{M_NS}}}t")
        )
    if name == "f":
        return rf"\frac{{{_part(element, 'num')}}}{{{_part(element, 'den')}}}"
    if name in ("sSub", "sSup", "sSubSup", "sPre"):
        return _scripted(element, name)
    if name == "rad":
        degree, radicand = _part(element, "deg"), _part(element, "e")
        return rf"\sqrt[{degree}]{{{radicand}}}" if degree else rf"\sqrt{{{radicand}}}"
    if name == "d":
        return _delimited(element)
    if name == "nary":
        return _nary(element)
    if name in ("limLow", "limUpp"):
        # The limit binds to the operator, not to the whole expression: the
        # base of a limLow is "lim" itself, and the thing being taken to the
        # limit is the func's argument, which follows.
        base, limit = _operator(_part(element, "e")), _part(element, "lim")
        operator = "_" if name == "limLow" else "^"
        return f"{base}{operator}{_wrap(limit)}" if limit else base
    if name == "func":
        return _part(element, "fName") + _part(element, "e")
    if name == "fName":
        return _operator(_concat(element))
    if name == "acc":
        accent = _ACCENTS.get(_accent_char(element), r"\hat")
        return f"{accent}{{{_part(element, 'e')}}}"
    if name == "bar":
        # OMML defaults an unpositioned bar to the top, which is why the deck
        # that prompted this module carries no ``pos`` at all.
        command = r"\underline" if _bar_position(element) == "bot" else r"\overline"
        return f"{command}{{{_part(element, 'e')}}}"
    if name == "groupChr":
        return rf"\underbrace{{{_part(element, 'e')}}}"
    if name == "m":
        return _matrix(element)
    if name == "eqArr":
        rows = [_concat(child) for child in element if _tag(child) == "e"]
        return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"
    if name == "phant":
        return rf"\phantom{{{_part(element, 'e')}}}"
    # oMath, oMathPara, e, num, den, sub, sup, lim, deg, and anything OMML
    # grows later: walk through it. This is the degradation the module
    # promises -- a partially converted equation beats a dropped one.
    return _concat(element)


def _scripted(element: Any, name: str) -> str:
    """Sub/superscripts, including the pre-script (tensor) form."""
    base = _part(element, "e")
    sub, sup = _part(element, "sub"), _part(element, "sup")
    if name == "sPre":
        if not (sub or sup):
            return base
        prefix = "{}"
        if sub:
            prefix += f"_{_wrap(sub)}"
        if sup:
            prefix += f"^{_wrap(sup)}"
        return prefix + base
    result = base
    if sub:
        result += f"_{_wrap(sub)}"
    if sup:
        result += f"^{_wrap(sup)}"
    return result


def _bar_position(element: Any) -> str:
    """Whether an ``m:bar`` sits over or under its argument."""
    for child in element:
        if _tag(child) != "barPr":
            continue
        for prop in child:
            if _tag(prop) == "pos":
                return _val(prop)
    return "top"


def _accent_char(element: Any) -> str:
    """The accent character an ``m:acc`` names in its properties."""
    for child in element:
        if _tag(child) != "accPr":
            continue
        for prop in child:
            if _tag(prop) == "chr":
                return _val(prop)
    return ""


def _delimited(element: Any) -> str:
    """An ``m:d`` group: parentheses unless the file names other fences."""
    opener, closer, separator = "(", ")", "|"
    for child in element:
        if _tag(child) != "dPr":
            continue
        for prop in child:
            key = _tag(prop)
            if key == "begChr":
                opener = _val(prop)
            elif key == "endChr":
                closer = _val(prop)
            elif key == "sepChr":
                separator = _val(prop)
    body = separator.join(_concat(child) for child in element if _tag(child) == "e")
    # An empty begChr/endChr means "no fence this side" -- a half-open
    # interval, or a ceiling written with one bar only. LaTeX spells that ".".
    left = _FENCES.get(opener, opener) if opener else "."
    right = _FENCES.get(closer, closer) if closer else "."
    return rf"\left{left}{body}\right{right}"


def _nary(element: Any) -> str:
    """An n-ary operator -- integral, sum, product -- with its limits."""
    char = "∫"
    hide_sub = hide_sup = False
    for child in element:
        if _tag(child) != "naryPr":
            continue
        for prop in child:
            key = _tag(prop)
            if key == "chr":
                char = _val(prop)
            elif key == "subHide":
                hide_sub = _val(prop) in _TRUE
            elif key == "supHide":
                hide_sup = _val(prop) in _TRUE
    result = _NARY.get(char) or _SYMBOLS.get(char) or r"\int"
    sub = "" if hide_sub else _part(element, "sub")
    sup = "" if hide_sup else _part(element, "sup")
    if sub:
        result += f"_{_wrap(sub)}"
    if sup:
        result += f"^{_wrap(sup)}"
    return result + _part(element, "e")


def _matrix(element: Any) -> str:
    """An ``m:m`` matrix as a plain ``matrix`` environment.

    No delimiters: in OMML the brackets around a matrix are a surrounding
    ``m:d``, so emitting ``pmatrix`` here would double them.
    """
    rows: list[str] = []
    for child in element:
        if _tag(child) != "mr":
            continue
        rows.append(" & ".join(_concat(cell) for cell in child if _tag(cell) == "e"))
    return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"


def omml_to_latex(element: Any) -> str:
    """Convert an ``m:oMath`` / ``m:oMathPara`` element to a LaTeX string.

    Returns ``''`` when the element holds no text, so a caller can use the
    result's truthiness to decide whether an equation is worth emitting.
    """
    return " ".join(_convert(element).split())
