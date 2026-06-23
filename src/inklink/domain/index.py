from __future__ import annotations

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


class CharacterIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
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


class StructuredFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    kind: FactKind
    text: str = Field(min_length=1)
    chapter_number: PositiveInt
    generation: PositiveInt
    priority: PositiveInt = 5
    keywords: list[str] = Field(default_factory=list)

    @field_validator("fact_id", "text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip():
                raise ValueError("keyword must not be blank")
        return sorted(set(values))


class StoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EntityMention] = Field(default_factory=list)
    facts: list[StructuredFact] = Field(default_factory=list)
    abandoned_generations: set[GenerationIdentity] = Field(default_factory=set)
    characters: dict[str, CharacterIndexEntry] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def ignore_input_characters(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {key: value for key, value in data.items() if key != "characters"}

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
        grouped: dict[str, list[EntityMention]] = {}
        for mention in self._active_mentions():
            grouped.setdefault(mention.entity_id, []).append(mention)

        self.characters = {}
        for entity_id, mentions in grouped.items():
            chapters = sorted({mention.chapter_number for mention in mentions})
            self.characters[entity_id] = CharacterIndexEntry(
                entity_id=entity_id,
                first_mentioned_chapter=min(chapters),
                last_mentioned_chapter=max(chapters),
                active_score=sum(mention.strength for mention in mentions),
                related_chapters=chapters,
            )

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
