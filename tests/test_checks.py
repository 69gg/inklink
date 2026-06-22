from inklink.domain.checks import count_chinese_chars, run_chapter_checks
from inklink.domain.models import ChapterContract, DraftChapter, PlotThread, PlotThreadStatus


def test_count_chinese_chars_ignores_latin_digits_and_punctuation() -> None:
    assert count_chinese_chars("他到了 Lv.10，笑了。") == 5


def test_count_chinese_chars_includes_cjk_extensions() -> None:
    assert count_chinese_chars("𠀀A1。") == 1


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
