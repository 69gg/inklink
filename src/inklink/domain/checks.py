from __future__ import annotations

from inklink.domain.models import (
    ChapterContract,
    CheckIssue,
    CheckReport,
    DraftChapter,
    PlotThread,
    PlotThreadStatus,
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
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in CJK_UNIFIED_IDEOGRAPH_RANGES)


def run_chapter_checks(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    plot_threads: list[PlotThread],
    resolved_thread_ids: list[str] | None = None,
) -> CheckReport:
    issues: list[CheckIssue] = []
    _check_chapter_identity(contract=contract, draft=draft, issues=issues)
    _check_word_count(contract=contract, draft=draft, issues=issues)
    _check_required_terms(contract=contract, draft=draft, issues=issues)
    _check_repeated_plot_thread_resolution(
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
) -> None:
    count = count_chinese_chars(draft.body)
    if count < contract.min_chars or count > contract.max_chars:
        issues.append(
            CheckIssue(
                code="word_count_out_of_range",
                message=(
                    f"Chinese character count {count} is outside target range "
                    f"{contract.min_chars}-{contract.max_chars}."
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


def _check_repeated_plot_thread_resolution(
    *,
    plot_threads: list[PlotThread],
    resolved_thread_ids: list[str],
    issues: list[CheckIssue],
) -> None:
    repeated = set(resolved_thread_ids)
    for thread in plot_threads:
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
