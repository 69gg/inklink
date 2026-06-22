from inklink.domain.index import EntityMention, StoryIndex


def test_last_mentioned_is_order_independent() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=80, generation=1, strength=3)]
    )
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=75, generation=1, strength=2)]
    )

    character = index.characters["c1"]

    assert character.first_mentioned_chapter == 75
    assert character.last_mentioned_chapter == 80


def test_repeated_merge_does_not_duplicate_related_chapters() -> None:
    mention = EntityMention(entity_id="c1", chapter_number=8, generation=1, strength=1)
    index = StoryIndex()
    index.upsert_mentions([mention])
    index.upsert_mentions([mention])

    assert index.characters["c1"].related_chapters == [8]


def test_upsert_replaces_same_identity_strength() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=8, generation=1, strength=1)]
    )
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=8, generation=1, strength=4)]
    )

    assert index.characters["c1"].active_score == 4


def test_abandon_generation_removes_fact_contribution() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [
            EntityMention(entity_id="c1", chapter_number=501, generation=1, strength=5),
            EntityMention(entity_id="c1", chapter_number=501, generation=2, strength=1),
        ]
    )

    index.abandon_generation(chapter_number=501, generation=1)
    character = index.characters["c1"]

    assert character.related_chapters == [501]
    assert character.active_score == 1


def test_abandon_generation_removes_character_when_no_active_facts_remain() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=12, generation=1, strength=2)]
    )

    index.abandon_generation(chapter_number=12, generation=1)

    assert "c1" not in index.characters
