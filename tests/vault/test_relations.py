"""The pure half of note relationships: normalise, parse, build, render."""

from __future__ import annotations

import pytest

from backend.vault import relations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Determinism", "Determinism"),
        ("**Determinism**", "Determinism"),
        ("`self-possession`.", "Self-Possession"),
        ("linear regression", "Linear Regression"),
        ("  Free   Will  ", "Free Will"),
        ("the will", "Will"),
        ("A Priori Knowledge", "A Priori Knowledge"),
        ("_Bayes' Theorem_", "Bayes' Theorem"),
        ("Determinism:", "Determinism"),
    ],
)
def test_normalise_keeps_the_concept_and_drops_the_decoration(raw, expected):
    assert relations.normalise_topic(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "3", "42", "AI", "**", "- "])
def test_normalise_rejects_what_is_not_a_concept(raw):
    """Too short, purely numeric, or nothing but punctuation."""
    assert relations.normalise_topic(raw) is None


def test_normalise_does_not_recase_a_term_that_already_has_case():
    """Title-casing runs only on all-lowercase input, so 'RNA polymerase' and
    'pH balance' keep the capitalisation the document gave them."""
    assert relations.normalise_topic("RNA polymerase") == "RNA polymerase"
    assert relations.normalise_topic("pH balance") == "pH balance"


def test_parse_topics_splits_the_section_off_the_body():
    body = (
        "An opening paragraph.\n\n"
        "## Key points\n\n"
        "- something\n\n"
        "## Topics\n\n"
        "- Determinism\n"
        "- **Free Will**\n"
    )
    remainder, topics = relations.parse_topics(body)
    assert topics == ["Determinism", "Free Will"]
    assert "## Topics" not in remainder
    assert remainder.rstrip().endswith("- something")


def test_parse_topics_returns_the_body_untouched_when_the_model_ignored_the_tail():
    """The whole feature has to degrade to today's behaviour, not fail."""
    body = "An opening paragraph.\n\n## Key points\n\n- something\n"
    remainder, topics = relations.parse_topics(body)
    assert topics == []
    assert remainder == body


def test_parse_topics_takes_the_last_section_when_a_style_names_one_earlier():
    """'## Topics' can legitimately appear inside a study guide's outline.
    Only the trailing section is the machine-readable one."""
    body = (
        "## Topics\n\n- prose about topics\n\n"
        "## Notes\n\nreal content\n\n"
        "## Topics\n\n- Determinism\n"
    )
    remainder, topics = relations.parse_topics(body)
    assert topics == ["Determinism"]
    assert "real content" in remainder
    assert remainder.count("## Topics") == 1


def test_parse_topics_caps_and_dedupes_case_insensitively():
    body = "body\n\n## Topics\n\n" + "\n".join(
        f"- {name}"
        for name in [
            "Determinism",
            "determinism",
            "Free Will",
            "Alpha",
            "Beta",
            "Gamma",
            "Delta",
            "Epsilon",
            "Zeta",
            "Eta",
        ]
    )
    _, topics = relations.parse_topics(body)
    assert topics[:3] == ["Determinism", "Free Will", "Alpha"]
    assert len(topics) == relations.MAX_TOPICS


@pytest.mark.parametrize(
    ("topic", "tag"),
    [
        ("Determinism", "topic/determinism"),
        ("Free Will", "topic/free-will"),
        ("Bayes' Theorem", "topic/bayes-theorem"),
        ("A/B Testing", "topic/a-b-testing"),
    ],
)
def test_topic_tag_is_a_stable_nested_slug(topic, tag):
    assert relations.topic_tag(topic) == tag


# --- link assembly -----------------------------------------------------------


def _resolver(mapping: dict[str, str]):
    """A LinkIndex.resolve stand-in: name -> vault-relative path, or None."""
    return lambda name: mapping.get(name)


def test_a_resolved_concept_is_path_qualified_with_an_alias():
    """Two READMEs and thirteen ISLP chapters share stems. A bare [[Name]]
    would cross-wire under the shortest-path ambiguity rule; qualifying it
    with an alias names the file and still reads as the concept."""
    built = relations.build_relations(
        topics=["Linear Regression"],
        resolve=_resolver({"Linear Regression": "60-Knowledge/AI/Linear Regression.md"}),
        neighbours=[],
        source_rel_path="15-Courses/CS101/materials/wk1.pdf",
        note_rel_path="15-Courses/CS101/notes/wk1.notes.md",
        course="CS101",
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is True
    assert concept.wikilink() == "[[60-Knowledge/AI/Linear Regression|Linear Regression]]"


def test_an_unresolved_concept_stays_bare_so_obsidian_can_create_it():
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is False
    assert concept.wikilink() == "[[Determinism]]"


def test_a_concept_in_the_notes_own_folder_is_not_qualified():
    """Same folder needs no path: it is unambiguous, and the shorter form is
    what a human would have typed."""
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({"Determinism": "00-Inbox/files/Determinism.md"}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.wikilink() == "[[Determinism]]"


def test_the_source_link_keeps_its_extension():
    """The live bug: [[essay]] for essay.pdf resolves to nothing in Obsidian.
    [[00-Inbox/files/essay.pdf|essay]] resolves to the attachment."""
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    source = next(link for link in built.links if link.kind == "source")
    assert source.wikilink() == "[[00-Inbox/files/essay.pdf|essay]]"


def test_a_markdown_source_links_without_the_suffix():
    """Obsidian resolves [[folder/Note]] for markdown; keeping '.md' would
    read as a filename rather than a note."""
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="50-Reference/paper.md",
        note_rel_path="50-Reference/paper.summary.md",
        course=None,
    )
    source = next(link for link in built.links if link.kind == "source")
    assert source.wikilink() == "[[50-Reference/paper|paper]]"


def test_the_course_link_is_present_inside_a_course_and_absent_outside_one():
    inside = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="15-Courses/ETHICS/materials/wk1.pdf",
        note_rel_path="15-Courses/ETHICS/notes/wk1.notes.md",
        course="ETHICS",
    )
    course = next(link for link in inside.links if link.kind == "course")
    assert course.wikilink() == "[[15-Courses/ETHICS/course|ETHICS]]"

    outside = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    assert not [link for link in outside.links if link.kind == "course"]


def test_neighbours_are_capped_and_never_include_the_note_or_its_source():
    built = relations.build_relations(
        topics=[],
        resolve=_resolver({}),
        neighbours=[
            ("00-Inbox/files/essay.summary.md", "essay — summary"),  # itself
            ("00-Inbox/files/essay.pdf", "essay"),  # its source
            ("50-Reference/a.md", "A"),
            ("50-Reference/b.md", "B"),
            ("50-Reference/c.md", "C"),
            ("50-Reference/d.md", "D"),
        ],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    found = [link for link in built.links if link.kind == "neighbour"]
    assert len(found) == relations.MAX_NEIGHBOURS
    assert [link.target for link in found] == [
        "50-Reference/a",
        "50-Reference/b",
        "50-Reference/c",
    ]


def test_a_concept_that_resolves_to_a_neighbour_is_not_linked_twice():
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver({"Determinism": "50-Reference/Determinism.md"}),
        neighbours=[("50-Reference/Determinism.md", "Determinism")],
        source_rel_path="00-Inbox/files/essay.pdf",
        note_rel_path="00-Inbox/files/essay.summary.md",
        course=None,
    )
    targets = [link.target for link in built.links]
    assert targets.count("50-Reference/Determinism") == 1


# --- rendering ---------------------------------------------------------------


def _sample() -> relations.Relations:
    return relations.build_relations(
        topics=["Determinism", "Free Will"],
        resolve=_resolver({"Determinism": "60-Knowledge/General/Determinism.md"}),
        neighbours=[("15-Courses/ETHICS/notes/sartre.notes.md", "Sartre")],
        source_rel_path="15-Courses/ETHICS/materials/wk1.pdf",
        note_rel_path="15-Courses/ETHICS/notes/wk1.notes.md",
        course="ETHICS",
    )


def test_the_section_is_fenced_so_a_relink_knows_what_is_its_own():
    section = relations.render_section(_sample())
    assert section.startswith(relations.FENCE_START)
    assert section.rstrip().endswith(relations.FENCE_END)
    assert "## Related" in section
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in section
    assert "[[Free Will]]" in section  # unresolved -> bare
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in section


def test_nothing_to_say_renders_nothing():
    """A note with no topics, no neighbours and no course still has a source
    link, so this is the genuinely empty case: no links at all."""
    empty = relations.Relations(topics=[], links=[])
    assert relations.render_section(empty) == ""


def test_replacing_the_section_is_idempotent():
    body = "real content\n"
    once = relations.replace_section(body, relations.render_section(_sample()))
    twice = relations.replace_section(once, relations.render_section(_sample()))
    assert once == twice
    assert once.count(relations.FENCE_START) == 1


def test_a_hand_edit_outside_the_fence_survives_a_relink():
    body = "real content\n"
    once = relations.replace_section(body, relations.render_section(_sample()))
    edited = once.replace("real content", "real content\n\nEthan's own paragraph.")
    again = relations.replace_section(edited, relations.render_section(_sample()))
    assert "Ethan's own paragraph." in again
    assert again.count(relations.FENCE_START) == 1


def test_replacing_with_an_empty_section_removes_the_region_entirely():
    body = relations.replace_section("real content\n", relations.render_section(_sample()))
    assert relations.replace_section(body, "").rstrip() == "real content"


def test_strip_section_leaves_the_body_a_model_would_have_written():
    body = relations.replace_section("real content\n", relations.render_section(_sample()))
    assert relations.strip_section(body).rstrip() == "real content"


def test_merge_frontmatter_adds_its_keys_and_keeps_the_ones_it_did_not_add():
    front = {"title": "wk1 — summary", "type": "note", "prompt": "", "custom": "keep me"}
    merged = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    assert merged["custom"] == "keep me"
    assert merged["title"] == "wk1 — summary"
    assert merged["topics"] == ["Determinism", "Free Will"]
    assert "argus/note" in merged["tags"]
    assert "course/ETHICS" in merged["tags"]
    assert "topic/determinism" in merged["tags"]
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in merged["related"]


def test_merge_frontmatter_does_not_duplicate_tags_a_second_time_round():
    front = {"title": "t"}
    once = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    twice = relations.merge_frontmatter(dict(once), _sample(), kind="note", course="ETHICS")
    assert once["tags"] == twice["tags"]
    assert once["related"] == twice["related"]


def test_merge_frontmatter_keeps_a_users_own_tags():
    front = {"title": "t", "tags": ["reading/2026", "argus/note"]}
    merged = relations.merge_frontmatter(front, _sample(), kind="note", course="ETHICS")
    assert "reading/2026" in merged["tags"]
    assert merged["tags"].count("argus/note") == 1


def test_source_and_course_are_separate_paragraphs():
    """Consecutive lines fold into one paragraph under CommonMark, and the
    app's own viewer is react-markdown + remark-gfm with no remark-breaks. On
    consecutive lines the Course link rendered on the end of the Source line
    everywhere except Obsidian, which breaks single newlines by default."""
    section = relations.render_section(_sample())
    assert "\n\n**Course** — " in section
    source_line = next(line for line in section.splitlines() if line.startswith("**Source**"))
    # "**Course**", not "Course": the source path itself contains
    # "15-Courses", which made the first draft of this assertion fail
    # against correct output.
    assert "**Course**" not in source_line
