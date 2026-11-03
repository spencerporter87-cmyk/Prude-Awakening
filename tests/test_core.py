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


def test_word_groups_can_be_switched_off():
    from prude.words import category_groups
    labels = [g[0] for g in category_groups("strong")]
    assert "shit" in labels and "bullshit" in labels
    words = [{"w": w, "s": i, "e": i + 0.4, "p": 1} for i, w in
             enumerate(["oh", "shit", "that's", "bullshit", "you", "shithead"])]
    pats = build_patterns(["strong"], excluded=["strong:shit"])
    found = {h["words"] for h in find_matches(words, pats)}
    assert found == {"bullshit"}
    pats = build_patterns(["strong"], excluded=["strong:bullshit"])
    assert {h["words"] for h in find_matches(words, pats)} == {"shit", "shithead"}


def test_every_group_parses():
    from prude.words import CATEGORIES, category_groups
    for key, _n, _d in CATEGORIES:
        groups = category_groups(key)
        assert groups and all(label and pats for label, pats in groups)


# ---------------------------------------------------------------- granular scenes
def _fake_models(monkeypatch, nude_rows, clip):
    """nude_rows: {frame_index: {CLASS: score}}, clip: {key: {frame_index: prob}}"""
    import numpy as np
    from prude import scenes as sm

    class FakeNude:
        def scores(self, path):
            i = int(Path(path).stem) - 1
            row = nude_rows.get(i, {})
            return [row.get(c, 0.0) for c in sm.NUDE_CLASS_ORDER]

    class FakeClip:
        def __init__(self, *_a):
            pass

        def embed_images(self, paths):
            return np.zeros((len(paths), 4), dtype=np.float32)

    def fake_describe(embs, scorer, keys, custom):
        n = len(embs)
        out = {}
        for k in list(keys) + [f"custom:{c}" for c in custom]:
            arr = np.zeros(n, dtype=np.float32)
            for i, v in clip.get(k, {}).items():
                arr[i] = v
            out[k] = arr
        return out

    monkeypatch.setattr(sm, "NudityScorer", FakeNude)
    monkeypatch.setattr(sm, "ClipScorer", FakeClip)
    monkeypatch.setattr(sm, "describe_scores", fake_describe)
    monkeypatch.setattr(sm.models, "clip_ready", lambda d: True)


def _watch(media_dir, tmp_path, f):
    from prude import scenes as sm
    info = probe(media_dir / "movie51.mkv")
    return sm.watch(info, tmp_path / "work", tmp_path / "models", f)


def test_nudity_by_body_part(media_dir, tmp_path, monkeypatch):
    _fake_models(monkeypatch, {3: {"FEMALE_BREAST_EXPOSED": 0.9}, 4: {"FEMALE_BREAST_EXPOSED": 0.8},
                               12: {"MALE_BREAST_EXPOSED": 0.9}}, {})
    f = FilterOptions(words=[], scenes=["nude_f_breasts", "nude_m_genitals"])
    found = _watch(media_dir, tmp_path, f)
    assert len(found) == 1 and found[0]["detail"] == "female breasts"   # shirtless man not selected
    f.scenes = ["nude_m_chest"]
    found = _watch(media_dir, tmp_path, f)
    assert [x["label"] for x in found] == ["Shirtless man"]


def test_sex_with_and_without_nudity(media_dir, tmp_path, monkeypatch):
    clip = {"sex": {2: 0.8, 3: 0.85, 4: 0.8, 14: 0.8, 15: 0.9, 16: 0.8}}
    _fake_models(monkeypatch, {3: {"FEMALE_BREAST_EXPOSED": 0.9}}, clip)
    f = FilterOptions(words=[], scenes=["sex_nude", "sex_implied"])
    kinds = sorted(x["kind"] for x in _watch(media_dir, tmp_path, f))
    assert kinds == ["sex_implied", "sex_nude"]
    f.scenes = ["sex_implied"]
    assert [x["kind"] for x in _watch(media_dir, tmp_path, f)] == ["sex_implied"]
    # nudity flagged inside a sex scene is folded into it, not listed twice
    f.scenes = ["sex_nude", "nude_f_breasts"]
    found = _watch(media_dir, tmp_path, f)
    assert [x["kind"] for x in found] == ["sex_nude"] and "female breasts" in found[0]["detail"]


def test_group_sensitivity_and_actions(media_dir, tmp_path, monkeypatch):
    clip = {"violence": {5: 0.5, 6: 0.5}, "swimwear": {10: 0.6, 11: 0.6}}
    _fake_models(monkeypatch, {}, clip)
    f = FilterOptions(words=[], scenes=["violence", "swimwear"],
                      scene_sensitivity={"violence": 1, "sexual": 4},
                      scene_actions={"sexual": "blur", "violence": "cut"})
    found = _watch(media_dir, tmp_path, f)
    assert [(x["kind"], x["action"]) for x in found] == [("swimwear", "blur")]   # violence too faint at 1


def test_mixed_scene_actions_render(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=["mild"], scene_action="cut")
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [
        {"id": "a", "kind": "violence", "label": "V", "start": 1.0, "end": 3.0, "on": True, "action": "cut"},
        {"id": "b", "kind": "swimwear", "label": "S", "start": 6.0, "end": 8.0, "on": True, "action": "blur"},
        {"id": "c", "kind": "kissing", "label": "K", "start": 11.0, "end": 13.0, "on": True, "action": "black"},
        {"id": "d", "kind": "scary", "label": "F", "start": 15.5, "end": 16.5, "on": True, "action": "mute"},
    ]
    out = tmp_path / "mixed.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert (res["cuts"], res["blurs"], res["blackouts"], res["muted_scenes"]) == (1, 1, 1, 1)
    assert probe(out).duration == pytest.approx(18.0, abs=0.3)
    # after the 2 s cut: blackout 11-13 -> 9-11, muted scene 15.5-16.5 -> 13.5-14.5
    assert level(out, 9.8) < -90 and level(out, 13.8) < -90 and level(out, 12.0) > -40
    assert level(out, 4.5) > -40   # blurred scene keeps its sound


def test_black_screen_keeps_sound(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=[], scene_action="cut")
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.scenes = [{"id": "a", "kind": "kissing", "label": "K", "start": 2.0, "end": 4.0, "on": True,
                 "action": "black_keep"}]
    out = tmp_path / "bk.mkv"
    pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert level(out, 3.0) > -40   # dialogue still audible
    r = subprocess.run([ffmpeg(), "-hide_banner", "-ss", "3", "-i", str(out), "-frames:v", "1",
                        "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                       capture_output=True, text=True)
    assert float([l for l in r.stderr.splitlines() if "YAVG" in l][0].split("=")[-1]) < 20


def test_blur_only_body_parts(media_dir, fake_words, tmp_path, monkeypatch):
    import cv2
    from prude import blurparts

    class FakeDetector:   # "finds" a body part in the top-left area between 4 and 8 seconds
        def __init__(self, classes):
            pass

        def boxes(self, image_path):
            return [(40.0, 30.0, 120.0, 90.0)]   # in the 640-wide sample's pixels

    monkeypatch.setattr(blurparts, "Detector", FakeDetector)
    f = FilterOptions(words=[], scenes=["nude_f_breasts"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.scenes = [{"id": "a", "kind": "nudity", "label": "Nudity", "start": 4.0, "end": 8.0, "on": True,
                 "action": "blur_parts"}]
    out = tmp_path / "parts.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert res["parts_blurred"] == 1 and probe(out).duration == pytest.approx(20.0, abs=0.3)

    def frame(path, t):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        assert ok
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)

    def sharp(img):
        return cv2.Laplacian(img, cv2.CV_64F).var()

    o, c = frame(media_dir / "movie51.mkv", 6.0), frame(out, 6.0)
    inside = (slice(22, 128), slice(28, 172))        # inside the padded box (640x360 source)
    outside = (slice(250, 340), slice(400, 600))
    assert sharp(c[inside]) < 0.25 * sharp(o[inside])         # edges inside the box blurred away
    assert abs(c[outside] - o[outside]).mean() < 6            # rest of the picture untouched
    o2, c2 = frame(media_dir / "movie51.mkv", 14.0), frame(out, 14.0)
    assert abs(c2[inside] - o2[inside]).mean() < 6            # no blur outside the scene
    assert level(out, 6.0) > -40                              # sound kept


# ---------------------------------------------------------------- stable edit identity
def test_ids_survive_shifted_rescan():
    from prude.pipeline import _carry_over
    old = [{"id": "w1", "start": 10.04, "end": 10.6, "words": "damn", "category": "mild", "on": False},
           {"id": "s1", "kind": "violence", "group": "violence", "start": 99.4, "end": 120.0, "on": True,
            "action": "blur", "action_set": True}]
    # a re-scan finds the same things, nudged across a rounding boundary
    new = [{"start": 9.4, "end": 10.1, "words": "damn", "category": "mild"},
           {"kind": "violence", "group": "violence", "start": 100.6, "end": 118.0}]
    out = _carry_over(new, old)
    w = [x for x in out if x.get("words") == "damn"][0]
    s = [x for x in out if x.get("kind") == "violence"][0]
    assert (w["id"], w["on"]) == ("w1", False)
    assert (s["id"], s["action"]) == ("s1", "blur")


def test_different_things_get_new_ids():
    from prude.pipeline import _carry_over
    old = [{"id": "w1", "start": 10.0, "end": 10.5, "words": "damn", "category": "mild", "on": False}]
    out = _carry_over([{"start": 10.1, "end": 10.6, "words": "hell", "category": "mild"}], old)
    assert out[0]["id"] != "w1" and out[0]["on"] is True


def test_old_projects_migrate(media_dir):
    import json
    p = Project.open(media_dir / "movie51.mkv")
    p.mutes = [{"start": 1.0, "end": 2.0, "words": "x", "on": True}]
    p.save()
    raw = json.loads((p.dir / "project.json").read_text())
    raw.pop("schema", None)
    raw["mutes"][0].pop("id", None)
    (p.dir / "project.json").write_text(json.dumps(raw))
    q = Project.open(media_dir / "movie51.mkv")
    assert q.schema == 2 and q.mutes[0]["id"] and q.mutes[0]["origin"] == "auto"


# ---------------------------------------------------------------- export options & Plex naming
def test_title_parsing_and_plex_names(tmp_path):
    from prude.export import ExportOptions, output_path, parse_title
    assert parse_title(Path("Pretty Woman (1990).mkv")) == ("Pretty Woman", "1990")
    assert parse_title(Path("The.Matrix.1999.1080p.BluRay.x264.mkv")) == ("The Matrix", "1999")
    assert parse_title(Path("Movies/Up (2009)/movie.mkv")) == ("Up", "2009")
    src = tmp_path / "Movies" / "The Matrix (1999)" / "The Matrix (1999).mkv"
    o = ExportOptions(naming="plex_edition", edition="Family Edit")
    assert output_path(src, o).name == "The Matrix (1999) {edition-Family Edit}.mkv"
    assert output_path(src, o).parent == src.parent                     # next to the original
    o2 = ExportOptions(naming="plex_library", folder=str(tmp_path / "Family Movies"), container="mp4")
    assert output_path(src, o2) == tmp_path / "Family Movies" / "The Matrix (1999)" / "The Matrix (1999).mp4"
    o3 = ExportOptions(naming="plex_library")                           # same folder + same name
    assert output_path(src, o3) != src and "(clean)" in output_path(src, o3).name
    o4 = ExportOptions(naming="plex_edition", edition='Kids: "PG" cut?')
    assert output_path(src, o4).name == "The Matrix (1999) {edition-Kids PG cut}.mkv"


def test_export_mp4_720p_with_summary(media_dir, fake_words, tmp_path):
    from prude.export import ExportOptions
    f = FilterOptions(words=["mild"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "a", "kind": "violence", "label": "V", "start": 2.0, "end": 4.0, "on": True,
                 "action": "cut"}]
    ex = ExportOptions(folder=str(tmp_path / "Plex"), container="mp4", resolution="720",
                       naming="plex_edition", edition="Family Edit")
    src = tmp_path / "Up (2009).mkv"
    src.write_bytes((media_dir / "movie51.mkv").read_bytes())
    p.source = str(src)
    res = pipeline.render_project(p, f, Settings(device="cpu"), export=ex)
    out = Path(res["output"])
    assert out == tmp_path / "Plex" / "Up (2009)" / "Up (2009) {edition-Family Edit}.mp4"
    assert res["valid"] and not res["problems"]
    assert res["final_duration"] == pytest.approx(18.0, abs=0.3) and res["original_duration"] == pytest.approx(20.0, abs=0.1)
    assert res["size_bytes"] > 0 and res["resolution"] == "640x360"      # 360p source is never upscaled
    assert any("re-encoded" in w for w in res["warnings"])
    assert not list(out.parent.glob("*.partial*"))
    assert p.exports and p.exports[-1]["edition"] == "Family Edit"


def test_resize_down(media_dir, fake_words, tmp_path):
    from prude.export import ExportOptions
    from prude.media import probe as pr
    big = tmp_path / "big.mkv"
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=24:d=4",
                    "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(big)], check=True)
    p = _project(big, [{"w": " damn", "s": 1.0, "e": 1.4, "p": 1}])
    f = FilterOptions(words=["mild"])
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"),
                                  export=ExportOptions(resolution="720", naming="simple"))
    assert pr(res["output"]).height == 720 and res["valid"]


def test_copy_only_export_is_marked_untouched(media_dir, fake_words, tmp_path):
    from prude.export import ExportOptions
    f = FilterOptions(words=["mild"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(), export=ExportOptions(folder=str(tmp_path), naming="simple"))
    assert res["how"] == "video copied untouched" and res["valid"]
    assert any("bit-for-bit" in w for w in res["warnings"])
    assert video_md5(media_dir / "movie51.mkv") == video_md5(res["output"])


# ---------------------------------------------------------------- TV naming
def test_tv_episode_names():
    from prude.export import ExportOptions, output_path
    ed = ExportOptions(naming="plex_edition", edition="Family Edit")
    p = Path("/tv/The Office (US) (2005)/Season 02/The Office (US) - S02E03 - Office Olympics.mkv")
    assert output_path(p, ed) == Path("/tv/The Office (US) (2005) {edition-Family Edit}/Season 02/"
                                      "The Office (US) - S02E03 - Office Olympics.mkv")
    assert output_path(Path("/dl/Bluey.S01E05.1080p.mkv"), ed) == \
        Path("/dl/Bluey {edition-Family Edit}/Season 01/Bluey.S01E05.1080p.mkv")
    assert output_path(Path("/tv/Friends/friends.1x04.avi"), ed) == \
        Path("/tv/Friends {edition-Family Edit}/Season 01/friends.1x04.mkv")
    lib = ExportOptions(naming="plex_library", folder="/family")
    assert output_path(p, lib) == Path("/family/The Office (US) (2005)/Season 02/"
                                       "The Office (US) - S02E03 - Office Olympics.mkv")


# ---------------------------------------------------------------- batch queue
def _batch_movies(media_dir, tmp_path, fake_words, names):
    out = []
    for n in names:
        dst = tmp_path / "in" / n
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((media_dir / "movie51.mkv").read_bytes())
        p = Project.open(dst)
        p.transcript = {"track": 0, "model": "fake", "words": fake_words}
        p.save()
        out.append(dst)
    return out


def test_batch_queue_runs_saves_and_reviews(media_dir, tmp_path, fake_words):
    import threading
    from dataclasses import asdict
    from prude.export import ExportOptions
    from prude.jobs import DONE, FAILED, REVIEW, Queue, run_queue
    a, b = _batch_movies(media_dir, tmp_path, fake_words, ["Up (2009).mkv", "Cars (2006).mkv"])
    q = Queue(tmp_path / "queue.json")
    f = asdict(FilterOptions(words=["mild", "blasphemy"]))
    ex = ExportOptions(folder=str(tmp_path / "out"), naming="plex_edition", edition="Family").to_dict()
    q.add([a, b], "Family night", f, ex, mode="save")
    q.add([b], "Family night", f, ex)                       # duplicate: ignored
    q.add([tmp_path / "gone.mkv"], "Family night", f, ex)    # missing file: fails cleanly
    rv = _batch_movies(media_dir, tmp_path, fake_words, ["Show.S01E02.mkv"])
    q.add(rv, "Kids", f, ex, mode="review")
    assert len(q.jobs) == 4
    seen = []
    run_queue(q, Settings(), on_change=lambda j: seen.append((j.name, j.status)), cancel=threading.Event())
    st = {j.name: j.status for j in q.jobs}
    assert st == {"Up (2009).mkv": DONE, "Cars (2006).mkv": DONE, "gone.mkv": FAILED,
                  "Show.S01E02.mkv": REVIEW}
    assert (tmp_path / "out" / "Up (2009)" / "Up (2009) {edition-Family}.mkv").exists()
    assert (tmp_path / "out" / "Cars (2006)" / "Cars (2006) {edition-Family}.mkv").exists()
    failed = [j for j in q.jobs if j.status == FAILED][0]
    assert "no longer there" in failed.error and any("Failed" in ln for ln in failed.log)
    # saved to disk; reloading keeps everything
    q2 = Queue(tmp_path / "queue.json")
    assert {j.name: j.status for j in q2.jobs} == st


def test_batch_resume_after_restart(tmp_path):
    from prude.jobs import QUEUED, RUNNING, Job, Queue
    q = Queue(tmp_path / "q.json")
    q.jobs = [Job(source="x.mkv", status=RUNNING, progress=0.5)]
    q.save()
    q2 = Queue(tmp_path / "q.json")
    assert q2.jobs[0].status == QUEUED and q2.jobs[0].progress == 0.0 and "Interrupted" in q2.jobs[0].message


def test_batch_cancel_and_retry(media_dir, tmp_path, fake_words):
    import threading
    from dataclasses import asdict
    from prude.jobs import CANCELLED, DONE, Queue, run_queue
    (a,) = _batch_movies(media_dir, tmp_path, fake_words, ["Heat (1995).mkv"])
    q = Queue(tmp_path / "q.json")
    q.add([a], "P", asdict(FilterOptions(words=["mild"])), {"folder": str(tmp_path / "o"), "naming": "simple"})
    stop = threading.Event()

    def change(job):
        if job.status == "running":   # press Cancel as soon as it starts
            stop.set()
    run_queue(q, Settings(), on_change=change, cancel=stop)
    j = q.jobs[0]
    assert j.status == CANCELLED and not list((tmp_path / "o").glob("*.partial*"))
    q.retry(j)
    run_queue(q, Settings(), cancel=threading.Event())
    assert j.status == DONE and j.attempts == 2


def test_expand_folders(tmp_path):
    from prude.jobs import expand_sources
    (tmp_path / "S" / "Season 01").mkdir(parents=True)
    for n in ["Show - S01E10.mkv", "Show - S01E2.mkv", "Show - S01E1.mkv", "notes.txt",
              "Show - S01E1 (clean).mkv", "x {edition-Family}.mkv"]:
        (tmp_path / "S" / "Season 01" / n).write_bytes(b"x")
    names = [p.name for p in expand_sources([tmp_path / "S"])]
    assert names == ["Show - S01E1.mkv", "Show - S01E2.mkv", "Show - S01E10.mkv"]


# ---------------------------------------------------------------- subtitles (2.8)
def test_star_styles():
    from prude.words import star_word
    pats = build_patterns(["strong", "blasphemy", "mild"])
    assert censor_text("Shut the fuck up", pats, "stars")[0] == "Shut the **** up"
    assert censor_text("Shut the fuck up", pats, "first")[0] == "Shut the f*** up"
    assert censor_text("Shut the fuck up", pats, "word")[0] != "Shut the fuck up"
    assert "fuck" not in censor_text("Shut the fuck up", pats, "word")[0].lower()
    assert star_word("Fucking", "first").startswith("F")


def _sub_text(path, index=0):
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(path), "-map", f"0:s:{index}", "-f", "srt", "-"],
                       capture_output=True, text=True)
    return r.stdout


def test_english_movie_subtitles_catch_missed_words(media_dir, fake_words):
    f = FilterOptions(words=["mild", "blasphemy", "strong"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.scan(p, f, Settings(device="cpu"))
    norms = {x["norm"] for x in p.subtitle_edits}
    assert {"hell", "shit"} <= norms
    checks = [m for m in p.mutes if m.get("origin") == "subtitle-check"]
    # "shit" is in the subtitles at 9.8-11 s but was never heard as a word -> caught by the check
    assert any("shit" in m["words"] for m in checks), p.mutes
    # "hell" was heard and muted already -> not added twice
    assert not any(m["words"].startswith("hell") for m in checks)


def test_foreign_dialogue_stars_subtitles_only(media_dir, fake_words, tmp_path):
    f = FilterOptions(words=["mild", "blasphemy", "strong"], star_style="first")
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.dialogue = "foreign"
    pipeline.scan(p, f, Settings(device="cpu"))
    assert p.mutes == [] or not p.active_mutes()
    assert len(p.subtitle_edits) >= 3
    hell = next(x for x in p.subtitle_edits if x["norm"] == "hell")
    hell["on"] = False                                  # keep this one as written
    out = tmp_path / "foreign.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu", make_subtitles=False), out)
    assert res["how"] == "video copied untouched"
    info = probe(out)
    assert len(info.subtitles) == 1
    disp = info.subtitles[0].get("disposition", {})
    assert disp.get("default") == 1 and disp.get("forced") == 1
    text = _sub_text(out)
    assert "What the hell?" in text                      # unticked -> unchanged
    assert "s***" in text and "shit" not in text
    assert level(out, 5.2) > -60                         # the sound is untouched


def test_skipped_listening_counts_as_scanned(media_dir):
    p = Project.open(media_dir / "old.avi")
    p.transcript = {"track": 0, "model": "", "words": [], "skipped": "not English"}
    assert p.has_transcript(0)


def test_picture_subtitles_found(media_dir, tmp_path, monkeypatch):
    """Finds each picture subtitle with the right timing (the OCR itself is faked here)."""
    from conftest import PIC_CUES
    from prude import ocr
    texts = iter([c[2] for c in PIC_CUES])
    monkeypatch.setattr(ocr, "engine", lambda: None)
    monkeypatch.setattr(ocr, "read_text", lambda img: next(texts))
    info = probe(media_dir / "pgs.mkv")
    cues = ocr.read_image_subtitles(info, info.subtitles[0], tmp_path)
    assert [c[2] for c in cues] == [c[2] for c in PIC_CUES]
    for (a, b, _t), (ea, eb, _e) in zip(cues, PIC_CUES):
        assert abs(a - ea) <= 0.2 and abs(b - eb) <= 0.2


@pytest.mark.skipif(not __import__("importlib").util.find_spec("rapidocr_onnxruntime"),
                    reason="RapidOCR not installed")
def test_picture_subtitles_read_and_starred(media_dir, tmp_path):
    """Spanish dialogue, English picture subtitles: the F-word is starred out in the clean copy."""
    f = FilterOptions(words=["mild", "blasphemy", "strong"])
    p = Project.open(media_dir / "pgs.mkv")
    p.dialogue = "foreign"
    p.transcript = {"track": 0, "model": "", "words": [], "skipped": "not English"}
    pipeline.scan(p, f, Settings(device="cpu"))
    norms = {x["norm"] for x in p.subtitle_edits}
    assert "fuck" in norms and "shit" in norms, p.subtitle_edits
    out = tmp_path / "pgs-clean.mkv"
    pipeline.render_project(p, f, Settings(device="cpu"), out)
    info = probe(out)
    assert info.subtitles[0]["codec_name"] == "subrip"
    assert info.subtitles[0]["disposition"]["default"] == 1
    text = _sub_text(out)
    assert "they are coming" in text.lower()
    assert "fuck" not in text.lower() and "****" in text
