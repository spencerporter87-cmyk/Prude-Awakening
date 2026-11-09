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
    assert j.status == CANCELLED, (j.status, j.error, j.log[-5:] if hasattr(j, "log") else "")
    assert not list((tmp_path / "o").glob("*.partial*"))
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


def test_burned_in_words_grouped_and_blurred(media_dir, fake_words, tmp_path):
    """Sightings of a burned-in subtitle line become one blur box over the word only."""
    from prude import burnin
    info = probe(media_dir / "movie51.mkv")          # 640x360
    pats = build_patterns(["strong"])
    # the band is the bottom 40% (144 px) read at 960 wide -> scale 1.5
    lines = [[t, "Get the shit out", 300, 60, 660, 100] for t in (6.0, 6.5, 7.0, 7.5)]
    lines += [[12.0, "Hello there", 300, 60, 600, 100]]
    items = burnin.words_from_lines(info, lines, pats)
    assert len(items) == 1
    it = items[0]
    assert it["norm"] == "shit" and it["start"] == pytest.approx(5.4) and it["end"] == pytest.approx(8.1)
    x, y, w, h = it["box"]
    # "shit" is characters 8..12 of 16 -> roughly the middle of the line, not the whole line
    assert 300 / 1.5 < x < 480 / 1.5 + 10 and w < 360 / 1.5 and 360 - 144 <= y < 360
    f = FilterOptions(words=["strong"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    p.burned_edits = items
    out = tmp_path / "burned.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert res["burned_words"] == 1 and res["valid"]
    assert "re-encoded" in res["how"]
    assert abs(probe(out).duration - 20) < 0.3
    import cv2

    def frame(path, t):
        png = tmp_path / f"{Path(path).stem}-{t}.png"
        subprocess.run([ffmpeg(), "-v", "error", "-y", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        str(png)], check=True)
        return cv2.cvtColor(cv2.imread(str(png)), cv2.COLOR_BGR2GRAY)

    def sharp(img):
        return cv2.Laplacian(img[y:y + h, x:x + w], cv2.CV_64F).var()
    a, b = frame(media_dir / "movie51.mkv", 7.0), frame(out, 7.0)
    assert sharp(b) < 0.3 * sharp(a)                    # the word's spot is blurred
    a2, b2 = frame(media_dir / "movie51.mkv", 10.0), frame(out, 10.0)
    assert sharp(b2) > 0.7 * sharp(a2)                  # only while the line shows


@pytest.mark.skipif(not __import__("importlib").util.find_spec("rapidocr_onnxruntime"),
                    reason="RapidOCR not installed")
def test_burned_in_subtitles_read(tmp_path):
    """Real text recognition on subtitles drawn into the picture."""
    import cv2
    import numpy as np
    from prude import burnin
    from conftest import ff
    txt = np.zeros((80, 760, 4), np.uint8)
    for color, thick in (((0, 0, 0, 255), 9), ((255, 255, 255, 255), 3)):
        cv2.putText(txt, "What the fuck is that?", (20, 58), cv2.FONT_HERSHEY_DUPLEX, 1.5, color, thick,
                    cv2.LINE_AA)
    cv2.imwrite(str(tmp_path / "t.png"), txt)
    src = tmp_path / "burned.mkv"
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=24:d=12", "-i", str(tmp_path / "t.png"),
       "-filter_complex", "[0][1]overlay=(W-w)/2:H-h-50:enable='between(t,3,6)'", "-c:v", "libx264",
       "-preset", "ultrafast", str(src))
    info = probe(src)
    assert burnin.has_text(info)
    items = burnin.scan(info, build_patterns(["strong"]), tmp_path)
    assert [i["norm"] for i in items] == ["fuck"], items
    assert 2.0 <= items[0]["start"] <= 3.2 and 5.8 <= items[0]["end"] <= 7.0
    x, y, w, h = items[0]["box"]
    assert y > 720 * 0.6 and w < 400


def test_hdr10_metadata_kept(tmp_path):
    """HDR10 source: re-encoding keeps 10-bit HDR color and the brightness details."""
    from prude.render import x265_hdr_params
    from conftest import ff
    from prude.media import encoders
    if "libx265" not in encoders():
        pytest.skip("no libx265")
    src = tmp_path / "hdr.mkv"
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=6", "-f", "lavfi", "-i", "sine=f=400:d=6",
       "-vf", "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc",
       "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
       "-x265-params", "log-level=error:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
       "colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)"
       "L(10000000,1):max-cll=1000,400",
       "-c:a", "aac", str(src))
    info = probe(src)
    assert info.is_hdr, {k: v for k, v in info.video.items() if "color" in k or "pix" in k or "profile" in k}
    meta = info.hdr10_metadata()
    assert meta.get("mastering") and int(meta["max_cll"]) == 1000
    params = x265_hdr_params(info)
    assert "max-cll=1000,400" in params and any(p.startswith("master-display=G(13250,34500)") for p in params)
    p = Project.open(src)
    p.transcript = {"track": 0, "model": "fake", "words": [{"w": " damn", "s": 2.0, "e": 2.3, "p": 0.9}]}
    f = FilterOptions(words=["mild"])
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "b", "kind": "violence", "label": "V", "start": 3.0, "end": 4.0, "on": True,
                 "action": "black"}]
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "hdr-clean.mkv")
    got = probe(res["output"])
    assert got.is_hdr and got.is_10bit
    assert int(got.hdr10_metadata().get("max_cll") or 0) == 1000
    assert any("HDR kept" in w and "couldn't" not in w for w in res["warnings"]), res["warnings"]


def test_scene_edges_snap_to_exact_camera_cuts(tmp_path):
    from prude.scenes import refine_edges
    from conftest import ff
    src = tmp_path / "shots.mkv"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3.25", "-f", "lavfi", "-i",
       "mandelbrot=s=320x180:r=24", "-f", "lavfi", "-i", "smptebars=s=320x180:r=24:d=4",
       "-filter_complex", "[1]trim=duration=4.25,setpts=PTS-STARTPTS[m];[0][m][2]concat=n=3:v=1[v]",
       "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(src))
    info = probe(src)
    # 1-per-second estimate: starts about a second early, ends about a second late
    a, b = refine_edges(info, 2.6, 8.1, {})
    assert a == pytest.approx(3.25, abs=0.05) and b == pytest.approx(7.5, abs=0.05)
    # no camera cut nearby -> the estimate is kept
    assert refine_edges(info, 10.0, 11.0, {}) == (10.0, 11.0)


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_fast_cuts_copy_the_picture(tmp_path, fake_words, codec):
    """Cutting with 'fast cuts': no re-encode, no frame of the cut scene left, sound in sync."""
    import cv2
    import numpy as np
    from conftest import ff
    from prude.export import ExportOptions
    src = tmp_path / "gop.mkv"
    # 0-6 s gray picture + 300 Hz, 6-9 s RED picture (the scene to cut) + 1000 Hz, 9-20 s gray + 300 Hz
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=20", "-f", "lavfi", "-i", "sine=f=300:d=20",
       "-f", "lavfi", "-i", "sine=f=1000:d=20",
       "-filter_complex", "[0]drawbox=c=red:t=fill:enable='between(t,6,9)'[v];"
                          "[1]volume=0:enable='between(t,6,9)'[q];[2]volume=0:enable='not(between(t,6,9))'[r];"
                          "[q][r]amix=inputs=2:normalize=0[a]",
       "-map", "[v]", "-map", "[a]",
       *(["-c:v", "libx264", "-preset", "ultrafast", "-bf", "3",
          "-x264-params", "keyint=48:min-keyint=48:scenecut=0:open-gop=1"] if codec == "h264" else
         ["-c:v", "libx265", "-preset", "ultrafast",
          "-x265-params", "log-level=error:keyint=48:min-keyint=48:scenecut=0:open-gop=1:bframes=4"]),
       "-c:a", "flac", str(src))
    p = _project(src, fake_words)
    p.scenes = [{"id": "c", "kind": "violence", "label": "V", "start": 6.0, "end": 9.0, "on": True,
                 "action": "cut"}]
    f = FilterOptions(words=[])
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "fast.mkv",
                                  export=ExportOptions(fast_cuts=True))
    assert res["how"] == "video copied untouched (fast cuts)", res
    assert res["valid"], res["problems"]
    out = Path(res["output"])
    # keyframes every 2 s: the cut runs on to the keyframe at 10 s, and stops a bit before 6 s
    assert 15.7 <= probe(out).duration <= 16.1
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", str(out), "-map", "0:v", "-vf", "scale=32:18",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    frames = np.frombuffer(r.stdout, np.uint8).reshape(-1, 18, 32, 3).astype(int)
    red = [i for i, fr in enumerate(frames) if (fr[..., 0] - fr[..., 1]).mean() > 120]
    assert not red, f"cut scene leaked into frames {red[:5]}"
    assert not r.stderr.strip(), r.stderr[:500]          # decodes cleanly across the splice
    # sound in sync: the cut scene's 1000 Hz tone is gone, the 300 Hz tone plays on both sides

    def band(t, f):
        r = subprocess.run([ffmpeg(), "-hide_banner", "-ss", str(t), "-t", "0.2", "-i", str(out), "-map", "0:a",
                            "-af", f"bandpass=f={f}:width_type=q:w=8,bandpass=f={f}:width_type=q:w=8,astats",
                            "-f", "null", "-"], capture_output=True, text=True)
        v = [l for l in r.stderr.splitlines() if "RMS level dB" in l][-1].split(":")[-1].strip()
        return -200.0 if v == "-inf" else float(v)
    for t in (1.0, 5.3, 5.9, 6.1, 7.0, 9.0, 13.0):
        assert band(t, 1000) < band(t, 300) - 30, t


def test_soft_fades_and_other_audio_tracks(tmp_path, fake_words):
    from conftest import ff
    from prude.export import ExportOptions
    src = tmp_path / "two.mkv"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=20", "-f", "lavfi", "-i", "sine=f=400:d=20",
       "-f", "lavfi", "-i", "sine=f=700:d=20", "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264",
       "-preset", "ultrafast", "-c:a", "ac3", "-metadata:s:a:0", "language=eng",
       "-metadata:s:a:1", "language=fre", str(src))
    f = FilterOptions(words=["mild"], mute_fade=True)
    p = _project(src, fake_words)
    pipeline.find_word_mutes(p, f)
    p.scenes = [{"id": "c", "kind": "violence", "label": "V", "start": 12.0, "end": 14.0, "on": True,
                 "action": "cut"}]
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "out.mkv",
                                  export=ExportOptions(other_audio=True))
    assert res["valid"], res["problems"]
    got = probe(res["output"])
    assert len(got.audio) == 2
    assert "not filtered" in got.audio[1].title and got.audio[1].language == "fre"
    assert not got.audio[1].default and got.audio[0].default
    assert abs(got.duration - 18.0) < 0.3
    assert level(res["output"], 5.25) < -60            # "hell" is silent in the middle
    assert level(res["output"], 2.0) > -40


# ---------------------------------------------------------------- Advanced Editing (beta)
def _fake_voice(text, gender, out):
    """Stand-in for a TTS voice: a 500 Hz tone about 0.1 s per letter."""
    ff("-f", "lavfi", "-i", f"sine=f=500:d={max(0.3, 0.09 * len(text)):.2f}", "-ar", "22050", "-ac", "1",
       str(out))


from conftest import ff  # noqa: E402


def test_replacement_suggestions():
    from prude import replace
    assert replace.suggest("hell?") == "heck"
    assert replace.suggest('shit (heard as "ship")') == "crap"
    assert replace.suggest("God damn").lower() == "gosh darn"
    assert replace.suggest("zzzzword") == ""
    assert replace.text_for({"words": "hell", "replace_text": "heck no"}) == "heck no"
    assert replace.text_for({"words": "hell", "replace_text": ""}) == ""        # user chose silence


def test_pitch_and_level():
    import numpy as np
    from prude import replace
    t = np.arange(16000) / 16000
    x = (0.3 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    f0, db = replace.pitch_and_level(x, 16000)
    assert 110 <= f0 <= 130 and -14 < db < -8


def test_replacement_words_are_spoken_over_the_silence(media_dir, fake_words, tmp_path, monkeypatch):
    from prude import replace
    monkeypatch.setattr(replace, "synthesize", lambda text, gender, engine, settings=None, spend_ok=False,
                        log=print, hook=None: (_make_clip(tmp_path, text), "windows"))
    f = FilterOptions(words=["mild", "blasphemy", "strong"],
                      advanced={"enabled": True, "mode": "replace", "engine": "windows"})
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "adv.mkv")
    assert res["valid"] and res["replaced_words"] >= 2, res["warnings"]
    out = tmp_path / "adv.mkv"
    # the original word (a 400 Hz tone in FC?) is gone; our 500 Hz replacement is in the centre speaker

    def band(t, f, pan):
        r = subprocess.run([ffmpeg(), "-hide_banner", "-ss", str(t), "-t", "0.2", "-i", str(out), "-map", "0:a:0",
                            "-af", f"pan=mono|c0={pan},bandpass=f={f}:width_type=q:w=8,astats",
                            "-f", "null", "-"], capture_output=True, text=True)
        v = [l for l in r.stderr.splitlines() if "RMS level dB" in l][-1].split(":")[-1].strip()
        return -200.0 if v == "-inf" else float(v)
    assert band(5.3, 500, "FC") > -45                  # a spoken replacement where "hell" was
    assert band(5.3, 500, "FL") < -80                  # only in the centre channel (dialogue)
    assert level(out, 2.0, pan="FL") > -40             # the rest untouched


def _make_clip(tmp_path, text):
    p = tmp_path / f"voice-{abs(hash(text))}.wav"
    if not p.exists():
        _fake_voice(text, "male", p)
    return p


def test_failed_voice_leaves_the_word_silent(media_dir, fake_words, tmp_path, monkeypatch):
    from prude import replace

    def boom(*a, **k):
        raise RuntimeError("no voice installed")
    monkeypatch.setattr(replace, "synthesize", boom)
    f = FilterOptions(words=["mild"], advanced={"enabled": True, "mode": "replace", "engine": "windows"})
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "fail.mkv")
    assert res["valid"] and res["replaced_words"] == 0
    assert level(tmp_path / "fail.mkv", 5.3, pan="FC") < -90         # still silent, never the original


def test_elevenlabs_needs_confirmation_and_a_key(tmp_path, monkeypatch):
    from prude import replace

    class S:
        elevenlabs_key = "k"
    calls = []
    monkeypatch.setattr(replace, "synth_elevenlabs", lambda *a, **k: calls.append(a))
    hook = lambda text, gender, out: _fake_voice(text, gender, out)          # noqa: E731
    _, used = replace.synthesize("heck", "male", "elevenlabs", S(), spend_ok=False, hook=hook)
    assert used == "windows" and not calls                 # no confirmation -> no spending
    _, used = replace.synthesize("heck", "male", "elevenlabs", type("T", (), {"elevenlabs_key": ""})(),
                                 spend_ok=True, hook=hook)
    assert used == "windows"                               # no key -> no ElevenLabs


def test_lipsync_blends_back_with_a_fake_musetalk(media_dir, fake_words, tmp_path, monkeypatch):
    import shutil as sh
    from prude import lipsync, replace
    monkeypatch.setattr(replace, "synthesize", lambda text, gender, engine, settings=None, spend_ok=False,
                        log=print, hook=None: (_make_clip(tmp_path, text), "windows"))
    fake = tmp_path / "MuseTalk"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "inference.py").write_text("")
    s = Settings(device="cpu", musetalk_dir=str(fake))

    def runner(py, folder, clip, wav, work, log=print):            # "MuseTalk": returns the clip unchanged
        out = work / "made.mp4"
        sh.copy(clip, out)
        return out
    f = FilterOptions(words=["mild"], advanced={"enabled": True, "mode": "replace_lipsync", "engine": "windows"})
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    # use the real render, then the lip-sync step with a fake face finder and runner
    monkeypatch.setattr(lipsync, "run_musetalk", runner)
    monkeypatch.setattr(lipsync, "face_present", lambda v, t: True)
    res = pipeline.render_project(p, f, s, tmp_path / "lips.mkv")
    assert "lip-sync" in res["how"], res["warnings"]
    assert res["valid"] is not False and abs(probe(tmp_path / "lips.mkv").duration - 20) < 0.5
    assert any("Lip-sync (experimental)" in w for w in res["warnings"])


def test_lipsync_not_set_up_is_explained(media_dir, fake_words, tmp_path, monkeypatch):
    from prude import replace
    monkeypatch.setattr(replace, "synthesize", lambda text, gender, engine, settings=None, spend_ok=False,
                        log=print, hook=None: (_make_clip(tmp_path, text), "windows"))
    f = FilterOptions(words=["mild"], advanced={"enabled": True, "mode": "replace_lipsync", "engine": "windows"})
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "nolips.mkv")
    assert any("isn't set up" in w for w in res["warnings"]) and res["replaced_words"] >= 1


def test_preview_sound_is_made_once_and_plays_what_the_file_has(media_dir, tmp_path, fake_words):
    """The player gets a stereo AAC copy of the file's own sound (so Dolby Digital is audible)."""
    import time
    from prude import preview
    f = FilterOptions(words=["mild", "blasphemy", "strong"])
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), tmp_path / "prev.mkv")
    a = preview.audio_proxy(res["output"], 0)
    info = probe(a)
    assert info.audio[0].codec == "aac" and info.audio[0].channels == 2 and abs(info.duration - 20) < 0.3
    assert level(a, 5.2) < -60 and level(a, 2.0) > -40        # the silenced word is silent in the preview too
    t = time.time()
    assert preview.audio_proxy(res["output"], 0) == a and time.time() - t < 1   # remembered


# ---------------------------------------------------------------- confidence, updates (3.1)
def test_word_confidence_from_how_clearly_it_was_heard():
    from prude import confidence as c
    words = [{"w": "Well", "s": 0, "e": .3, "p": .9}, {"w": " shit", "s": 1, "e": 1.3, "p": 0.35},
             {"w": " damn", "s": 2, "e": 2.3, "p": 0.95}, {"w": " hell", "s": 3, "e": 3.3, "p": 0.62}]
    hits = merge_intervals(find_matches(words, build_patterns(["mild", "strong"])))
    levels = {h["words"]: c.word(h)[0] for h in hits}
    assert levels == {"shit": c.LOW, "damn": c.HIGH, "hell": c.MEDIUM}
    assert c.word({"manual": True})[0] == c.MINE
    assert c.word({"origin": "subtitle-check", "unsure": True})[0] == c.LOW
    assert c.word({"origin": "subtitles"})[0] == c.HIGH


def test_scene_confidence_from_margin_over_threshold():
    from prude import confidence as c
    assert c.scene({"score": 0.9, "threshold": 0.45})[0] == c.HIGH
    assert c.scene({"score": 0.6, "threshold": 0.45})[0] == c.MEDIUM
    assert c.scene({"score": 0.47, "threshold": 0.45})[0] == c.LOW
    assert c.scene({"score": 0.47})[0] == c.MEDIUM            # older scans without a threshold
    assert c.scene({"manual": True})[0] == c.MINE


def test_scan_stores_confidence(media_dir, fake_words):
    p = _project(media_dir / "movie51.mkv", fake_words)
    pipeline.scan(p, FilterOptions(words=["mild", "blasphemy", "strong"]), Settings(device="cpu"))
    assert all("conf_p" in m for m in p.mutes if not m.get("origin") == "subtitle-check")


def test_update_check_and_download(tmp_path, monkeypatch):
    import hashlib
    from prude import updater
    blob = b"installer-bytes" * 1000

    class R:
        def __init__(self, status=200, js=None, body=b""):
            self.status_code, self._js, self.body = status, js, body
            self.headers = {"Content-Length": str(len(body))}

        def json(self):
            return self._js

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            for i in range(0, len(self.body), n):
                yield self.body[i:i + n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    release = {"assets": [{"name": "Prude-Awakening-Setup-3.2.0.exe", "browser_download_url": "http://x/y.exe",
                           "size": len(blob), "digest": "sha256:" + hashlib.sha256(blob).hexdigest()}]}
    info = updater.check("3.1.0", get=lambda *a, **k: R(200, release))
    assert info and info["version"] == "3.2.0" and info["sha256"]
    assert updater.check("3.2.0", get=lambda *a, **k: R(200, release)) is None       # already current
    assert updater.check("3.10.0", get=lambda *a, **k: R(200, release)) is None      # 3.10 > 3.2
    assert updater.check("3.1.0", get=lambda *a, **k: R(404)) is None
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    path = updater.download(info, get=lambda *a, **k: R(200, None, blob))
    assert Path(path).read_bytes() == blob
    bad = dict(info, sha256="0" * 64)
    with pytest.raises(RuntimeError):
        updater.download(bad, get=lambda *a, **k: R(200, None, blob))
    assert not list(tmp_path.rglob("*.part"))                  # a bad download is never kept


# ---- bug hunt (3.1.2) ------------------------------------------------------------

def _sc(a, b, action="cut"):
    return {"id": f"s{a}", "kind": "violence", "label": "V", "start": a, "end": b, "on": True, "action": action}


def test_scenes_hanging_over_the_edges_are_trimmed_to_the_movie(media_dir, fake_words):
    """A scene that starts before 0 or ends after the movie must not skew the cut length."""
    from prude.render import clamp_ranges
    assert clamp_ranges([(-3, 2), (18, 25), (30, 40), (5, 5)], 20.0) == [(0.0, 2.0), (18.0, 20.0)]
    f = FilterOptions(words=["mild"])
    for scenes, kept in (([_sc(-3, 2)], 18.0), ([_sc(18, 26)], 18.0)):
        p = _project(media_dir / "movie51.mkv", fake_words)
        p.scenes = scenes
        res = pipeline.render_project(p, f, Settings(device="cpu"), export=None)
        assert res["valid"], res["problems"]
        assert res["final_duration"] == pytest.approx(kept, abs=0.3)


def test_subtitle_lines_past_the_end_dont_lengthen_the_file(media_dir, fake_words):
    from prude.subtitles import clip_cues
    assert clip_cues([[1, 2, "a"], [19.5, 22, "b"], [25, 26, "c"], [-2, 0.01, "d"]], 20.0) == \
        [[1.0, 2.0, "a"], [19.5, 20.0, "b"]]
    late = [dict(w, s=w["s"], e=w["e"]) for w in fake_words] + [{"w": "damn", "s": 19.8, "e": 21.5, "p": 0.9}]
    p = _project(media_dir / "movie51.mkv", late)
    f = FilterOptions(words=["mild"])
    pipeline.find_word_mutes(p, f)
    res = pipeline.render_project(p, f, Settings(device="cpu"), export=None)
    assert res["valid"], res["problems"]
    assert res["final_duration"] == pytest.approx(20.0, abs=0.3)


def test_file_without_sound_gives_a_clear_message(tmp_path, fake_words):
    from conftest import ff
    from prude.media import MediaError
    src = tmp_path / "silent.mkv"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=4", "-c:v", "libx264", "-preset", "ultrafast", str(src))
    p = Project.open(src)
    p.scenes = [_sc(1, 2)]
    with pytest.raises(MediaError, match="no audio track"):
        pipeline.render_project(p, FilterOptions(words=["mild"]), Settings(device="cpu"))


def test_censoring_keeps_quote_marks_and_hells_is_caught():
    pats = build_patterns(["strong", "mild"])
    assert censor_text("‘bitch’ he said", pats)[0] == "‘****’ he said"
    assert censor_text("'damn' it, \"hell\" no", pats)[0] == "'****' it, \"****\" no"
    assert censor_text("Hell's bells, what the hells", pats)[1] == 2
    assert censor_text("Hello, Hellen and Michelle", pats)[1] == 0


def test_movie_titles_with_numbers_keep_them():
    from prude.export import parse_title, clean_name
    cases = {"2012 (2009).mkv": ("2012", "2009"), "Blade Runner 2049 (2017).mkv": ("Blade Runner 2049", "2017"),
             "Blade.Runner.2049.2017.1080p.BluRay.mkv": ("Blade Runner 2049", "2017"),
             "1917 (2019).mkv": ("1917", "2019"), "Pretty.Woman.1990.1080p.mkv": ("Pretty Woman", "1990"),
             "Blade Runner 2049.mkv": ("Blade Runner 2049", ""), "Up (2009) {edition-Family Edit}.mkv": ("Up", "2009")}
    for name, want in cases.items():
        assert parse_title(Path("/m") / name) == want, name
    assert clean_name("CON") != "CON" and clean_name("Up") == "Up"


def test_saving_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("PRUDE_DATA_DIR", str(tmp_path / "data"))
    s = Settings()
    s.last_preset = "x"
    s.save()
    assert Settings.load().last_preset == "x"
    assert not list((tmp_path / "data").glob("*.tmp"))


# ---- better nudity detection (3.2.0) -----------------------------------------------

def _fake_nsfw(monkeypatch, p_by_frame, default=0.0):
    import numpy as np
    from prude import scenes as sm

    class FakeNsfw:
        def __init__(self, *_a):
            pass

        def probs(self, paths):
            return np.array([p_by_frame.get(int(Path(p).stem) - 1, default) for p in paths], np.float32)

    monkeypatch.setattr(sm, "NsfwClassifier", FakeNsfw)
    monkeypatch.setattr(sm.models, "nsfw_ready", lambda d: True)


def test_whole_picture_opinion_overrules_a_body_part_false_alarm(media_dir, tmp_path, monkeypatch):
    rows = {3: {"FEMALE_BREAST_EXPOSED": 0.8}, 4: {"FEMALE_BREAST_EXPOSED": 0.8}}
    f = FilterOptions(words=[], scenes=["nude_f_breasts"])
    _fake_models(monkeypatch, rows, {})
    _fake_nsfw(monkeypatch, {}, default=0.02)             # the whole picture looks harmless
    assert _watch(media_dir, tmp_path / "a", f) == []
    _fake_nsfw(monkeypatch, {}, default=0.95)             # the whole picture is explicit too
    found = _watch(media_dir, tmp_path / "b", f)
    assert len(found) == 1 and "whole-picture check 95% explicit" in found[0]["why"]


def test_one_faint_frame_is_not_a_scene_but_one_sure_frame_is(media_dir, tmp_path, monkeypatch):
    f = FilterOptions(words=[], scenes=["nude_f_breasts"], sensitivity=3)
    _fake_models(monkeypatch, {6: {"FEMALE_BREAST_EXPOSED": 0.6}}, {})
    assert _watch(media_dir, tmp_path / "a", f) == []
    f5 = FilterOptions(words=[], scenes=["nude_f_breasts"], sensitivity=5)
    assert len(_watch(media_dir, tmp_path / "b", f5)) == 1      # most sensitive: any single frame
    _fake_models(monkeypatch, {6: {"FEMALE_BREAST_EXPOSED": 0.95}}, {})
    assert len(_watch(media_dir, tmp_path / "c", f)) == 1


def test_sharper_detector_is_asked_only_about_suspicious_frames(media_dir, tmp_path, monkeypatch):
    from prude import scenes as sm
    asked = []

    class Big:
        def __init__(self, path=None, res=320):
            self.big = path is not None

        def scores(self, path):
            if not self.big:
                i = int(Path(path).stem) - 1
                return [0.7 if i in (3, 4) else 0.0] + [0.0] * (len(sm.NUDE_CLASS_ORDER) - 1)
            asked.append(Path(path).stem)
            return [0.0] * len(sm.NUDE_CLASS_ORDER)           # the sharper look says: nothing there

    _fake_models(monkeypatch, {}, {})
    monkeypatch.setattr(sm, "NudityScorer", Big)
    monkeypatch.setattr(sm.models, "nudenet_640_ready", lambda d: True)
    f = FilterOptions(words=[], scenes=["nude_f_breasts"])
    assert _watch(media_dir, tmp_path, f) == []              # overruled by the sharper detector
    assert len(asked) == 2                                    # only the two suspicious of ~20 frames


def test_combine_nudity_and_description_check():
    import numpy as np
    from prude.scenes import combine_nudity, clip_nude_prob, CHEST
    det = np.zeros((3, 6), np.float32)
    det[:, 0] = [0.9, 0.9, 0.9]
    det[2, CHEST] = 0.8
    out = combine_nudity(det, nsfw_p=np.array([0.0, 0.9, -1.0]), clip_p=np.array([-1.0, 0.0, -1.0]))
    assert out[1, 0] < 0.5 < out[2, 0] and out[0, 0] < 0.5       # disagreeing checks cut a hit down
    assert out[2, CHEST] == pytest.approx(0.8)                    # a bare chest is the detector's call
    assert (out >= 0).all() and out[0, 0] >= 0.9 * 0.2            # never cut below a fifth

    class T:
        def embed_texts(self, prompts):
            e = np.eye(len(prompts), dtype=np.float32)
            return e
    embs = np.zeros((1, 25), np.float32)
    embs[0, 0] = 1.0                                              # looks like the first "naked" prompt
    assert clip_nude_prob(embs, T())[0] > 0.9
    embs[0, 0], embs[0, 8] = 0.0, 1.0                             # looks like "a marble statue"... (a safe prompt)
    assert clip_nude_prob(embs, T())[0] < 0.1


def test_whole_picture_classifier_reads_the_model_files(tmp_path):
    import numpy as np
    from prude.nsfw import NsfwClassifier
    import cv2
    img = np.full((90, 160, 3), 128, np.uint8)
    cv2.imwrite(str(tmp_path / "a.jpg"), img)
    c = NsfwClassifier.__new__(NsfwClassifier)
    c.size, c.mean, c.std, c.nsfw_index, c.input = 384, [0.5] * 3, [0.5] * 3, 1, "input"

    class S:
        def run(self, _o, feeds):
            n = feeds["input"].shape[0]
            assert feeds["input"].shape == (n, 3, 384, 384)
            return [np.tile(np.array([[0.0, 4.0]], np.float32), (n, 1))]
    c.sess = S()
    p = c.probs([tmp_path / "a.jpg", tmp_path / "missing.jpg"])
    assert p.shape == (2,) and p[0] > 0.95


# ---- "See it edited" (3.2.1) -------------------------------------------------------------

def test_edited_preview_shows_the_real_edits(media_dir, fake_words):
    from prude.media import probe
    from prude.preview import edited_clip, nearest_edit
    p = _project(media_dir / "movie51.mkv", fake_words)
    f = FilterOptions(words=["mild", "blasphemy", "strong"], scene_action="cut")
    pipeline.find_word_mutes(p, f)
    p.scenes = [_sc(14, 16, "cut")]
    m = p.mutes[0]
    out = edited_clip(p, f, Settings(device="cpu"), m["start"], m["end"], pad=3.0)
    o, e = probe(out["original"]), probe(out["edited"])
    assert o.video and e.video and e.audio and e.video.get("codec_name") == "h264"
    assert out["valid"], out["problems"]
    mid = (m["start"] + m["end"]) / 2 - out["start"]
    assert level(out["original"], mid) > -40            # the clip of the original has the word
    assert level(out["edited"], mid) < -60              # the edited clip is silent there
    assert "silenced" in out["summary"]
    # a cut scene inside the window shortens the edited clip but not the original
    far = edited_clip(p, f, Settings(device="cpu"), 13.0, 17.0, pad=2.0)
    assert probe(far["original"]).duration - probe(far["edited"]).duration == pytest.approx(2.0, abs=0.4)
    assert "cut out" in far["summary"]


def test_nearest_edit_picks_the_closest_ticked_item():
    from prude.preview import nearest_edit
    p = Project(source="x")
    p.mutes = [{"start": 10, "end": 11, "words": "hell", "on": True}, {"start": 50, "end": 51, "words": "damn", "on": False}]
    p.scenes = [{"start": 100, "end": 120, "label": "Fight", "on": True}]
    assert nearest_edit(p, 12)[:2] == (10.0, 11.0)
    assert nearest_edit(p, 60)[:2] == (100.0, 120.0)      # the unticked word at 50 doesn't count
    assert nearest_edit(Project(source="x"), 5) is None


# ---- blur that follows movement and your own nudges (3.3.0) -------------------------------

def test_scene_nudges_and_blur_margin():
    from prude.render import scene_ranges
    p = Project(source="x")
    p.scenes = [{"start": 10, "end": 20, "on": True, "action": "blur_parts", "pad_before": 0.4, "pad_after": 0.25},
                {"start": 50, "end": 60, "on": True, "action": "cut", "pad_before": 1.0},
                {"start": 80, "end": 90, "on": False, "action": "cut"}]
    r = scene_ranges(p, "cut", 200.0, blur_margin=1.0)
    assert r["blur_parts"] == [(8.6, 21.25)]           # nudges plus the margin that private-parts blur gets
    assert r["cut"] == [(49.0, 60.0)]                  # nudges apply to every action, the margin only to blur
    assert scene_ranges(p, "cut", 200.0)["blur_parts"] == [(9.6, 20.25)]


def test_tracking_bridges_gaps_and_merges_overlaps():
    from prude.blurparts import merge_boxes, track
    box = (100.0, 100.0, 50.0, 50.0)
    s = [(0.0, [box]), (0.1, []), (0.2, []), (0.3, [box]), (0.8, []), (1.5, []), (2.0, []), (2.1, [box]), (2.2, [])]
    out = dict(track(s))
    assert out[0.1] and out[0.2]                       # a blink between two sightings is blurred through
    assert out[0.8] == [box]
    assert out[1.5] == []                              # long gone: nothing blurred
    assert out[2.0] == [box]                           # picked up a moment before it's found
    assert out[2.2] == [box]                           # and held a moment after
    two = merge_boxes([(0, 0, 50, 50), (40, 10, 50, 50), (300, 300, 20, 20)])
    assert len(two) == 2 and (0, 0, 90, 60) in [tuple(map(int, b)) for b in two]


def test_blur_covers_the_edges_and_blinks(media_dir, fake_words, tmp_path, monkeypatch):
    import cv2
    from prude import blurparts

    class Mover:   # sees the area from 3.5 to 8.3 s (the scene says 4-8), but misses 5.0-5.3
        def __init__(self, classes):
            pass

        def boxes(self, image_path):
            import cv2
            t = 3.0 + (int(Path(image_path).stem) - 1) / blurparts.SAMPLE_FPS     # the range starts at 4 - 1 s margin
            k = cv2.imread(str(image_path)).shape[1] / 640.0                      # the boxes below are for a 640-wide picture
            return [(40.0 * k, 30.0 * k, 120.0 * k, 90.0 * k)] if 3.5 <= t <= 8.3 and not 5.0 <= t < 5.3 else []

    monkeypatch.setattr(blurparts, "Detector", Mover)
    f = FilterOptions(words=[], scenes=["nude_f_breasts"], blur_margin=1.0)
    p = _project(media_dir / "movie51.mkv", fake_words)
    p.scenes = [{"id": "a", "kind": "nudity", "label": "Nudity", "start": 4.0, "end": 8.0, "on": True,
                 "action": "blur_parts"}]
    out = tmp_path / "edges.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert res["valid"], res["problems"]

    def frame(path, t):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        assert ok
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)

    inside = (slice(22, 128), slice(28, 172))
    sharp = lambda img: cv2.Laplacian(img, cv2.CV_64F).var()           # noqa: E731
    src = media_dir / "movie51.mkv"
    for t, blurred in ((3.7, True), (5.15, True), (8.2, True), (2.5, False), (9.6, False)):
        o, c = frame(src, t), frame(out, t)
        assert (sharp(c[inside]) < 0.3 * sharp(o[inside])) == blurred, t


# ---------------------------------------------------------------- hand-painted blur
def test_paint_shape_follows_keyframes_and_edits():
    from prude import paint
    p = paint.new_patch(2.0, 4.0)
    paint.add_stroke(p, 2.0, [(0.30, 0.40), (0.34, 0.40)], 0.03)
    assert p["anchor"] == [0.30, 0.40] and p["start"] == 2.0
    (r, pts), = paint.shape_at(p, 2.0)
    assert r == pytest.approx(0.03) and pts[0] == pytest.approx((0.30, 0.40))
    paint.move_at(p, 4.0, 0.2, 0.1)                       # slide it at 4 s; it glides there from the start
    assert paint.transform(p, 3.0) == pytest.approx((0.1, 0.05, 1.0))
    (r, pts), = paint.shape_at(p, 4.0)
    assert pts[0] == pytest.approx((0.50, 0.50))
    paint.scale_at(p, 4.0, 2.0, (0.5, 0.5))               # grow around the point that stays put
    (r, pts), = paint.shape_at(p, 4.0)
    assert r == pytest.approx(0.06) and pts[0] == pytest.approx((0.50, 0.50))
    # strokes added later land where you drew them, whatever the shape's own offset
    paint.add_stroke(p, 3.0, [(0.85, 0.85)], 0.03)
    assert paint.shape_at(p, 3.0)[1][1][0] == pytest.approx((0.85, 0.85))
    assert paint.erase_near(p, 3.0, 0.85, 0.85, 0.01, 1.0) == 1 and len(p["strokes"]) == 1
    q = paint.shifted(p, 1.0)
    assert q["start"] == 1.0 and q["keys"][-1]["t"] == pytest.approx(3.0) and p["keys"][-1]["t"] == 4.0
    assert paint.span(dict(p, pad_before=0.5, pad_after=0.25)) == (1.5, 4.25)


def test_paint_mask_only_inside_the_shape_and_time(tmp_path):
    from prude import paint
    p = paint.new_patch(1.0, 2.0)
    paint.add_stroke(p, 1.0, [(0.25, 0.5), (0.35, 0.5)], 0.05)
    m = paint.mask_at([p], 1.5, 200, 100)
    assert m[50, 60] > 200 and m[5, 5] == 0 and m[50, 150] == 0
    assert not paint.mask_at([p], 3.0, 200, 100).any()                         # not at that moment
    assert not paint.mask_at([dict(p, on=True)], 0.5, 200, 100).any()


def test_painted_blur_hits_the_exact_frames(media_dir, fake_words, tmp_path):
    import cv2
    from prude import paint
    src = media_dir / "movie51.mkv"
    info = probe(src)
    fps = info.fps
    f = FilterOptions(words=[], scenes=[])
    p = _project(src, fake_words)
    patch = paint.new_patch(5.0, 6.0)
    paint.add_stroke(patch, 5.0, [(0.10, 0.25), (0.22, 0.25)], 0.06)       # over the textured top-left area
    p.paint = [patch]
    out = tmp_path / "painted.mkv"
    res = pipeline.render_project(p, f, Settings(device="cpu"), out)
    assert res["valid"], res["problems"]
    assert probe(out).duration == pytest.approx(info.duration, abs=0.2)

    def frame(path, n):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, img = cap.read()
        assert ok
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)

    H, W = frame(src, 0).shape
    inside = (slice(int(H * 0.2), int(H * 0.3)), slice(int(W * 0.12), int(W * 0.20)))
    sharp = lambda img: cv2.Laplacian(img, cv2.CV_64F).var()       # noqa: E731
    first, last = int(round(5.0 * fps)), int(round(6.0 * fps))
    # the first painted frame and the last are blurred; the frames either side are untouched
    for n, blurred in ((0, False), (first - 1, False), (first, True), (first + 1, True), (last - 2, True),
                       (last - 1, True), (last, False), (last + 1, False)):
        o, c = frame(src, n), frame(out, n)
        assert (sharp(c[inside]) < 0.35 * sharp(o[inside])) == blurred, n
        assert sharp(c[: int(H * 0.1)]) == pytest.approx(sharp(o[: int(H * 0.1)]), rel=0.15), n   # elsewhere untouched


def test_painted_blur_shows_in_the_edited_preview(media_dir, fake_words):
    import cv2
    from prude import paint
    from prude.preview import edited_clip
    p = _project(media_dir / "movie51.mkv", fake_words)
    f = FilterOptions(words=[], scene_action="cut")
    patch = paint.new_patch(10.0, 12.0)
    paint.add_stroke(patch, 10.0, [(0.10, 0.25), (0.22, 0.25)], 0.06)
    p.paint = [patch]
    out = edited_clip(p, f, Settings(device="cpu"), 10.0, 12.0, pad=2.0)
    assert out["valid"], out["problems"]
    assert "painted blur" in out["summary"]

    def sharp_at(path, t):
        cap = cv2.VideoCapture(str(path))
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        assert ok
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
        H, W = g.shape
        return cv2.Laplacian(g[int(H * 0.2):int(H * 0.3), int(W * 0.12):int(W * 0.20)], cv2.CV_64F).var()

    t = 11.0 - out["start"]
    assert sharp_at(out["edited"], t) < 0.35 * sharp_at(out["original"], t)
    assert sharp_at(out["edited"], 0.3) > 0.7 * sharp_at(out["original"], 0.3)       # before the painted blur


def test_paint_preview_frame_blurs_only_the_painted_spot(tmp_path):
    import cv2
    import numpy as np
    from prude import paint
    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, (120, 200, 3), dtype=np.uint8)
    path = tmp_path / "f.jpg"
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 98])
    p = paint.new_patch(1.0, 2.0)
    paint.add_stroke(p, 1.0, [(0.30, 0.5), (0.45, 0.5)], 0.08)
    plain = paint.preview_frame(path, p, 3.0)                       # outside the time span: untouched
    done = paint.preview_frame(path, p, 1.5, cache={})
    sharp = lambda a: cv2.Laplacian(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var()   # noqa: E731
    assert sharp(done[50:70, 70:90]) < 0.2 * sharp(plain[50:70, 70:90])
    assert (done[:20] == plain[:20]).all()
    paint.include_frame(p, 5.0, 24.0)
    assert p["end"] == pytest.approx(5.0 + 1 / 24)


def test_paint_mask_moves_with_the_shape_and_clips_cover_every_frame(media_dir, tmp_path):
    import numpy as np
    from prude import paint
    info = probe(media_dir / "movie51.mkv")
    fps = info.fps
    p = paint.new_patch(4.6, 7.0)                          # not on frame boundaries
    paint.add_stroke(p, 4.6, [(0.2, 0.5)], 0.05)
    paint.move_at(p, 6.9, 0.5, 0.0)                        # slides right across the picture
    left = paint.mask_at([p], 4.7, 200, 100)
    right = paint.mask_at([p], 6.9, 200, 100)
    assert left[50, 40] > 200 and left[50, 140] == 0 and right[50, 140] > 200 and right[50, 40] == 0
    segs = paint.write_masks([p], info, tmp_path / "m")
    assert len(segs) == 1
    s = segs[0]
    assert s["start_frame"] == int(np.ceil(4.6 * fps - 1e-3 * fps))
    last_blurred = int(np.ceil(7.0 * fps - 1e-3 * fps)) - 1
    assert s["start_frame"] + s["frames"] - 3 == last_blurred          # every frame up to the end, plus two spare empty ones


def test_paint_follow_cursor_and_default_length():
    from prude import paint
    p = paint.new_patch(5.0, 5.0)
    paint.add_stroke(p, 5.0, [(0.30, 0.50), (0.34, 0.50)], 0.04)
    paint.include_frame(p, 5.0, 24.0)
    assert paint.frame_count(p, 24.0) == 1                         # one frame until you play on
    grab = paint.grab_offset(p, 5.0, 0.32, 0.50)
    for n in range(1, 25):                                         # a second of "hold and drag to the right"
        t = 5.0 + n / 24.0
        paint.include_frame(p, t, 24.0)
        paint.follow_to(p, t, 0.32 + n * 0.01, 0.50, grab)
    assert paint.frame_count(p, 24.0) == 25
    (r, pts), = paint.shape_at(p, 5.0 + 24 / 24.0)
    assert pts[0] == pytest.approx((0.30 + 0.24, 0.50), abs=1e-6)  # the blur is where the cursor was
    (r, pts), = paint.shape_at(p, 5.0 + 12 / 24.0)
    assert pts[0] == pytest.approx((0.30 + 0.12, 0.50), abs=1e-6)


# ---------------------------------------------------------------- body-part blur: second bug hunt
def _wide_clip(tmp_path, secs=3, with_bars=True):
    """A 2.39:1 picture inside black bars, grey with a small white 'target' at x=900..960, y=200..260 of the
    1280x536 picture (so at y=292..352 of the 1280x720 frame)."""
    import subprocess
    out = tmp_path / "wide.mkv"
    vf = "drawbox=x=900:y=200:w=60:h=60:color=white:t=fill"
    if with_bars:
        vf += ",pad=1280:720:0:92:black"
    subprocess.run([ffmpeg_path(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x606060:s=1280x536:r=24:d={secs}", "-vf", vf, "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def ffmpeg_path():
    from prude.media import ffmpeg
    return ffmpeg()


def test_content_rect_finds_letterbox_bars_but_not_dark_scenes():
    import numpy as np
    from prude import blurparts
    pic = np.full((720, 1280, 3), 90, np.uint8)
    pic[:92] = 0
    pic[-92:] = 0
    assert blurparts.content_rect([pic]) == (0, 92, 1280, 628)
    dark = np.full((720, 1280, 3), 8, np.uint8)                    # a night scene is not a bar
    assert blurparts.content_rect([dark]) == (0, 0, 1280, 720)
    mixed = [dark, pic]                                            # judged over several frames
    assert blurparts.content_rect(mixed) == (0, 92, 1280, 628)
    assert blurparts.views(1280, 536)[1:] == [(0, 0, 768, 536), (512, 0, 768, 536)]
    assert blurparts.views(1000, 750) == [(0, 0, 1000, 750)]


def test_small_parts_in_a_widescreen_film_are_found_by_looking_closer(tmp_path):
    from pathlib import Path as P
    import cv2
    from prude import blurparts
    clip = _wide_clip(tmp_path)

    class NeedsABigTarget:                       # a detector that can't see a part smaller than 6% of the picture's width
        def boxes(self, image_path):
            img = cv2.imread(str(image_path))
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            ys, xs = (g > 230).nonzero()
            if not len(xs):
                return []
            x, y, w, h = xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            return [(float(x), float(y), float(w), float(h))] if w / img.shape[1] >= 0.06 else []

    info = probe(clip)
    got = blurparts.detect(info, [(0.0, 2.5)], ["FEMALE_BREAST_EXPOSED"], tmp_path / "w", detector=NeedsABigTarget())
    assert got and all(boxes for _t, boxes in got)                  # seen in every sample...
    x, y, w, h = got[0][1][0]
    assert 880 <= x <= 920 and 285 <= y <= 305 and 50 <= w <= 80 and 50 <= h <= 80   # ...in the right place of the real frame


def test_third_opinion_locates_what_the_detectors_missed():
    import numpy as np
    from prude import blurparts
    img = np.full((360, 640, 3), 100, np.uint8)
    img[150:210, 400:460] = 255                                     # the explicit area

    class Checker:                                                   # explicit only while the bright area is visible
        def probs(self, imgs):
            return np.array([0.05 + 0.9 * ((im[150:210, 400:460] > 200).mean()) for im in imgs])

    (x, y, w, h), = blurparts.locate_with_checker(Checker(), img)
    assert x <= 400 and y <= 150 and x + w >= 460 and y + h >= 210    # it covers the area...
    assert w < 640 / 2 and h < 360 / 2                                # ...and not the whole picture
    assert blurparts.locate_with_checker(Checker(), np.full((360, 640, 3), 100, np.uint8)) == []   # nothing explicit: nothing


def test_track_is_fast_on_long_empty_stretches_and_ranges_dont_wipe_each_other(tmp_path):
    import time
    from prude import blurparts
    t0 = time.time()
    out = blurparts.track([(i / 10, []) for i in range(30000)])
    assert time.time() - t0 < 2.0 and not any(b for _t, b in out)
    box = (10.0, 10.0, 50.0, 50.0)
    cmd = tmp_path / "x.cmd"
    # two scenes 0.08 s apart: nothing may blank the picture between them
    s = [(i / 10, [box]) for i in range(0, 51)] + [(5.1 + i / 10, [box]) for i in range(0, 41)]
    blurparts.write_commands(cmd, s, [(0.0, 5.0), (5.08, 9.2)], 640, 360)
    times = [float(ln.split(" ", 1)[0]) for ln in cmd.read_text().splitlines()]
    parked = [ln for ln in cmd.read_text().splitlines() if "x 0, drawbox@s0 y 0, drawbox@s0 w 1, drawbox@s0 h 1" in ln
              and 5.0 < float(ln.split(" ", 1)[0]) < 5.4]
    assert not parked and times == sorted(times)


# ---------------------------------------------------------------- bug hunt 3
def test_movie_names_with_percent_signs_dont_break_frame_files(media_dir, tmp_path):
    from prude import blurparts, paint
    info = probe(media_dir / "movie51.mkv")

    class Always:
        def boxes(self, image_path):
            return [(10.0, 10.0, 80.0, 60.0)]

    odd = tmp_path / "Movie 50% (clean) #1 %d"
    got = blurparts.detect(info, [(1.0, 3.0)], ["FEMALE_BREAST_EXPOSED"], odd, detector=Always())
    assert got and all(b for _t, b in got)
    assert len(paint.extract_frames(info, 1.0, 2.0, tmp_path / "frames 100%")) >= 20


def test_atomic_writes_survive_a_brief_file_lock(tmp_path, monkeypatch):
    import os
    from prude import paths
    real, calls = os.replace, {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("held by the virus scanner")
        return real(a, b)
    monkeypatch.setattr(paths.os, "replace", flaky)
    paths.write_atomic(tmp_path / "x.json", "{}")
    assert (tmp_path / "x.json").read_text() == "{}" and calls["n"] == 3


def test_ffmpeg_seeks_are_not_pushed_by_the_files_start_offset(tmp_path, monkeypatch):
    """FFmpeg's -ss already counts from the start of a file's own clock (a transport stream starts ~1.4 s in),
    so adding that offset again looks at the wrong moment."""
    import subprocess
    from prude import replace
    from prude.media import ffmpeg
    src = tmp_path / "late.ts"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=6", "-f", "lavfi", "-i",
                    "sine=d=6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-f", "mpegts", str(src)], check=True)
    info = probe(src)
    assert info.format_start > 1.0
    seen = []

    class R:
        stdout, stderr, returncode = b"", b"", 0

    monkeypatch.setattr(replace.subprocess, "run", lambda cmd, **k: (seen.append(cmd), R())[1])
    replace.actor_voice(info, 0, 3.0, 4.0, window=0.5)
    cmd = seen[0]
    assert float(cmd[cmd.index("-ss") + 1]) == 2.5
