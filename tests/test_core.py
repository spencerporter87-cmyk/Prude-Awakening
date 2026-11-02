import subprocess
from pathlib import Path

import pytest

from conftest import level, video_md5
from prude import pipeline
from prude.media import ffmpeg, probe, pick_audio_track
from prude.project import Project
from prude.render import output_path
from prude.scenes import build_segments
from prude.settings import FilterOptions, Settings
from prude.subtitles import TimeMap, generate_cues, read_srt, shift_cues
from prude.words import build_patterns, censor_text, find_matches, merge_intervals


def test_word_matching(fake_words):
    pats = build_patterns(["mild", "blasphemy", "strong"])
    hits = merge_intervals(find_matches(fake_words, pats))
    found = [h["words"] for h in hits]
    assert found == ["hell?", "God damn", "shitty", "Oh my god."]
    assert all("class" not in f for f in found)


def test_categories_and_custom(fake_words):
    pats = build_patterns(["strong"], custom_text="hello")
    hits = find_matches(fake_words, pats)
    assert {h["words"] for h in hits} == {"shitty", "Hello,", "hello"}
    assert {h["category"] for h in hits} == {"strong", "custom"}


def test_allow_list(fake_words):
    pats = build_patterns(["mild"], allow_text="hell")
    assert not [h for h in find_matches(fake_words, pats, allow_text="hell") if "hell" in h["words"]]


def test_censor_text():
    pats = build_patterns(["strong", "blasphemy", "mild"])
    t, n = censor_text("<i>God damn it,</i> get the\nclass-A shit out!", pats)
    assert t == "<i>**** it,</i> get the\nclass-A **** out!" and n == 2


def test_timemap_and_shift():
    tm = TimeMap([(10, 20), (30, 35)])
    assert tm(5) == 5 and tm(15) is None and tm(25) == 15 and tm(40) == 25
    cues = [[1, 2, "a"], [12, 14, "gone"], [18, 22, "tail"], [29, 31, "head"]]
    out = shift_cues(cues, [(10, 20), (30, 35)])
    assert out[0] == [1, 2, "a"]
    assert [c[2] for c in out] == ["a", "tail", "head"]
    assert out[1][0] == pytest.approx(10) and out[1][1] == pytest.approx(12)


def test_generated_subs_star_only_listed(fake_words):
    pats = build_patterns(["mild", "blasphemy", "strong"])
    mutes = merge_intervals(find_matches(fake_words, pats))
    text = " ".join(c[2] for c in generate_cues(fake_words, mutes, pats))
    assert "what the ****?" in text and "**** **** it!" in text and "class" in text


def test_segments_snap_and_merge():
    times = list(range(0, 60))
    scores = [0.0] * 60
    for i in (20, 21, 22, 24):
        scores[i] = 0.9
    segs = build_segments(times, scores, 0.5, boundaries=[18.5, 30.5], duration=60)
    assert len(segs) == 1
    a, b, peak, idx = segs[0]
    assert a == pytest.approx(18.5) and 24 < b <= 30.5 and peak == pytest.approx(0.9)
    # single-frame blips are ignored when two hits are required
    scores2 = [0.0] * 60
    scores2[40] = 0.5
    assert build_segments(times, scores2, 0.45, [], 60, min_hits=2, strong=0.8) == []


def test_track_choice(media_dir):
    info = probe(media_dir / "movie51.mkv")
    assert pick_audio_track(info) == 0 and info.audio[0].layout.startswith("5.1")


def _project(path, words):
    p = Project.open(path)
    p.transcript = {"track": 0, "model": "fake", "words": words}
    return p


def test_render_mutes_copy_video(media_dir, fake_words, tmp_path):
    src = media_dir / "movie51.mkv"
    f = FilterOptions(words=["mild", "blasphemy", "strong"])
    s = Settings()
    p = _project(src, fake_words)
    pipeline.find_word_mutes(p, f)
    out = tmp_path / "clean.mkv"
    res = pipeline.render_project(p, f, s, out)
    assert res["how"] == "video copied untouched"
    assert video_md5(src) == video_md5(out)
    assert level(out, 5.2, pan="FL") < -90 and level(out, 5.2, pan="FC") < -90
    assert level(out, 7.0, pan="FL") > -40
    srts = list(tmp_path.glob("clean.en.srt"))
    assert srts and "what the ****?" in srts[0].read_text()
    info = probe(out)
    assert len(info.subtitles) == 2


def test_render_center_mode(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=["mild"], mute_mode="center")
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    out = tmp_path / "c.mkv"
    pipeline.render_project(p, f, Settings(), out)
    assert level(out, 5.2, pan="FC") < -90 and level(out, 5.2, pan="FL") > -40


def test_render_with_cut(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=["mild"], scene_action="cut")
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "x", "kind": "violence", "label": "Violence", "start": 8.0, "end": 12.0, "on": True}]
    out = tmp_path / "cut.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert "re-encoded" in res["how"]
    info = probe(out)
    assert info.duration == pytest.approx(16.0, abs=0.3)
    v = [s for s in info.streams if s["codec_type"] == "video"][0]
    assert float(v.get("duration") or info.duration) == pytest.approx(16.0, abs=0.3)
    # the 'hell' mute at ~5 s is unaffected; after the cut, 'shitty' muted region shifts by 4 s
    assert level(out, 5.2) < -90
    cues = read_srt(tmp_path / "cut.en.srt")
    assert all(not (8.0 < c[0] < 8.01) for c in cues)
    assert not any("damn" in c[2] for c in cues)


def test_render_blackout(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=[], scene_action="black")
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.scenes = [{"id": "x", "kind": "gore", "label": "Gore", "start": 2.0, "end": 4.0, "on": True}]
    out = tmp_path / "black.mkv"
    pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert probe(out).duration == pytest.approx(20.0, abs=0.3)
    assert level(out, 3.0) < -90 and level(out, 6.0) > -40
    r = subprocess.run([ffmpeg(), "-hide_banner", "-ss", "3", "-i", str(out), "-frames:v", "1",
                        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                       capture_output=True, text=True)
    yavg = float([l for l in r.stderr.splitlines() if "YAVG" in l][0].split("=")[-1])
    assert yavg < 20


def test_avi_to_mkv(media_dir, fake_words, tmp_path):
    src = media_dir / "old.avi"
    assert output_path(src).suffix == ".mkv"
    f = FilterOptions(words=["mild", "strong"])
    p = _project(src, fake_words)
    pipeline.find_word_mutes(p, f)
    out = tmp_path / "old (clean).mkv"
    pipeline.render_project(p, f, Settings(), out)
    assert level(out, 5.2) < -90 and video_md5(src) == video_md5(out)


def test_rescan_keeps_choices(media_dir, fake_words):
    f = FilterOptions(words=["mild", "blasphemy"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.mutes[0]["on"] = False
    p.mutes.append({"id": "m1", "start": 2.0, "end": 2.5, "words": "by hand", "on": True, "manual": True})
    f.words = ["mild", "blasphemy", "strong"]
    pipeline.find_word_mutes(p, f)
    hell = [m for m in p.mutes if m["words"] == "hell?"][0]
    assert hell["on"] is False
    assert any(m.get("manual") for m in p.mutes)
    assert any(m["words"] == "shitty" for m in p.mutes)


def test_cut_everything_is_refused(media_dir, fake_words, tmp_path):
    from prude.media import MediaError
    f = FilterOptions(words=["mild"], scene_action="cut")
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "x", "kind": "violence", "label": "V", "start": 0.0, "end": 20.0, "on": True}]
    with pytest.raises(MediaError):
        pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "all.mkv")


def test_cut_drops_empty_subtitles(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=["mild"], scene_action="cut")
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "x", "kind": "violence", "label": "V", "start": 0.5, "end": 19.0, "on": True}]
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "most.mkv")
    assert Path(res["output"]).exists()


def test_numpy_scene_times_save(media_dir, fake_words):
    import json
    import numpy as np
    times = np.arange(0, 60, dtype=np.float32) + np.float32(0.3)
    scores = np.zeros(60, dtype=np.float32)
    scores[30:33] = 0.9
    segs = build_segments(times, scores, 0.5, np.array([28.8], dtype=np.float32), 60.0)
    assert all(type(x) in (float, int) for s in segs for x in s)
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.scenes = [{"start": np.float32(1.5), "end": np.float64(2.5), "score": np.float32(0.7), "on": True}]
    p.save()
    assert json.loads((p.dir / "project.json").read_text())["scenes"][0]["start"] == 1.5
