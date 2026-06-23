from __future__ import annotations

import unicodedata

from inklink.domain.models import (
    ChapterContract,
    CheckIssue,
    CheckReport,
    DraftChapter,
    PlotThread,
    PlotThreadStatus,
    SceneContract,
    SceneDraft,
)

CJK_UNIFIED_IDEOGRAPH_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x2EBF0, 0x2EE5F),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
)


def count_chinese_chars(text: str) -> int:
    return sum(1 for char in text if _is_cjk_unified_ideograph(char))


def _is_cjk_unified_ideograph(char: str) -> bool:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return False
    if not name.startswith("CJK UNIFIED IDEOGRAPH-"):
        return False
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in CJK_UNIFIED_IDEOGRAPH_RANGES)


def run_chapter_checks(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    plot_threads: list[PlotThread],
    scene_contracts: list[SceneContract] | None = None,
    scene_drafts: list[SceneDraft] | None = None,
    tolerance_ratio: float = 0.0,
    resolved_thread_ids: list[str] | None = None,
) -> CheckReport:
    issues: list[CheckIssue] = []
    _check_chapter_identity(contract=contract, draft=draft, issues=issues)
    _check_word_count(
        contract=contract, draft=draft, issues=issues, tolerance_ratio=tolerance_ratio
    )
    if scene_contracts is not None and scene_drafts is not None:
        _check_scene_word_counts(
            scene_contracts=scene_contracts,
            scene_drafts=scene_drafts,
            chapter_contract=contract,
            tolerance_ratio=tolerance_ratio,
            issues=issues,
        )
    _check_required_terms(contract=contract, draft=draft, issues=issues)
    _check_forbidden_terms(contract=contract, draft=draft, issues=issues)
    _check_repeated_plot_thread_resolution(
        current_chapter=contract.chapter_number,
        plot_threads=plot_threads,
        resolved_thread_ids=resolved_thread_ids or [],
        issues=issues,
    )
    return CheckReport(passed=not issues, issues=issues)


def _check_chapter_identity(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    issues: list[CheckIssue],
) -> None:
    if contract.chapter_number != draft.chapter_number:
        issues.append(
            CheckIssue(
                code="chapter_number_mismatch",
                message=(
                    f"Draft chapter {draft.chapter_number} does not match "
                    f"contract chapter {contract.chapter_number}."
                ),
            )
        )


def _check_word_count(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    issues: list[CheckIssue],
    tolerance_ratio: float,
) -> None:
    count = count_chinese_chars(draft.body)
    min_chars, max_chars = _range_with_tolerance(
        min_chars=contract.min_chars,
        max_chars=contract.max_chars,
        tolerance_ratio=tolerance_ratio,
    )
    if count < min_chars or count > max_chars:
        issues.append(
            CheckIssue(
                code="word_count_out_of_range",
                message=(
                    f"Chinese character count {count} is outside target range "
                    f"{min_chars}-{max_chars}."
                ),
            )
        )


def _check_scene_word_counts(
    *,
    scene_contracts: list[SceneContract],
    scene_drafts: list[SceneDraft],
    chapter_contract: ChapterContract,
    tolerance_ratio: float,
    issues: list[CheckIssue],
) -> None:
    drafts_by_id = {draft.scene_id: draft for draft in scene_drafts}
    total_min = 0
    total_max = 0
    for contract in scene_contracts:
        total_min += contract.min_chars
        total_max += contract.max_chars
        draft = drafts_by_id.get(contract.scene_id)
        if draft is None:
            continue
        count = count_chinese_chars(draft.text)
        min_chars, max_chars = _range_with_tolerance(
            min_chars=contract.min_chars,
            max_chars=contract.max_chars,
            tolerance_ratio=tolerance_ratio,
        )
        if count < min_chars or count > max_chars:
            issues.append(
                CheckIssue(
                    code="scene_word_count_out_of_range",
                    message=(
                        f"Scene {contract.scene_id} Chinese character count {count} is "
                        f"outside target range {min_chars}-{max_chars}."
                    ),
                )
            )
    chapter_min, chapter_max = _range_with_tolerance(
        min_chars=chapter_contract.min_chars,
        max_chars=chapter_contract.max_chars,
        tolerance_ratio=tolerance_ratio,
    )
    if total_max < chapter_min or total_min > chapter_max:
        issues.append(
            CheckIssue(
                code="scene_total_out_of_range",
                message=(
                    f"Scene target total {total_min}-{total_max} is outside chapter range "
                    f"{chapter_min}-{chapter_max}."
                ),
            )
        )


def _check_required_terms(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    issues: list[CheckIssue],
) -> None:
    for name in contract.required_characters:
        if name not in draft.body:
            issues.append(
                CheckIssue(
                    code="required_character_missing",
                    message=f"Required character {name} is missing.",
                )
            )
    for keyword in contract.required_keywords:
        if keyword not in draft.body:
            issues.append(
                CheckIssue(
                    code="required_keyword_missing",
                    message=f"Required keyword {keyword} is missing.",
                )
            )


def _check_forbidden_terms(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    issues: list[CheckIssue],
) -> None:
    for term in contract.forbidden:
        if term in draft.body:
            issues.append(
                CheckIssue(
                    code="forbidden_term_present",
                    message=f"Forbidden term {term} is present.",
                )
            )


def _check_repeated_plot_thread_resolution(
    *,
    current_chapter: int,
    plot_threads: list[PlotThread],
    resolved_thread_ids: list[str],
    issues: list[CheckIssue],
) -> None:
    repeated = {thread_id for thread_id in resolved_thread_ids if thread_id.strip()}
    for thread in _latest_plot_threads(plot_threads):
        if thread.thread_id in repeated and thread.status in {
            PlotThreadStatus.RESOLVED,
            PlotThreadStatus.ABANDONED,
        }:
            issues.append(
                CheckIssue(
                    code="plot_thread_repeated_resolution",
                    message=f"Plot thread {thread.thread_id} cannot be resolved again.",
                )
            )
        if (
            thread.due_chapter is not None
            and current_chapter > thread.due_chapter
            and thread.status not in {PlotThreadStatus.RESOLVED, PlotThreadStatus.ABANDONED}
        ):
            issues.append(
                CheckIssue(
                    code="plot_thread_overdue",
                    message=(
                        f"Plot thread {thread.thread_id} is overdue after chapter "
                        f"{thread.due_chapter}."
                    ),
                    severity="warning",
                )
            )


def _latest_plot_threads(plot_threads: list[PlotThread]) -> list[PlotThread]:
    by_thread_id: dict[str, PlotThread] = {}
    for thread in sorted(
        plot_threads,
        key=lambda item: (
            item.thread_id,
            _plot_thread_lifecycle_chapter(item),
            _plot_thread_status_rank(item.status),
            item.description,
        ),
    ):
        by_thread_id[thread.thread_id] = thread
    return [by_thread_id[thread_id] for thread_id in sorted(by_thread_id)]


def _plot_thread_status_rank(status: PlotThreadStatus) -> int:
    ranks: dict[PlotThreadStatus, int] = {
        PlotThreadStatus.SEEDED: 1,
        PlotThreadStatus.REINFORCED: 2,
        PlotThreadStatus.DUE: 3,
        PlotThreadStatus.RESOLVED: 4,
        PlotThreadStatus.ABANDONED: 5,
    }
    return ranks[status]


def _plot_thread_lifecycle_chapter(thread: PlotThread) -> int:
    return (
        thread.resolved_chapter
        or thread.abandoned_chapter
        or thread.due_chapter
        or thread.source_chapter
    )


def _range_with_tolerance(
    *,
    min_chars: int,
    max_chars: int,
    tolerance_ratio: float,
) -> tuple[int, int]:
    if tolerance_ratio < 0:
        raise ValueError("tolerance_ratio must be non-negative")
    return (
        max(0, int(min_chars * (1 - tolerance_ratio))),
        int(max_chars * (1 + tolerance_ratio)),
    )
