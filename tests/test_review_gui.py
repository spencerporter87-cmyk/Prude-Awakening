import os
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from prude.gui.pages import ReviewPage
from prude.gui.style import QSS
from prude.media import MediaInfo
from prude.project import Project
from prude.settings import FilterOptions, Settings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(QSS)
    yield app


@pytest.fixture
def no_multimedia(monkeypatch):
    monkeypatch.setattr("prude.gui.player.HAVE_MM", False)
    monkeypatch.setattr("prude.gui.pages.HAVE_MM", False)


def make_review(qapp, project, filters, duration=20.0):
    info = MediaInfo(path=Path(project.source), duration=duration, format_start=0, streams=[],
                     video={"width": 640, "height": 360, "codec_name": "h264"},
                     audio=[], subtitles=[])
    page = ReviewPage()
    page.resize(1280, 720)
    page.set_project(project, info, Settings(), filters)
    page.show()
    qapp.processEvents()
    return page


def close(page, qapp):
    page.close()
    page.deleteLater()
    qapp.processEvents()


def scanned_empty(profile="Family night", words=("strong",), scenes=("nudity",), duration=20.0):
    f = FilterOptions(words=list(words), scenes=list(scenes), scene_action="cut")
    p = Project(source="/tmp/empty movie.mkv", profile=profile, scan_filters=asdict(f))
    return p, f


def test_scanned_empty_review_cards_and_footer(qapp, no_multimedia):
    project, filters = scanned_empty()
    page = make_review(qapp, project, filters)
    try:
        assert page.tabs.tabText(0) == "Words (0)"
        assert page.tabs.tabText(1) == "Scenes (0)"

        assert not page.words_empty.isHidden()
        assert page.words_empty_title.text() == "Scan finished: no words to silence"
        assert page.words_empty_sub.text() == (
            "The scan listened to all 0:00:20 and found nothing from Family night's word lists.")
        assert page.words_empty_link.text() == "Check the word lists in settings and scan again"
        assert page.table.isHidden()
        assert all(not w.isEnabled() for w in page.words_bulk)
        assert page.add_mute_btn.isEnabled()

        assert not page.scenes_empty.isHidden()
        assert page.scenes_empty_title.text() == "Scan finished: no scenes to remove"
        assert page.scenes_empty_sub.text() == (
            "The scan watched all 0:00:20 and found none of Family night's scene types.")
        assert page.scenes_empty_link.text() == "Check the scene options in settings and scan again"
        assert page.scene_scroll.isHidden()
        assert all(not w.isEnabled() for w in page.scenes_bulk)
        assert page.scene_paint_btn.isEnabled()
        assert page.add_scene_btn.isEnabled()

        assert page.summary.text() == (
            "Nothing to change yet: the scan found no words or scenes for Family night. "
            "Add a mute or a scene, paint a blur, or change settings and scan again.")
        assert not page.go.isEnabled()
        assert page.go.toolTip() == "Nothing to change yet: add a mute, a scene or a blur first."
        assert page.go.accessibleDescription() == "Nothing to change yet: add a mute, a scene or a blur first."
        assert page.steps.current == 3
    finally:
        close(page, qapp)


def test_empty_card_link_is_keyboard_reachable(qapp, no_multimedia):
    project, filters = scanned_empty(words=("strong",), scenes=())
    page = make_review(qapp, project, filters)
    try:
        calls = []
        page.back.connect(lambda: calls.append(1))
        page.words_empty_link.setFocus()
        qapp.processEvents()
        assert page.words_empty_link.hasFocus()
        QTest.keyClick(page.words_empty_link, Qt.Key_Space)
        qapp.processEvents()
        assert calls == [1]
    finally:
        close(page, qapp)


def test_words_not_scanned_copy(qapp, no_multimedia):
    f = FilterOptions(words=[], scenes=["nudity"], scene_action="cut")
    project = Project(source="/tmp/empty movie.mkv", profile="Family night", scan_filters=asdict(f))
    page = make_review(qapp, project, f)
    try:
        assert page.words_empty_title.text() == "Words weren't part of this scan"
        assert page.words_empty_sub.text() == "Turn on a word list in settings to look for some."
        assert page.words_empty_link.text() == "Check the word lists in settings and scan again"
        assert not page.scenes_empty.isHidden()
        assert page.scenes_empty_title.text() == "Scan finished: no scenes to remove"
    finally:
        close(page, qapp)


def test_scenes_not_scanned_keeps_existing_copy(qapp, no_multimedia):
    f = FilterOptions(words=["strong"], scenes=[], scene_descriptions="", scene_action="cut")
    project = Project(source="/tmp/empty movie.mkv", profile="Family night", scan_filters=asdict(f))
    page = make_review(qapp, project, f)
    try:
        assert not page.words_empty.isHidden()
        assert page.words_empty_title.text() == "Scan finished: no words to silence"
        assert page.scenes_empty.isHidden()
        assert not page.scene_scroll.isHidden()
        texts = [lab.text() for lab in page.scene_host.findChildren(QLabel)]
        assert "No scenes to remove. (Turn on scene options in settings to look for some.)" in texts
    finally:
        close(page, qapp)


def test_adding_and_removing_last_mute_leaves_and_returns_empty(qapp, no_multimedia):
    project, filters = scanned_empty()
    page = make_review(qapp, project, filters)
    try:
        project.mutes.append({"id": "m1", "start": 2.0, "end": 3.0, "words": "Yours",
                              "context": "", "category": "custom", "on": True, "manual": True})
        page._fill_words()
        page._update()
        qapp.processEvents()

        assert page.words_empty.isHidden()
        assert not page.table.isHidden()
        assert all(w.isEnabled() for w in page.words_bulk)
        assert page.summary.text() == "1 of 1 words will be silenced · picture copied untouched."
        assert page.go.isEnabled()
        assert not page.summary.text().startswith("Nothing to change yet")
        assert not page.scenes_empty.isHidden()          # still a partial result: only Words filled

        project.mutes.clear()
        page._fill_words()
        page._update()
        qapp.processEvents()

        assert not page.words_empty.isHidden()
        assert page.table.isHidden()
        assert page.summary.text().startswith("Nothing to change yet")
        assert not page.go.isEnabled()
        assert page.go.toolTip() == "Nothing to change yet: add a mute, a scene or a blur first."
    finally:
        close(page, qapp)


def test_adding_scene_leaves_scenes_empty(qapp, no_multimedia):
    project, filters = scanned_empty()
    page = make_review(qapp, project, filters)
    try:
        project.scenes.append({"id": "s1", "start": 2.0, "end": 4.0, "kind": "manual",
                               "label": "Added by you", "on": True, "manual": True})
        page._fill_scenes()
        page._update()
        qapp.processEvents()

        assert page.scenes_empty.isHidden()
        assert not page.scene_scroll.isHidden()
        assert all(w.isEnabled() for w in page.scenes_bulk)
        assert page.go.isEnabled()
        assert not page.summary.text().startswith("Nothing to change yet")
        assert not page.words_empty.isHidden()          # words tab is still empty
    finally:
        close(page, qapp)


def test_painted_blur_leaves_scenes_empty(qapp, no_multimedia):
    project, filters = scanned_empty()
    page = make_review(qapp, project, filters)
    try:
        project.paint.append({"id": "p1", "on": True, "label": "Painted blur", "start": 1.0, "end": 2.0,
                              "pad_before": 0.0, "pad_after": 0.0, "anchor": [0.5, 0.5],
                              "strokes": [{"r": 0.03, "pts": [[0.0, 0.0], [0.1, 0.1]]}],
                              "keys": [], "mode": "blur"})
        page._fill_scenes()
        page._update()
        qapp.processEvents()

        assert page.scenes_empty.isHidden()
        assert not page.scene_scroll.isHidden()
        assert all(w.isEnabled() for w in page.scenes_bulk)
        assert page.go.isEnabled()
        assert "1 painted blur" in page.summary.text()
    finally:
        close(page, qapp)


def test_populated_review_keeps_normal_state(qapp, no_multimedia):
    project, filters = scanned_empty()
    project.mutes.append({"id": "m1", "start": 2.0, "end": 3.0, "words": "Yours", "context": "",
                          "category": "custom", "on": True, "manual": True})
    project.scenes.append({"id": "s1", "start": 2.0, "end": 4.0, "kind": "manual",
                           "label": "Added by you", "on": True, "manual": True})
    page = make_review(qapp, project, filters)
    try:
        assert page.words_empty.isHidden()
        assert not page.table.isHidden()
        assert page.scenes_empty.isHidden()
        assert not page.scene_scroll.isHidden()
        assert page.summary.text().startswith("1 of 1 words will be silenced")
        assert "1 of 1 scenes" in page.summary.text()
        assert page.go.isEnabled()
        assert page.go.toolTip() == ""
        assert page.go.accessibleDescription() == ""
    finally:
        close(page, qapp)
