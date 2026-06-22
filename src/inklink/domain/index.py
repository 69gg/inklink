from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    chapter_number: int = Field(gt=0)
    generation: int = Field(gt=0)
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
    first_mentioned_chapter: int = Field(gt=0)
    last_mentioned_chapter: int = Field(gt=0)
    active_score: int = Field(ge=0)
    related_chapters: list[int] = Field(default_factory=list)

    @field_validator("entity_id")
    @classmethod
    def validate_entity_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("entity_id must not be blank")
        return value


class StoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EntityMention] = Field(default_factory=list)
    abandoned_generations: set[tuple[int, int]] = Field(default_factory=set)
    characters: dict[str, CharacterIndexEntry] = Field(default_factory=dict)

    def upsert_mentions(self, mentions: list[EntityMention]) -> None:
        by_identity = {
            (mention.entity_id, mention.chapter_number, mention.generation): mention
            for mention in self.mentions
        }
        for mention in mentions:
            by_identity[(mention.entity_id, mention.chapter_number, mention.generation)] = mention
        self.mentions = list(by_identity.values())
        self.rebuild()

    def abandon_generation(self, chapter_number: int, generation: int) -> None:
        self.abandoned_generations.add((chapter_number, generation))
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
