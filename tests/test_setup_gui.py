import os
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from prude.gui.pages import SetupPage
from prude.gui.style import QSS
from prude.media import AudioTrack, MediaInfo
from prude.project import Project
from prude.settings import BUILTIN_PRESETS, Settings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(QSS)
    yield app


def make_setup(qapp, settings=None, width=1280, height=720):
    settings = settings or Settings()
    project = Project(source="/tmp/example movie.mkv", dialogue="auto")
    audio = AudioTrack(number=0, index=1, codec="ac3", layout="5.1", channels=6,
                       language="eng", title="", default=True, extra=False)
    info = MediaInfo(path=Path(project.source), duration=3600, format_start=0, streams=[],
                     video={"width": 1920, "height": 1080, "codec_name": "h264"},
                     audio=[audio], subtitles=[])
    page = SetupPage()
    page.resize(width, height)
    page.set_movie(info, project, settings)
    page.show()
    qapp.processEvents()
    return page, settings


def close(page, qapp):
    page.close()
    page.deleteLater()
    qapp.processEvents()


def test_default_setup_matches_spec_and_fits(qapp):
    page, _ = make_setup(qapp)
    try:
        assert page._selected_preset == "Family night"
        assert page.summary.text() == (
            "Silences: strong language, God's name in vain, sexual words, slurs. "
            "Removes: nudity (4 types), sex scenes (2 types) by cutting them out."
        )
        assert page.scan_btn.isEnabled()
        assert not page.more_lang.is_expanded()
        assert not page.more_scenes.is_expanded()
        assert not page.adv.is_expanded()
        assert page._more_presets_btn is None
        assert page.scroll.verticalScrollBar().maximum() == 0
        assert not page.scroll.horizontalScrollBar().isVisible()
        assert page.steps.current == 1
    finally:
        close(page, qapp)


def test_preset_rematch_after_returning_to_preset_values(qapp):
    page, _ = make_setup(qapp)
    try:
        page._choose_preset("Kids")
        qapp.processEvents()
        assert page._selected_preset == "Kids"
        assert page.groups["nudity"].sensitivity() == 4

        page.pad.setValue(30)
        qapp.processEvents()
        assert page._selected_preset == "Custom"
        assert page._custom_card.isVisible()

        page.pad.setValue(15)
        qapp.processEvents()
        assert page._selected_preset == "Kids"
        assert not page._custom_card.isVisible()

        page._choose_preset("Family night")
        page.cats["strong"].set_all(False)
        qapp.processEvents()
        assert page._selected_preset == "Custom"
        page.cats["strong"].set_all(True)
        qapp.processEvents()
        assert page._selected_preset == "Family night"
    finally:
        close(page, qapp)


def test_disclosure_state_persists_across_settings_load(qapp):
    page, settings = make_setup(qapp)
    try:
        page.more_scenes.set_expanded(True)
        qapp.processEvents()
        settings.save()
        reloaded = Settings.load()
    finally:
        close(page, qapp)

    assert "scenes" in reloaded.setup_expanded
    page2, _ = make_setup(qapp, settings=reloaded)
    try:
        assert page2.more_scenes.is_expanded()
        assert not page2.more_lang.is_expanded()
    finally:
        close(page2, qapp)


def test_scan_gating_reason(qapp):
    page, _ = make_setup(qapp)
    try:
        page._choose_preset("Language only")
        page._all_words(False)
        qapp.processEvents()
        assert not page.scan_btn.isEnabled()
        assert page.scan_reason.text() == "Pick at least one word list or scene type to scan for."

        page.cats["strong"].set_all(True)
        qapp.processEvents()
        assert page.scan_btn.isEnabled()
        assert not page.scan_reason.isVisible()
    finally:
        close(page, qapp)


def test_more_than_eight_presets_use_menu(qapp):
    settings = Settings()
    settings.presets = {f"Saved {i}": asdict(BUILTIN_PRESETS["Language only"]) for i in range(5)}
    page, _ = make_setup(qapp, settings=settings)
    try:
        assert page._overflow_presets == ["Saved 4"]
        assert page._more_presets_btn is not None
        assert page._more_presets_btn.isVisible()
        assert "Saved 4" not in page._preset_cards
    finally:
        close(page, qapp)


def test_small_window_stacks_cards_without_horizontal_scroll(qapp):
    page, _ = make_setup(qapp, width=900, height=640)
    try:
        qapp.processEvents()
        assert page._cols_vertical
        assert page.cols_v.isVisible()
        assert not page.cols_h.isVisible()
        assert not page.scroll.horizontalScrollBar().isVisible()
        assert page.scroll.verticalScrollBar().isVisible()
    finally:
        close(page, qapp)


def test_expanded_word_panel_stacks_cards_without_horizontal_scroll(qapp):
    page, _ = make_setup(qapp)
    try:
        page.cats["slurs"].toggle()
        qapp.processEvents()
        qapp.processEvents()
        assert page._cols_vertical
        assert page.scroll.horizontalScrollBar().maximum() == 0

        page.cats["slurs"].toggle()
        qapp.processEvents()
        qapp.processEvents()
        assert not page._cols_vertical
        assert page.scroll.horizontalScrollBar().maximum() == 0
    finally:
        close(page, qapp)
