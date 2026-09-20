"""Tests for OMML (Office Math Markup) -> LaTeX conversion.

Lecture decks store their mathematics as OMML inside ``mc:AlternateContent``,
which ``python-pptx``'s shape iterator does not walk. Before this module the
extractor returned a slide's title and dropped its entire body: the
preliminaries deck in the author's vault lost 74% of its text and all 168 of
its equations, and the study guide generated from it said "the supplied text
does not contain its definition" fourteen times.
"""

from lxml import etree

from backend.rag.extractors.omml import M_NS, omml_to_latex


def _math(inner: str):
    """Parse an ``<m:oMath>`` fragment with the namespace already bound."""
    return etree.fromstring(f'<m:oMath xmlns:m="{M_NS}">{inner}</m:oMath>')


def test_a_run_of_plain_math_text_survives() -> None:
    assert omml_to_latex(_math("<m:r><m:t>x + 1</m:t></m:r>")) == "x + 1"


def test_mathematical_italic_codepoints_fold_to_ascii() -> None:
    """OMML emits U+1D453 for an italic f; KaTeX must receive a plain ``f``."""
    assert omml_to_latex(_math("<m:r><m:t>\U0001d453\U0001d465</m:t></m:r>")) == "fx"


def test_a_fraction_becomes_frac() -> None:
    xml = "<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>n</m:t></m:r></m:den></m:f>"
    assert omml_to_latex(_math(xml)) == r"\frac{1}{n}"


def test_subscripts_and_superscripts() -> None:
    base = "<m:e><m:r><m:t>x</m:t></m:r></m:e>"
    sub = f"<m:sSub>{base}<m:sub><m:r><m:t>0</m:t></m:r></m:sub></m:sSub>"
    assert omml_to_latex(_math(sub)) == "x_{0}"
    sup = f"<m:sSup>{base}<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>"
    assert omml_to_latex(_math(sup)) == "x^{2}"


def test_delimiters_become_left_right_pairs() -> None:
    xml = "<m:d><m:e><m:r><m:t>a,b</m:t></m:r></m:e></m:d>"
    assert omml_to_latex(_math(xml)) == r"\left(a,b\right)"


def test_a_radical_with_no_degree_is_a_square_root() -> None:
    xml = (
        "<m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr>"
        "<m:deg/><m:e><m:r><m:t>2</m:t></m:r></m:e></m:rad>"
    )
    assert omml_to_latex(_math(xml)) == r"\sqrt{2}"


def test_an_n_ary_integral_carries_its_limits() -> None:
    xml = (
        '<m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr>'
        "<m:sub><m:r><m:t>c</m:t></m:r></m:sub>"
        "<m:sup><m:r><m:t>x</m:t></m:r></m:sup>"
        "<m:e><m:r><m:t>f</m:t></m:r></m:e></m:nary>"
    )
    assert omml_to_latex(_math(xml)) == r"\int_{c}^{x}f"


def test_a_lower_limit_renders_lim_as_an_operator() -> None:
    """``lim`` arrives as three literal characters; bare ``lim`` is three variables.

    The nesting here is copied from the author's numerical-analysis deck
    rather than invented: PowerPoint writes the operator and its limit into
    ``m:fName`` and leaves the argument in the ``m:func``'s own ``m:e``.
    """
    xml = (
        "<m:func><m:fName><m:limLow>"
        "<m:e><m:r><m:t>lim</m:t></m:r></m:e>"
        "<m:lim><m:r><m:t>x→a</m:t></m:r></m:lim>"
        "</m:limLow></m:fName>"
        "<m:e><m:r><m:t>f</m:t></m:r></m:e></m:func>"
    )
    assert omml_to_latex(_math(xml)) == r"\lim_{x \to a}f"


def test_unicode_operators_become_latex_commands() -> None:
    xml = "<m:r><m:t>x∈X ∧ 0≤y≤ε</m:t></m:r>"
    latex = omml_to_latex(_math(xml))
    assert r"\in" in latex and r"\leq" in latex and r"\varepsilon" in latex
    assert "∈" not in latex and "≤" not in latex


def test_an_unknown_node_degrades_to_its_text_rather_than_raising() -> None:
    """A partially converted equation beats a dropped one."""
    xml = "<m:borderBox><m:e><m:r><m:t>E=mc</m:t></m:r></m:e></m:borderBox>"
    assert omml_to_latex(_math(xml)) == "E=mc"


def test_control_properties_never_leak_into_the_output() -> None:
    """``m:ctrlPr`` holds run formatting and carries a stray ``w:t`` in the wild."""
    xml = (
        "<m:f><m:fPr><m:ctrlPr/></m:fPr>"
        "<m:num><m:r><m:t>1</m:t></m:r></m:num>"
        "<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>"
    )
    assert omml_to_latex(_math(xml)) == r"\frac{1}{2}"


def test_a_loose_combining_macron_becomes_a_bar_accent() -> None:
    """Not every accent arrives as ``m:acc``.

    PowerPoint also writes x-bar as a literal ``x`` followed by U+0305, which
    NFKC leaves alone because there is no precomposed character for it. Left
    as-is it reaches the reader as a macron floating beside the variable.
    """
    xml = "<m:r><m:t>f'(x̅)</m:t></m:r>"
    assert omml_to_latex(_math(xml)) == r"f'(\overline{x})"


def test_a_combining_mark_with_nothing_to_combine_with_is_dropped() -> None:
    """A stray accent command would render worse than no accent at all."""
    assert omml_to_latex(_math("<m:r><m:t>̅x</m:t></m:r>")) == "x"
