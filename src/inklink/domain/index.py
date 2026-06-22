from __future__ import annotations

from typing import Annotated, Any, Self

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
type GenerationIdentity = tuple[PositiveInt, PositiveInt]

_GENERATION_IDENTITY_ADAPTER: TypeAdapter[GenerationIdentity] = TypeAdapter(GenerationIdentity)


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    chapter_number: PositiveInt
    generation: PositiveInt
    strength: int = Field(ge=0)

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
    active_score: int = Field(ge=0)
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


class StoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EntityMention] = Field(default_factory=list)
    abandoned_generations: set[GenerationIdentity] = Field(default_factory=set)
    characters: dict[str, CharacterIndexEntry] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def ignore_input_characters(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "characters" not in data:
            return data
        return {key: value for key, value in data.items() if key != "characters"}

    @model_validator(mode="after")
    def normalize_and_rebuild(self) -> Self:
        self.mentions = _normalize_mentions(self.mentions)
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


def _mention_identity(mention: EntityMention) -> tuple[str, int, int]:
    return mention.entity_id, mention.chapter_number, mention.generation


def _normalize_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    by_identity = {_mention_identity(mention): mention for mention in mentions}
    return [by_identity[identity] for identity in sorted(by_identity)]
