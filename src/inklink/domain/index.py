from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from inklink.domain.models import (
    AnalysisCharacterFact,
    AnalysisEventFact,
    AnalysisPlotThreadFact,
    AnalysisWorldRuleFact,
    PlotThreadStatus,
)

type PositiveInt = Annotated[int, Field(strict=True, gt=0)]
type NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
type GenerationIdentity = tuple[PositiveInt, PositiveInt]
FactKind = Literal["worldbuilding", "plot_thread", "keyword", "event"]

_GENERATION_IDENTITY_ADAPTER: TypeAdapter[GenerationIdentity] = TypeAdapter(GenerationIdentity)


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    chapter_number: PositiveInt
    generation: PositiveInt
    strength: NonNegativeInt

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entity_id must not be blank")
        return value


class CharacterStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    RESOLVED = "resolved"
    DEAD = "dead"
    UNKNOWN = "unknown"


class CharacterIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    status: CharacterStatus = CharacterStatus.UNKNOWN
    first_mentioned_chapter: PositiveInt
    last_mentioned_chapter: PositiveInt
    active_score: NonNegativeInt
    related_chapters: list[PositiveInt] = Field(default_factory=list)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entity_id must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="alias")

    @field_validator("related_chapters")
    @classmethod
    def normalize_related_chapters(cls, values: list[int]) -> list[int]:
        return sorted(set(values))

    @model_validator(mode="after")
    def validate_chapter_range(self) -> Self:
        if self.first_mentioned_chapter > self.last_mentioned_chapter:
            raise ValueError(
                "first_mentioned_chapter must be less than or equal to last_mentioned_chapter"
            )
        return self


class ResolutionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_chapter: PositiveInt
    end_chapter: PositiveInt

    @model_validator(mode="after")
    def validate_chapter_range(self) -> Self:
        if self.start_chapter > self.end_chapter:
            raise ValueError("start_chapter must be less than or equal to end_chapter")
        return self


class PlotThreadIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlotThreadStatus = PlotThreadStatus.SEEDED
    source_chapter: PositiveInt
    reinforced_chapters: list[PositiveInt] = Field(default_factory=list)
    resolution_window: ResolutionWindow | None = None
    resolved_chapter: PositiveInt | None = None
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: PositiveInt = 5

    @field_validator("thread_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value, field_name="value")

    @field_validator("reinforced_chapters")
    @classmethod
    def normalize_reinforced_chapters(cls, values: list[int]) -> list[int]:
        return sorted(set(values))

    @field_validator("related_entities")
    @classmethod
    def normalize_related_entities(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="related_entity")

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="keyword")


class EventIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    chapter_number: PositiveInt
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: PositiveInt = 5

    @field_validator("event_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value, field_name="value")

    @field_validator("related_entities")
    @classmethod
    def normalize_related_entities(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="related_entity")

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="keyword")


class WorldRuleIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_chapter: PositiveInt
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: PositiveInt = 5

    @field_validator("rule_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value, field_name="value")

    @field_validator("related_entities")
    @classmethod
    def normalize_related_entities(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="related_entity")

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="keyword")


class KeywordIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1)
    kinds: list[FactKind] = Field(default_factory=list)
    related_fact_ids: list[str] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)
    related_chapters: list[PositiveInt] = Field(default_factory=list)
    importance: PositiveInt = 5

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, value: str) -> str:
        return _validate_non_blank_string(value, field_name="keyword")

    @field_validator("kinds")
    @classmethod
    def normalize_kinds(cls, values: list[FactKind]) -> list[FactKind]:
        return sorted(set(values))

    @field_validator("related_fact_ids")
    @classmethod
    def normalize_related_fact_ids(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="related_fact_id")

    @field_validator("related_entities")
    @classmethod
    def normalize_related_entities(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="related_entity")

    @field_validator("related_chapters")
    @classmethod
    def normalize_related_chapters(cls, values: list[int]) -> list[int]:
        return sorted(set(values))


class StructuredFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    kind: FactKind
    text: str = Field(min_length=1)
    chapter_number: PositiveInt
    generation: PositiveInt
    priority: PositiveInt = 5
    keywords: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fact_id", "text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _validate_non_blank_string(value, field_name="value")

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalize_non_blank_strings(values, field_name="keyword")


class StoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EntityMention] = Field(default_factory=list)
    facts: list[StructuredFact] = Field(default_factory=list)
    abandoned_generations: set[GenerationIdentity] = Field(default_factory=set)
    characters: dict[str, CharacterIndexEntry] = Field(default_factory=dict)
    plot_threads: dict[str, PlotThreadIndexEntry] = Field(default_factory=dict)
    events: dict[str, EventIndexEntry] = Field(default_factory=dict)
    world_rules: dict[str, WorldRuleIndexEntry] = Field(default_factory=dict)
    keywords: dict[str, KeywordIndexEntry] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def ignore_input_views(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ignored_keys = {"characters", "plot_threads", "events", "world_rules", "keywords"}
        return {key: value for key, value in data.items() if key not in ignored_keys}

    @model_validator(mode="after")
    def normalize_and_rebuild(self) -> Self:
        self.mentions = _normalize_mentions(self.mentions)
        self.facts = _normalize_facts(self.facts)
        self.rebuild()
        return self

    @field_serializer("abandoned_generations")
    def serialize_abandoned_generations(
        self, abandoned_generations: set[GenerationIdentity]
    ) -> list[GenerationIdentity]:
        return sorted(abandoned_generations)

    def upsert_mentions(self, mentions: list[EntityMention]) -> None:
        self.mentions = _normalize_mentions([*self.mentions, *mentions])
        self.rebuild()

    def upsert_facts(self, facts: list[StructuredFact]) -> None:
        self.facts = _normalize_facts([*self.facts, *facts])
        self.rebuild()

    def abandon_generation(self, chapter_number: int, generation: int) -> None:
        generation_identity = _GENERATION_IDENTITY_ADAPTER.validate_python(
            (chapter_number, generation)
        )
        self.abandoned_generations.add(generation_identity)
        self.rebuild()

    def rebuild(self) -> None:
        active_facts = self.active_facts()
        grouped: dict[str, list[EntityMention]] = {}
        for mention in self._active_mentions():
            grouped.setdefault(mention.entity_id, []).append(mention)

        self.characters = {}
        for entity_id, mentions in grouped.items():
            character_facts = [
                fact
                for fact in active_facts
                if entity_id in _fact_related_entities(fact)
                or _fact_character_entity_id(fact) == entity_id
            ]
            chapters = sorted({mention.chapter_number for mention in mentions})
            self.characters[entity_id] = CharacterIndexEntry(
                entity_id=entity_id,
                aliases=[
                    alias
                    for fact in character_facts
                    for alias in _fact_aliases(fact, entity_id=entity_id)
                ],
                status=_character_status_from_facts(character_facts),
                first_mentioned_chapter=min(chapters),
                last_mentioned_chapter=max(chapters),
                active_score=sum(mention.strength for mention in mentions),
                related_chapters=chapters,
            )
        self.plot_threads = _build_plot_threads(active_facts)
        self.events = _build_events(active_facts)
        self.world_rules = _build_world_rules(active_facts)
        self.keywords = _build_keywords(active_facts)

    def _active_mentions(self) -> list[EntityMention]:
        return [
            mention
            for mention in self.mentions
            if (mention.chapter_number, mention.generation) not in self.abandoned_generations
        ]

    def active_facts(self) -> list[StructuredFact]:
        return [
            fact
            for fact in self.facts
            if (fact.chapter_number, fact.generation) not in self.abandoned_generations
        ]

    def retrieval_items(
        self,
        *,
        keywords: list[str] | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, object]]:
        keyword_set = {keyword for keyword in keywords or [] if keyword.strip()}
        items: list[dict[str, object]] = []
        for character in self.characters.values():
            if keyword_set and character.entity_id not in keyword_set:
                continue
            items.append(
                {
                    "priority": 3,
                    "kind": "character",
                    "text": (
                        f"{character.entity_id}: first={character.first_mentioned_chapter}, "
                        f"last={character.last_mentioned_chapter}, "
                        f"score={character.active_score}"
                    ),
                }
            )
        for fact in self.active_facts():
            if keyword_set and not keyword_set.intersection({fact.text, *fact.keywords}):
                continue
            items.append(
                {
                    "priority": fact.priority,
                    "kind": fact.kind,
                    "text": fact.text,
                    "chapter_number": fact.chapter_number,
                }
            )
        items.sort(key=_retrieval_item_sort_key)
        if max_items is None:
            return items
        return items[:max_items]


def facts_from_chapter_analysis(
    *,
    chapter_number: int,
    generation: int,
    worldbuilding: list[str],
    plot_threads: list[str],
    suspense: list[str],
    character_facts: list[AnalysisCharacterFact] | None = None,
    worldbuilding_facts: list[AnalysisWorldRuleFact] | None = None,
    plot_thread_facts: list[AnalysisPlotThreadFact] | None = None,
    event_facts: list[AnalysisEventFact] | None = None,
) -> list[StructuredFact]:
    facts: list[StructuredFact] = []
    for character in character_facts or []:
        facts.append(
            StructuredFact(
                fact_id=f"character:{chapter_number}:{character.entity_id}",
                kind="keyword",
                text=character.entity_id,
                chapter_number=chapter_number,
                generation=generation,
                priority=3,
                keywords=[character.entity_id, *character.aliases],
                payload={
                    "entity_id": character.entity_id,
                    "aliases": character.aliases,
                    "character_status": character.status,
                    "traits": character.traits,
                    "relationships": character.relationships,
                },
            )
        )
    for offset, world_text in enumerate(worldbuilding):
        facts.append(
            StructuredFact(
                fact_id=f"worldbuilding:{chapter_number}:{offset}",
                kind="worldbuilding",
                text=world_text,
                chapter_number=chapter_number,
                generation=generation,
                priority=4,
                keywords=_keywords_from_text(world_text),
                payload={
                    "description": world_text,
                    "source_chapter": chapter_number,
                    "importance": 4,
                },
            )
        )
    for world_fact in worldbuilding_facts or []:
        facts.append(
            StructuredFact(
                fact_id=world_fact.rule_id,
                kind="worldbuilding",
                text=world_fact.description,
                chapter_number=chapter_number,
                generation=generation,
                priority=world_fact.importance,
                keywords=world_fact.keywords,
                payload={
                    "description": world_fact.description,
                    "source_chapter": chapter_number,
                    "related_entities": world_fact.related_entities,
                    "importance": world_fact.importance,
                },
            )
        )
    for offset, thread_text in enumerate(plot_threads):
        facts.append(
            StructuredFact(
                fact_id=f"plot_thread:{chapter_number}:{offset}",
                kind="plot_thread",
                text=thread_text,
                chapter_number=chapter_number,
                generation=generation,
                priority=2,
                keywords=_keywords_from_text(thread_text),
                payload={
                    "thread_id": f"plot_thread:{chapter_number}:{offset}",
                    "description": thread_text,
                    "status": PlotThreadStatus.SEEDED.value,
                    "source_chapter": chapter_number,
                    "reinforced_chapters": [],
                    "importance": 2,
                },
            )
        )
    for thread_fact in plot_thread_facts or []:
        source_chapter = thread_fact.source_chapter or chapter_number
        facts.append(
            StructuredFact(
                fact_id=thread_fact.thread_id,
                kind="plot_thread",
                text=thread_fact.description,
                chapter_number=chapter_number,
                generation=generation,
                priority=thread_fact.importance,
                keywords=thread_fact.keywords,
                payload={
                    "thread_id": thread_fact.thread_id,
                    "description": thread_fact.description,
                    "status": thread_fact.status.value,
                    "source_chapter": source_chapter,
                    "due_chapter": thread_fact.due_chapter,
                    "resolved_chapter": thread_fact.resolved_chapter,
                    "reinforced_chapters": thread_fact.reinforced_chapters,
                    "related_entities": thread_fact.related_entities,
                    "importance": thread_fact.importance,
                },
            )
        )
    for offset, suspense_item in enumerate(suspense):
        facts.append(
            StructuredFact(
                fact_id=f"event:{chapter_number}:{offset}",
                kind="event",
                text=suspense_item,
                chapter_number=chapter_number,
                generation=generation,
                priority=3,
                keywords=_keywords_from_text(suspense_item),
                payload={
                    "description": suspense_item,
                    "source_chapter": chapter_number,
                    "importance": 3,
                },
            )
        )
    for event in event_facts or []:
        facts.append(
            StructuredFact(
                fact_id=event.event_id,
                kind="event",
                text=event.description,
                chapter_number=chapter_number,
                generation=generation,
                priority=event.importance,
                keywords=event.keywords,
                payload={
                    "description": event.description,
                    "source_chapter": chapter_number,
                    "related_entities": event.related_entities,
                    "importance": event.importance,
                },
            )
        )
    return facts


def _mention_identity(mention: EntityMention) -> tuple[str, int, int]:
    return mention.entity_id, mention.chapter_number, mention.generation


def _fact_identity(fact: StructuredFact) -> tuple[str, int, int]:
    return fact.fact_id, fact.chapter_number, fact.generation


def _normalize_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    by_identity = {_mention_identity(mention): mention for mention in mentions}
    return [by_identity[identity] for identity in sorted(by_identity)]


def _normalize_facts(facts: list[StructuredFact]) -> list[StructuredFact]:
    by_identity = {_fact_identity(fact): fact for fact in facts}
    return [by_identity[identity] for identity in sorted(by_identity)]


def _retrieval_item_sort_key(item: dict[str, object]) -> tuple[int, str, str]:
    priority = item.get("priority")
    return (
        priority if isinstance(priority, int) and not isinstance(priority, bool) else 999,
        str(item.get("kind", "")),
        str(item.get("text", "")),
    )


def _validate_non_blank_string(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _normalize_non_blank_strings(values: list[str], *, field_name: str) -> list[str]:
    for value in values:
        _validate_non_blank_string(value, field_name=field_name)
    return sorted(set(values))


def _payload_string(fact: StructuredFact, key: str) -> str | None:
    value = fact.payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _payload_positive_int(fact: StructuredFact, key: str) -> int | None:
    value = fact.payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _payload_string_list(fact: StructuredFact, key: str) -> list[str]:
    value = fact.payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _payload_positive_int_list(fact: StructuredFact, key: str) -> list[int]:
    value = fact.payload.get(key)
    if not isinstance(value, list):
        return []
    return [
        item for item in value if isinstance(item, int) and not isinstance(item, bool) and item > 0
    ]


def _fact_related_entities(fact: StructuredFact) -> list[str]:
    return _payload_string_list(fact, "related_entities")


def _fact_aliases(fact: StructuredFact, *, entity_id: str) -> list[str]:
    aliases = _payload_string_list(fact, "aliases")
    if entity_id != _payload_string(fact, "entity_id"):
        return []
    return aliases


def _fact_character_entity_id(fact: StructuredFact) -> str | None:
    return _payload_string(fact, "entity_id")


def _character_status_from_facts(facts: list[StructuredFact]) -> CharacterStatus:
    status_values: list[CharacterStatus] = []
    for fact in sorted(
        facts, key=lambda item: (item.chapter_number, item.generation, item.fact_id)
    ):
        status_value = _payload_string(fact, "character_status")
        if status_value is None:
            continue
        try:
            status_values.append(CharacterStatus(status_value))
        except ValueError:
            continue
    return status_values[-1] if status_values else CharacterStatus.UNKNOWN


def _build_plot_threads(active_facts: list[StructuredFact]) -> dict[str, PlotThreadIndexEntry]:
    facts = [fact for fact in active_facts if fact.kind == "plot_thread"]
    grouped: dict[str, list[StructuredFact]] = {}
    for fact in facts:
        grouped.setdefault(_plot_thread_id(fact), []).append(fact)

    plot_threads: dict[str, PlotThreadIndexEntry] = {}
    for thread_id in sorted(grouped):
        thread_facts = sorted(
            grouped[thread_id],
            key=lambda fact: (fact.chapter_number, fact.generation, fact.fact_id),
        )
        entry = _plot_thread_entry(thread_id=thread_id, facts=thread_facts)
        plot_threads[thread_id] = entry
    return plot_threads


def _plot_thread_id(fact: StructuredFact) -> str:
    return _payload_string(fact, "thread_id") or fact.fact_id


def _plot_thread_entry(
    *,
    thread_id: str,
    facts: list[StructuredFact],
) -> PlotThreadIndexEntry:
    source_fact = facts[0]
    latest_fact = facts[-1]
    source_chapter = (
        _payload_positive_int(source_fact, "source_chapter") or source_fact.chapter_number
    )
    status = _plot_thread_status(latest_fact)
    resolved_chapter = _payload_positive_int(latest_fact, "resolved_chapter")
    if resolved_chapter is None and status == PlotThreadStatus.RESOLVED:
        resolved_chapter = latest_fact.chapter_number
    reinforced_chapters = [
        chapter
        for fact in facts
        for chapter in [
            fact.chapter_number,
            *_payload_positive_int_list(fact, "reinforced_chapters"),
        ]
        if chapter != source_chapter
    ]

    return PlotThreadIndexEntry(
        thread_id=thread_id,
        description=_payload_string(latest_fact, "description") or latest_fact.text,
        status=status,
        source_chapter=source_chapter,
        reinforced_chapters=reinforced_chapters,
        resolution_window=_resolution_window_from_facts(facts),
        resolved_chapter=resolved_chapter,
        related_entities=[entity for fact in facts for entity in _fact_related_entities(fact)],
        keywords=[
            keyword
            for fact in facts
            for keyword in [*fact.keywords, *_keywords_from_text(fact.text)]
        ],
        importance=_payload_positive_int(latest_fact, "importance") or latest_fact.priority,
    )


def _plot_thread_status(fact: StructuredFact) -> PlotThreadStatus:
    status_value = _payload_string(fact, "status")
    if status_value is None:
        return PlotThreadStatus.SEEDED
    try:
        return PlotThreadStatus(status_value)
    except ValueError:
        return PlotThreadStatus.SEEDED


def _resolution_window(fact: StructuredFact) -> ResolutionWindow | None:
    start = _payload_positive_int(fact, "resolution_start_chapter")
    end = _payload_positive_int(fact, "resolution_end_chapter")
    if start is None:
        start = _payload_positive_int(fact, "source_chapter") or fact.chapter_number
    if end is None:
        end = _payload_positive_int(fact, "due_chapter")
    if end is None:
        return None
    if start > end:
        start = end
    return ResolutionWindow(start_chapter=start, end_chapter=end)


def _resolution_window_from_facts(facts: list[StructuredFact]) -> ResolutionWindow | None:
    for fact in reversed(facts):
        window = _resolution_window(fact)
        if window is not None:
            return window
    return None


def _build_events(active_facts: list[StructuredFact]) -> dict[str, EventIndexEntry]:
    events: dict[str, EventIndexEntry] = {}
    for fact in sorted(
        (fact for fact in active_facts if fact.kind == "event"),
        key=lambda item: (item.fact_id, item.chapter_number, item.generation),
    ):
        events[fact.fact_id] = EventIndexEntry(
            event_id=fact.fact_id,
            description=_payload_string(fact, "description") or fact.text,
            chapter_number=fact.chapter_number,
            related_entities=_fact_related_entities(fact),
            keywords=[*fact.keywords, *_keywords_from_text(fact.text)],
            importance=_payload_positive_int(fact, "importance") or fact.priority,
        )
    return events


def _build_world_rules(active_facts: list[StructuredFact]) -> dict[str, WorldRuleIndexEntry]:
    world_rules: dict[str, WorldRuleIndexEntry] = {}
    for fact in sorted(
        (fact for fact in active_facts if fact.kind == "worldbuilding"),
        key=lambda item: (item.fact_id, item.chapter_number, item.generation),
    ):
        world_rules[fact.fact_id] = WorldRuleIndexEntry(
            rule_id=fact.fact_id,
            description=_payload_string(fact, "description") or fact.text,
            source_chapter=_payload_positive_int(fact, "source_chapter") or fact.chapter_number,
            related_entities=_fact_related_entities(fact),
            keywords=[*fact.keywords, *_keywords_from_text(fact.text)],
            importance=_payload_positive_int(fact, "importance") or fact.priority,
        )
    return world_rules


def _build_keywords(active_facts: list[StructuredFact]) -> dict[str, KeywordIndexEntry]:
    grouped: dict[str, list[StructuredFact]] = {}
    for fact in active_facts:
        for keyword in [*fact.keywords, *_keywords_from_text(fact.text)]:
            grouped.setdefault(keyword, []).append(fact)

    keywords: dict[str, KeywordIndexEntry] = {}
    for keyword in sorted(grouped):
        facts = sorted(
            grouped[keyword],
            key=lambda item: (item.priority, item.kind, item.fact_id, item.chapter_number),
        )
        keywords[keyword] = KeywordIndexEntry(
            keyword=keyword,
            kinds=[fact.kind for fact in facts],
            related_fact_ids=[fact.fact_id for fact in facts],
            related_entities=[entity for fact in facts for entity in _fact_related_entities(fact)],
            related_chapters=[fact.chapter_number for fact in facts],
            importance=min(fact.priority for fact in facts),
        )
    return keywords


def _keywords_from_text(text: str) -> list[str]:
    keyword = text.strip()
    if not keyword:
        return []
    return [keyword]
