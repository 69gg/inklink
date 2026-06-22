import pytest
from pydantic import ValidationError

from inklink.domain.checks import count_chinese_chars, run_chapter_checks
from inklink.domain.models import ChapterContract, DraftChapter, PlotThread, PlotThreadStatus


def test_count_chinese_chars_ignores_latin_digits_and_punctuation() -> None:
    assert count_chinese_chars("他到了 Lv.10，笑了。") == 5


def test_count_chinese_chars_includes_cjk_extensions() -> None:
    assert count_chinese_chars("𠀀A1。") == 1


def test_count_chinese_chars_includes_extension_i() -> None:
    assert count_chinese_chars("\U0002ebf0A1。") == 1


def test_count_chinese_chars_ignores_unassigned_cjk_block_codepoints() -> None:
    assert count_chinese_chars("\U0002ee5e") == 0


def test_chapter_contract_rejects_invalid_ranges() -> None:
    with pytest.raises(ValidationError):
        ChapterContract(
            chapter_number=1,
            title="第一章",
            min_chars=10,
            max_chars=1,
            required_characters=[],
            required_keywords=[],
            scene_ids=[],
        )


def test_chapter_contract_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        ChapterContract(
            chapter_number=1,
            title=" ",
            min_chars=1,
            max_chars=10,
            required_characters=[],
            required_keywords=[],
            scene_ids=[],
        )


def test_chapter_contract_rejects_blank_required_terms() -> None:
    with pytest.raises(ValidationError):
        ChapterContract(
            chapter_number=1,
            title="第一章",
            min_chars=1,
            max_chars=10,
            required_characters=[" "],
            required_keywords=["玉佩"],
            scene_ids=[],
        )
    with pytest.raises(ValidationError):
        ChapterContract(
            chapter_number=1,
            title="第一章",
            min_chars=1,
            max_chars=10,
            required_characters=["林青"],
            required_keywords=[""],
            scene_ids=[],
        )


def test_draft_chapter_rejects_invalid_identity() -> None:
    with pytest.raises(ValidationError):
        DraftChapter(chapter_number=0, title="第一章", body="正文")
    with pytest.raises(ValidationError):
        DraftChapter(chapter_number=1, title=" ", body="正文")


def test_plot_thread_rejects_blank_identity_and_keywords() -> None:
    with pytest.raises(ValidationError):
        PlotThread(
            thread_id=" ",
            description="旧约定",
            status=PlotThreadStatus.SEEDED,
            source_chapter=1,
            due_chapter=2,
            related_keywords=["旧约定"],
        )
    with pytest.raises(ValidationError):
        PlotThread(
            thread_id="p1",
            description=" ",
            status=PlotThreadStatus.SEEDED,
            source_chapter=1,
            due_chapter=2,
            related_keywords=["旧约定"],
        )
    with pytest.raises(ValidationError):
        PlotThread(
            thread_id="p1",
            description="旧约定",
            status=PlotThreadStatus.SEEDED,
            source_chapter=1,
            due_chapter=2,
            related_keywords=[" "],
        )


def test_chapter_check_passes_when_contract_is_satisfied() -> None:
    contract = ChapterContract(
        chapter_number=1,
        title="第一章",
        min_chars=2,
        max_chars=10,
        required_characters=["林青"],
        required_keywords=["玉佩"],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=1, title="第一章", body="林青收起玉佩。")
    thread = PlotThread(
        thread_id="p1",
        description="玉佩来历",
        status=PlotThreadStatus.REINFORCED,
        source_chapter=1,
        due_chapter=10,
        related_keywords=["玉佩"],
    )

    report = run_chapter_checks(
        contract=contract,
        draft=draft,
        plot_threads=[thread],
        resolved_thread_ids=["p1"],
    )

    assert report.passed
    assert report.issues == []


def test_chapter_check_fails_when_chinese_count_is_out_of_range() -> None:
    short_contract = ChapterContract(
        chapter_number=2,
        title="第二章",
        min_chars=5,
        max_chars=10,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    long_contract = ChapterContract(
        chapter_number=2,
        title="第二章",
        min_chars=1,
        max_chars=2,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=2, title="第二章", body="他来了。")

    short_report = run_chapter_checks(contract=short_contract, draft=draft, plot_threads=[])
    long_report = run_chapter_checks(contract=long_contract, draft=draft, plot_threads=[])

    assert any(issue.code == "word_count_out_of_range" for issue in short_report.issues)
    assert any(issue.code == "word_count_out_of_range" for issue in long_report.issues)
    assert "5" in short_report.issues[0].message
    assert "10" in short_report.issues[0].message


def test_chapter_check_fails_when_required_name_missing() -> None:
    contract = ChapterContract(
        chapter_number=3,
        title="第三章",
        min_chars=5,
        max_chars=20,
        required_characters=["林青"],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=3, title="第三章", body="他走入山门。")

    report = run_chapter_checks(contract=contract, draft=draft, plot_threads=[])

    assert not report.passed
    assert any(issue.code == "required_character_missing" for issue in report.issues)


def test_chapter_check_fails_when_required_keyword_missing() -> None:
    contract = ChapterContract(
        chapter_number=4,
        title="第四章",
        min_chars=1,
        max_chars=20,
        required_characters=[],
        required_keywords=["玉佩"],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=4, title="第四章", body="他走入山门。")

    report = run_chapter_checks(contract=contract, draft=draft, plot_threads=[])

    assert not report.passed
    assert any(issue.code == "required_keyword_missing" for issue in report.issues)


def test_chapter_check_fails_when_contract_and_draft_chapter_mismatch() -> None:
    contract = ChapterContract(
        chapter_number=5,
        title="第五章",
        min_chars=1,
        max_chars=20,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=6, title="第六章", body="他走入山门。")

    report = run_chapter_checks(contract=contract, draft=draft, plot_threads=[])

    assert not report.passed
    assert any(issue.code == "chapter_number_mismatch" for issue in report.issues)


def test_resolved_plot_thread_cannot_be_resolved_again() -> None:
    contract = ChapterContract(
        chapter_number=10,
        title="第十章",
        min_chars=1,
        max_chars=100,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=10, title="第十章", body="主角回收旧伏笔。")
    thread = PlotThread(
        thread_id="p1",
        description="玉佩来历",
        status=PlotThreadStatus.RESOLVED,
        source_chapter=1,
        due_chapter=10,
        related_keywords=["玉佩"],
    )

    report = run_chapter_checks(
        contract=contract,
        draft=draft,
        plot_threads=[thread],
        resolved_thread_ids=["p1"],
    )

    assert not report.passed
    assert any(issue.code == "plot_thread_repeated_resolution" for issue in report.issues)


def test_abandoned_plot_thread_cannot_be_resolved_again() -> None:
    contract = ChapterContract(
        chapter_number=11,
        title="第十一章",
        min_chars=1,
        max_chars=100,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=11, title="第十一章", body="主角回收旧伏笔。")
    thread = PlotThread(
        thread_id="p2",
        description="旧约定",
        status=PlotThreadStatus.ABANDONED,
        source_chapter=2,
        due_chapter=11,
        related_keywords=["旧约定"],
    )

    report = run_chapter_checks(
        contract=contract,
        draft=draft,
        plot_threads=[thread],
        resolved_thread_ids=["p2"],
    )

    assert not report.passed
    assert any(issue.code == "plot_thread_repeated_resolution" for issue in report.issues)
