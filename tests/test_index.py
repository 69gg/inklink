import pytest
from pydantic import ValidationError

from inklink.domain.index import (
    CharacterIndexEntry,
    CharacterStatus,
    EntityMention,
    StoryIndex,
    StructuredFact,
    facts_from_chapter_analysis,
)
from inklink.domain.models import PlotThreadStatus


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


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (EntityMention, {"entity_id": "c1", "chapter_number": 1, "generation": 1, "strength": 1}),
        (
            CharacterIndexEntry,
            {
                "entity_id": "c1",
                "first_mentioned_chapter": 1,
                "last_mentioned_chapter": 1,
                "active_score": 1,
                "related_chapters": [1],
            },
        ),
        (StoryIndex, {}),
    ],
)
def test_index_models_forbid_extra_fields(
    model: type[EntityMention] | type[CharacterIndexEntry] | type[StoryIndex],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "unexpected": "value"})


@pytest.mark.parametrize(
    "payload",
    [
        {"entity_id": "", "chapter_number": 1, "generation": 1, "strength": 1},
        {"entity_id": "   ", "chapter_number": 1, "generation": 1, "strength": 1},
        {"entity_id": "c1", "chapter_number": 0, "generation": 1, "strength": 1},
        {"entity_id": "c1", "chapter_number": 1, "generation": 0, "strength": 1},
        {"entity_id": "c1", "chapter_number": 1, "generation": 1, "strength": -1},
    ],
)
def test_entity_mention_rejects_invalid_basic_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EntityMention.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "entity_id": "",
            "first_mentioned_chapter": 1,
            "last_mentioned_chapter": 1,
            "active_score": 1,
            "related_chapters": [1],
        },
        {
            "entity_id": "c1",
            "first_mentioned_chapter": 0,
            "last_mentioned_chapter": 1,
            "active_score": 1,
            "related_chapters": [1],
        },
        {
            "entity_id": "c1",
            "first_mentioned_chapter": 1,
            "last_mentioned_chapter": 0,
            "active_score": 1,
            "related_chapters": [1],
        },
        {
            "entity_id": "c1",
            "first_mentioned_chapter": 1,
            "last_mentioned_chapter": 1,
            "active_score": -1,
            "related_chapters": [1],
        },
        {
            "entity_id": "c1",
            "first_mentioned_chapter": 1,
            "last_mentioned_chapter": 1,
            "active_score": 1,
            "related_chapters": [0],
        },
    ],
)
def test_character_entry_rejects_invalid_basic_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CharacterIndexEntry.model_validate(payload)


@pytest.mark.parametrize("coerced_value", ["1", 1.0, True])
@pytest.mark.parametrize("field_name", ["chapter_number", "generation"])
def test_entity_mention_rejects_coerced_identity_integers(
    field_name: str, coerced_value: object
) -> None:
    payload: dict[str, object] = {
        "entity_id": "c1",
        "chapter_number": 1,
        "generation": 1,
        "strength": 1,
        field_name: coerced_value,
    }

    with pytest.raises(ValidationError):
        EntityMention.model_validate(payload)


@pytest.mark.parametrize("coerced_value", ["2", 2.0, True])
def test_entity_mention_rejects_coerced_strength(coerced_value: object) -> None:
    with pytest.raises(ValidationError):
        EntityMention.model_validate(
            {
                "entity_id": "c1",
                "chapter_number": 1,
                "generation": 1,
                "strength": coerced_value,
            }
        )


@pytest.mark.parametrize("coerced_value", ["1", 1.0, True])
@pytest.mark.parametrize("field_name", ["first_mentioned_chapter", "last_mentioned_chapter"])
def test_character_entry_rejects_coerced_chapter_integers(
    field_name: str, coerced_value: object
) -> None:
    payload: dict[str, object] = {
        "entity_id": "c1",
        "first_mentioned_chapter": 1,
        "last_mentioned_chapter": 1,
        "active_score": 1,
        "related_chapters": [1],
        field_name: coerced_value,
    }

    with pytest.raises(ValidationError):
        CharacterIndexEntry.model_validate(payload)


@pytest.mark.parametrize("coerced_value", ["2", 2.0, True])
def test_character_entry_rejects_coerced_active_score(coerced_value: object) -> None:
    with pytest.raises(ValidationError):
        CharacterIndexEntry.model_validate(
            {
                "entity_id": "c1",
                "first_mentioned_chapter": 1,
                "last_mentioned_chapter": 1,
                "active_score": coerced_value,
                "related_chapters": [1],
            }
        )


@pytest.mark.parametrize("coerced_value", ["1", 1.0, True])
def test_character_entry_rejects_coerced_related_chapters(coerced_value: object) -> None:
    with pytest.raises(ValidationError):
        CharacterIndexEntry.model_validate(
            {
                "entity_id": "c1",
                "first_mentioned_chapter": 1,
                "last_mentioned_chapter": 1,
                "active_score": 1,
                "related_chapters": [coerced_value],
            }
        )


def test_character_entry_rejects_first_chapter_after_last_chapter() -> None:
    with pytest.raises(ValidationError):
        CharacterIndexEntry.model_validate(
            {
                "entity_id": "c1",
                "first_mentioned_chapter": 2,
                "last_mentioned_chapter": 1,
                "active_score": 1,
                "related_chapters": [1, 2],
            }
        )


def test_story_index_rebuilds_characters_from_mentions_on_initialization() -> None:
    index = StoryIndex(
        mentions=[
            EntityMention(entity_id="c1", chapter_number=3, generation=1, strength=2),
            EntityMention(entity_id="c1", chapter_number=4, generation=1, strength=5),
        ]
    )

    assert index.characters["c1"].active_score == 7
    assert index.characters["c1"].related_chapters == [3, 4]


def test_story_index_model_validate_ignores_stale_characters() -> None:
    index = StoryIndex.model_validate(
        {
            "mentions": [{"entity_id": "c1", "chapter_number": 8, "generation": 1, "strength": 3}],
            "characters": {
                "c1": {
                    "entity_id": "c1",
                    "first_mentioned_chapter": 1,
                    "last_mentioned_chapter": 1,
                    "active_score": 999,
                    "related_chapters": [1],
                },
                "stale": {
                    "entity_id": "stale",
                    "first_mentioned_chapter": 1,
                    "last_mentioned_chapter": 1,
                    "active_score": 1,
                    "related_chapters": [1],
                },
            },
        }
    )

    assert set(index.characters) == {"c1"}
    assert index.characters["c1"].active_score == 3
    assert index.characters["c1"].related_chapters == [8]


def test_story_index_model_validate_ignores_stale_typed_views() -> None:
    index = StoryIndex.model_validate(
        {
            "facts": [
                {
                    "fact_id": "thread-1:seed",
                    "kind": "plot_thread",
                    "text": "旧钥匙来历",
                    "chapter_number": 3,
                    "generation": 1,
                    "priority": 2,
                    "keywords": ["旧钥匙"],
                    "payload": {"thread_id": "thread-1", "source_chapter": 3},
                }
            ],
            "plot_threads": {
                "stale": {
                    "thread_id": "stale",
                    "description": "不应保留",
                    "status": "resolved",
                    "source_chapter": 1,
                    "reinforced_chapters": [],
                    "resolution_window": None,
                    "resolved_chapter": 1,
                    "related_entities": [],
                    "keywords": [],
                    "importance": 1,
                }
            },
            "events": {
                "stale-event": {
                    "event_id": "stale-event",
                    "description": "不应保留",
                    "chapter_number": 1,
                    "related_entities": [],
                    "keywords": [],
                    "importance": 1,
                }
            },
            "world_rules": {
                "stale-rule": {
                    "rule_id": "stale-rule",
                    "description": "不应保留",
                    "source_chapter": 1,
                    "related_entities": [],
                    "keywords": [],
                    "importance": 1,
                }
            },
            "keywords": {
                "stale": {
                    "keyword": "stale",
                    "kinds": ["event"],
                    "related_fact_ids": ["stale-event"],
                    "related_entities": [],
                    "related_chapters": [1],
                    "importance": 1,
                }
            },
        }
    )

    assert set(index.plot_threads) == {"thread-1"}
    assert index.events == {}
    assert index.world_rules == {}
    assert "stale" not in index.keywords


def test_story_index_model_validate_rebuilds_with_abandoned_generations() -> None:
    index = StoryIndex.model_validate(
        {
            "mentions": [
                {"entity_id": "c1", "chapter_number": 8, "generation": 1, "strength": 3},
                {"entity_id": "c1", "chapter_number": 8, "generation": 2, "strength": 4},
            ],
            "abandoned_generations": [[8, 1]],
        }
    )

    assert index.characters["c1"].active_score == 4


def test_abandoned_generations_reject_invalid_values() -> None:
    with pytest.raises(ValidationError):
        StoryIndex.model_validate({"abandoned_generations": [(0, -1)]})


def test_abandon_generation_rejects_invalid_values() -> None:
    index = StoryIndex()

    with pytest.raises(ValidationError):
        index.abandon_generation(0, -1)


@pytest.mark.parametrize("abandoned_generation", [("1", 1), (1.0, 1), (True, 1)])
def test_abandoned_generations_reject_coerced_values(
    abandoned_generation: tuple[object, int],
) -> None:
    with pytest.raises(ValidationError):
        StoryIndex.model_validate({"abandoned_generations": [abandoned_generation]})


@pytest.mark.parametrize("chapter_number", [True, "1", 1.0])
def test_abandon_generation_rejects_coerced_chapter_numbers(chapter_number: object) -> None:
    index = StoryIndex()

    with pytest.raises(ValidationError):
        index.abandon_generation(chapter_number, 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("generation", [True, "1", 1.0])
def test_abandon_generation_rejects_coerced_generations(generation: object) -> None:
    index = StoryIndex()

    with pytest.raises(ValidationError):
        index.abandon_generation(1, generation)  # type: ignore[arg-type]


def test_mentions_dump_order_is_stable_across_upsert_order() -> None:
    first = StoryIndex()
    first.upsert_mentions(
        [
            EntityMention(entity_id="c2", chapter_number=2, generation=1, strength=1),
            EntityMention(entity_id="c1", chapter_number=3, generation=2, strength=1),
            EntityMention(entity_id="c1", chapter_number=3, generation=1, strength=1),
        ]
    )

    second = StoryIndex()
    second.upsert_mentions(
        [
            EntityMention(entity_id="c1", chapter_number=3, generation=1, strength=1),
            EntityMention(entity_id="c2", chapter_number=2, generation=1, strength=1),
            EntityMention(entity_id="c1", chapter_number=3, generation=2, strength=1),
        ]
    )

    assert first.model_dump() == second.model_dump()
    assert [
        (mention["entity_id"], mention["chapter_number"], mention["generation"])
        for mention in first.model_dump()["mentions"]
    ] == [
        ("c1", 3, 1),
        ("c1", 3, 2),
        ("c2", 2, 1),
    ]


def test_story_index_json_round_trip_is_stable() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [
            EntityMention(entity_id="c2", chapter_number=2, generation=1, strength=1),
            EntityMention(entity_id="c1", chapter_number=3, generation=1, strength=4),
            EntityMention(entity_id="c1", chapter_number=3, generation=2, strength=2),
        ]
    )
    index.abandon_generation(chapter_number=3, generation=2)

    restored = StoryIndex.model_validate_json(index.model_dump_json())

    assert restored == index
    assert restored.model_dump() == index.model_dump()


def test_story_index_structured_fact_payload_round_trip_rebuilds_typed_views() -> None:
    index = StoryIndex(
        facts=[
            StructuredFact(
                fact_id="thread-1",
                kind="plot_thread",
                text="旧钥匙尚未解释",
                chapter_number=3,
                generation=1,
                priority=2,
                keywords=["旧钥匙"],
                payload={
                    "thread_id": "thread-1",
                    "description": "旧钥匙尚未解释",
                    "status": "reinforced",
                    "source_chapter": 1,
                    "due_chapter": 5,
                    "related_entities": ["林青"],
                },
            )
        ]
    )

    restored = StoryIndex.model_validate_json(index.model_dump_json())

    assert restored.plot_threads["thread-1"].status == PlotThreadStatus.REINFORCED
    assert restored.plot_threads["thread-1"].resolution_window is not None
    assert restored.plot_threads["thread-1"].resolution_window.end_chapter == 5


def test_story_index_structured_facts_are_generation_aware_and_retrievable() -> None:
    index = StoryIndex()
    index.upsert_facts(
        [
            StructuredFact(
                fact_id="thread-1",
                kind="plot_thread",
                text="旧钥匙尚未解释",
                chapter_number=5,
                generation=1,
                priority=2,
                keywords=["旧钥匙"],
            ),
            StructuredFact(
                fact_id="world-1",
                kind="worldbuilding",
                text="青灯会回应钥匙",
                chapter_number=6,
                generation=1,
                priority=4,
                keywords=["青灯", "旧钥匙"],
            ),
        ]
    )

    index.abandon_generation(chapter_number=5, generation=1)
    items = index.retrieval_items(keywords=["旧钥匙"])

    assert [item["text"] for item in items] == ["青灯会回应钥匙"]


def test_story_index_builds_typed_views_from_active_structured_facts() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [
            EntityMention(entity_id="林青", chapter_number=1, generation=1, strength=2),
            EntityMention(entity_id="林青", chapter_number=2, generation=1, strength=4),
        ]
    )
    index.upsert_facts(
        [
            StructuredFact(
                fact_id="character:lin-qing",
                kind="keyword",
                text="林青改用青衣客身份行动",
                chapter_number=2,
                generation=1,
                priority=3,
                keywords=["青衣客"],
                payload={
                    "entity_id": "林青",
                    "aliases": ["青衣客", "林青"],
                    "character_status": "active",
                },
            ),
            StructuredFact(
                fact_id="thread-1:seed",
                kind="plot_thread",
                text="旧钥匙来历",
                chapter_number=1,
                generation=1,
                priority=2,
                keywords=["旧钥匙"],
                payload={
                    "thread_id": "thread-1",
                    "description": "旧钥匙来历",
                    "status": "seeded",
                    "source_chapter": 1,
                    "resolution_start_chapter": 2,
                    "resolution_end_chapter": 4,
                    "related_entities": ["林青"],
                    "importance": 2,
                },
            ),
            StructuredFact(
                fact_id="thread-1:reinforce",
                kind="plot_thread",
                text="旧钥匙再次发烫",
                chapter_number=3,
                generation=1,
                priority=1,
                keywords=["旧钥匙", "青灯"],
                payload={
                    "thread_id": "thread-1",
                    "description": "旧钥匙开始回应青灯",
                    "status": "reinforced",
                    "source_chapter": 1,
                    "reinforced_chapters": [2, 3],
                    "related_entities": ["林青"],
                    "importance": 1,
                },
            ),
            StructuredFact(
                fact_id="world-1",
                kind="worldbuilding",
                text="青灯只回应旧钥匙",
                chapter_number=2,
                generation=1,
                priority=4,
                keywords=["青灯"],
                payload={
                    "description": "青灯只回应旧钥匙",
                    "source_chapter": 2,
                    "related_entities": ["林青"],
                    "importance": 4,
                },
            ),
            StructuredFact(
                fact_id="event-1",
                kind="event",
                text="门后传来陌生脚步",
                chapter_number=3,
                generation=1,
                priority=3,
                keywords=["门后"],
                payload={
                    "description": "门后传来陌生脚步",
                    "related_entities": ["林青"],
                    "importance": 3,
                },
            ),
        ]
    )

    character = index.characters["林青"]
    thread = index.plot_threads["thread-1"]

    assert character.aliases == ["林青", "青衣客"]
    assert character.status == CharacterStatus.ACTIVE
    assert character.active_score == 6
    assert thread.description == "旧钥匙开始回应青灯"
    assert thread.status == PlotThreadStatus.REINFORCED
    assert thread.source_chapter == 1
    assert thread.reinforced_chapters == [2, 3]
    assert thread.resolution_window is not None
    assert thread.resolution_window.start_chapter == 2
    assert thread.resolution_window.end_chapter == 4
    assert thread.related_entities == ["林青"]
    assert thread.keywords == ["旧钥匙", "旧钥匙再次发烫", "旧钥匙来历", "青灯"]
    assert index.world_rules["world-1"].description == "青灯只回应旧钥匙"
    assert index.events["event-1"].chapter_number == 3
    assert index.keywords["青灯"].related_fact_ids == ["thread-1:reinforce", "world-1"]


def test_story_index_typed_views_drop_abandoned_generation_contributions() -> None:
    index = StoryIndex()
    index.upsert_facts(
        [
            StructuredFact(
                fact_id="thread-1",
                kind="plot_thread",
                text="旧钥匙来历",
                chapter_number=5,
                generation=1,
                priority=2,
                keywords=["旧钥匙"],
                payload={
                    "thread_id": "thread-1",
                    "status": "resolved",
                    "resolved_chapter": 5,
                    "source_chapter": 1,
                },
            ),
            StructuredFact(
                fact_id="world-1",
                kind="worldbuilding",
                text="青灯会回应钥匙",
                chapter_number=5,
                generation=1,
                priority=4,
                keywords=["青灯"],
            ),
            StructuredFact(
                fact_id="thread-1",
                kind="plot_thread",
                text="旧钥匙仍未解释",
                chapter_number=5,
                generation=2,
                priority=2,
                keywords=["旧钥匙"],
                payload={
                    "thread_id": "thread-1",
                    "status": "reinforced",
                    "source_chapter": 1,
                },
            ),
        ]
    )

    index.abandon_generation(chapter_number=5, generation=1)
    thread = index.plot_threads["thread-1"]

    assert thread.status == PlotThreadStatus.REINFORCED
    assert thread.resolved_chapter is None
    assert "world-1" not in index.world_rules
    assert index.keywords["旧钥匙"].related_chapters == [5]


def test_facts_from_chapter_analysis_adds_compatible_typed_payloads() -> None:
    facts = facts_from_chapter_analysis(
        chapter_number=7,
        generation=2,
        worldbuilding=["青灯只在雨夜点亮"],
        plot_threads=["旧钥匙为何发烫"],
        suspense=["门后的人没有影子"],
    )
    index = StoryIndex(facts=facts)

    assert {fact.kind for fact in facts} == {"worldbuilding", "plot_thread", "event"}
    assert facts[0].payload["source_chapter"] == 7
    assert index.world_rules["worldbuilding:7:0"].source_chapter == 7
    assert index.plot_threads["plot_thread:7:0"].status == PlotThreadStatus.SEEDED
    assert index.events["event:7:0"].description == "门后的人没有影子"
