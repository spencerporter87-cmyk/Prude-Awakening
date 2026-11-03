"""Making the clean copy.

* Only muting words: the video is copied byte-for-byte (fast, zero quality loss).
* Cutting scenes or blacking them out: the video is re-encoded at very high quality
  (NVIDIA graphics card when available), audio and subtitles are kept in sync."""

import shutil
from pathlib import Path

from .media import (LANG3, LANG_NAMES, TEXT_SUB_CODECS, MediaError, channel_layouts, encoder_works,
                    encoders, ffmpeg, ffmpeg_major_version, run_ffmpeg, stream_bitrate)
from .subtitles import (clean_cues, extract_text_sub, generate_cues, read_srt, shift_cues,
                        write_srt)

KEEP_CONTAINERS = {".mkv", ".mp4", ".m4v", ".mov"}
MP4_LIKE = {".mp4", ".m4v", ".mov"}
LOSSLESS = {"truehd", "mlp", "flac", "alac", "wavpack", "tta"}


def output_path(src: Path, output_dir: str = "") -> Path:
    ext = src.suffix.lower() if src.suffix.lower() in KEEP_CONTAINERS else ".mkv"
    folder = Path(output_dir) if output_dir else src.parent
    return folder / f"{src.stem} (clean){ext}"


def merge_ranges(ranges, gap=0.05):
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in ranges if b > a):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _between(ranges, var="t"):
    return "+".join(f"between({var},{a:.3f},{b:.3f})" for a, b in ranges)


def audio_codec_args(st: dict, out_ext, quality="match"):
    codec = st.get("codec_name", "")
    ch = int(st.get("channels", 2))
    br = stream_bitrate(st) or 0
    mp4 = out_ext in MP4_LIKE
    profile = (st.get("profile") or "").lower()
    enc = encoders()
    lossless_src = codec in LOSSLESS or codec.startswith("pcm_") or (codec == "dts" and "ma" in profile)
    if quality == "lossless" or lossless_src:
        return ["-c:a:0", "alac" if mp4 else "flac"]
    if codec == "ac3" and "ac3" in enc:
        return ["-c:a:0", "ac3", "-b:a:0", str(min(max(br, 448000 if ch > 2 else 224000), 640000))]
    if codec == "eac3" and "eac3" in enc:
        return ["-c:a:0", "eac3", "-b:a:0", str(min(max(br, 768000 if ch > 2 else 256000), 1536000))]
    if codec == "opus" and "libopus" in enc and not mp4:
        return ["-c:a:0", "libopus", "-b:a:0", str(max(br, 96000 * max(1, ch // 2)))]
    if codec == "mp3" and "libmp3lame" in enc and ch <= 2:
        return ["-c:a:0", "libmp3lame", "-b:a:0", "320k"]
    if codec == "aac" or mp4:
        return ["-c:a:0", "aac", "-b:a:0", str(max(br, 640000 if ch > 2 else 256000))]
    return ["-c:a:0", "flac"]


def _ratio(v):
    try:
        a, _, b = str(v).partition("/")
        return float(a) / float(b or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def x265_hdr_params(info):
    """x265 settings that keep HDR10 brightness information (mastering display, MaxCLL)."""
    meta = info.hdr10_metadata() if info.is_hdr else {}
    out = []
    m = meta.get("mastering")
    if m:
        c = {k: _ratio(m.get(k)) for k in m}
        if all(v is not None for v in c.values()):
            def xy(a, b):
                return f"({round(c[a] * 50000)},{round(c[b] * 50000)})"
            out.append("master-display=G" + xy("green_x", "green_y") + "B" + xy("blue_x", "blue_y") +
                       "R" + xy("red_x", "red_y") + "WP" + xy("white_point_x", "white_point_y") +
                       f"L({round(c['max_luminance'] * 10000)},{round(c['min_luminance'] * 10000)})")
    if meta.get("max_cll") is not None:
        out.append(f"max-cll={int(meta['max_cll'])},{int(meta.get('max_fall') or 0)}")
    if info.is_hdr and info.video.get("color_transfer") == "smpte2084":
        out += ["hdr10=1", "hdr10-opt=1", "repeat-headers=1"]
    return out


def video_encoder_args(info, quality="high", device="auto"):
    """Returns (args, description)."""
    tenbit = info.is_10bit
    v = info.video
    color = []
    for opt, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"),
                     ("-colorspace", "color_space"), ("-color_range", "color_range")):
        val = v.get(key)
        if val and val not in ("unknown", "reserved"):
            color += [opt, val]
    if device != "cpu" and encoder_works("hevc_nvenc"):
        cq = "16" if quality == "max" else "19"
        args = ["-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", cq,
                "-b:v", "0", "-spatial-aq", "1"]
        args += ["-profile:v", "main10", "-pix_fmt", "p010le"] if tenbit else ["-pix_fmt", "yuv420p"]
        return args + color, "graphics card (NVENC HEVC)"
    if "libx265" in encoders() and (tenbit or info.is_hdr):
        args = ["-c:v", "libx265", "-preset", "slow" if quality == "max" else "medium",
                "-crf", "16" if quality == "max" else "18", "-pix_fmt", "yuv420p10le",
                "-x265-params", ":".join(["log-level=error"] + x265_hdr_params(info))]
        return args + color, "processor (x265, 10-bit)"
    args = ["-c:v", "libx264", "-preset", "slow" if quality == "max" else "medium",
            "-crf", "15" if quality == "max" else "17", "-pix_fmt", "yuv420p"]
    return args + color, "processor (x264)"


FADE = 0.03


def _fade_gain(ranges, fade=FADE):
    """volume expression: 0 inside each range, ramping over `fade` s at the edges, 1 elsewhere."""
    terms = [f"clip((abs(t-{(a + b) / 2:.3f})-{(b - a) / 2:.3f})/{fade},0,1)" for a, b in ranges]
    return "*".join(terms) or "1"


def build_audio_chain(info, track, mutes, blackouts, cuts, mute_mode, fade=False, inserts=()):
    """inserts: [(input number, start seconds)] mono clips laid over the silences (Advanced Editing)."""
    st = info.audio[track].raw
    src = f"[0:a:{track}]asetnsamples=n=256:p=0"
    graph, label = [], "a0"
    layout = st.get("channel_layout", "")
    chans = channel_layouts().get(layout)
    if mutes and mute_mode == "center" and chans and "FC" in chans and len(chans) > 1:
        pads = "".join(f"[c{i}]" for i in range(len(chans)))
        fc = chans.index("FC")
        join_in = "".join("[cfc]" if i == fc else f"[c{i}]" for i in range(len(chans)))
        jmap = "|".join(f"{i}.0-{c}" for i, c in enumerate(chans))
        graph.append(f"{src},channelsplit=channel_layout={layout}{pads}")
        if fade:
            graph.append(f"[c{fc}]volume='{_fade_gain([(a - FADE, b + FADE) for a, b in mutes])}'"
                         f":eval=frame[cfc]")
        else:
            graph.append(f"[c{fc}]volume=0:enable='{_between(mutes)}'[cfc]")
        graph.append(f"{join_in}join=inputs={len(chans)}:channel_layout={layout}:map={jmap}[{label}]")
        chain = f"[{label}]anull"
        whole_mutes = list(blackouts)
    else:
        chain = src
        whole_mutes = merge_ranges(list(mutes) + list(blackouts))
    if whole_mutes and fade:
        # the fade is outside the word: the ramp starts FADE before the silence
        chain += f",volume='{_fade_gain([(a - FADE, b + FADE) for a, b in whole_mutes])}':eval=frame"
    elif whole_mutes:
        chain += f",volume=0:enable='{_between(whole_mutes)}'"
    if inserts and layout:
        rate = int(st.get("sample_rate") or 48000)
        if chans:
            dlg = ["FC"] if "FC" in chans else [c for c in ("FL", "FR") if c in chans] or list(chans)
        else:
            dlg = ["FC"] if layout == "mono" else ["FL", "FR"]
        pan = f"pan={layout}|" + "|".join(f"{c}=c0" for c in dlg)
        graph.append(chain + "[apre]")
        labels = ""
        for n, (inp, start) in enumerate(inserts):
            graph.append(f"[{inp}:a]aresample={rate},adelay={int(round(start * 1000))}:all=1,{pan}[ri{n}]")
            labels += f"[ri{n}]"
        graph.append(f"[apre]{labels}amix=inputs={len(inserts) + 1}:normalize=0:duration=first:"
                     f"dropout_transition=0[amix]")
        chain = "[amix]anull"
    if cuts:
        removed = "+".join(f"gte(T,{b:.3f})*{b - a:.3f}" for a, b in cuts)
        chain += (f",aselect='not({_between(cuts)})',asetpts='PTS-({removed})/TB',"
                  f"aresample=async=1:first_pts=0")
    graph.append(chain + "[aout]")
    return graph


ACTIONS = ("cut", "black", "black_keep", "blur", "blur_parts", "mute")


def scene_ranges(project, default_action):
    """{action: merged ranges} for the ticked scenes, each with its own action."""
    out = {a: [] for a in ACTIONS}
    for s in project.active_scenes():
        a = s.get("action") or default_action
        out[a if a in out else "cut"].append((s["start"], s["end"]))
    return {a: merge_ranges(r) for a, r in out.items()}


def build_video_chain(blackouts, cuts, blurs=(), src="[0:v:0]", scale_h=None):
    if not blackouts and not cuts and not blurs and not scale_h:
        return [f"{src}null[vout]"] if src != "[0:v:0]" else []
    chain = src
    parts = []
    if blurs:
        parts.append("boxblur=luma_radius='min(w,h)/14':luma_power=3:"
                     "chroma_radius='min(cw,ch)/14':chroma_power=3:"
                     f"enable='{_between(blurs)}'")
    if blackouts:
        parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{_between(blackouts)}'")
    if cuts:
        removed = "+".join(f"gte(T,{b:.3f})*{b - a:.3f}" for a, b in cuts)
        parts.append(f"select='not({_between(cuts)})'")
        parts.append(f"setpts='PTS-({removed})/TB'")
    if scale_h:
        parts.append(f"scale=-2:{int(scale_h)}:flags=lanczos")
    return [chain + ",".join(parts) + "[vout]"]


def render(info, project, filters, settings, patterns, out: Path, progress=None, cancel=None,
           log=print, export=None):
    """Returns a dict summary. filters: FilterOptions; settings: Settings; export: ExportOptions."""
    adv = getattr(filters, "advanced", None) or {}
    if adv.get("enabled") and adv.get("mode") != "mute":
        import copy
        try:
            res = _render_with_fallback(info, project, filters, settings, patterns, out, progress, cancel, log, export)
        except MediaError as e:
            if cancel is not None and cancel.is_set():
                raise
            log(f"Advanced Editing (beta) failed ({str(e)[-200:]}); saving with plain silences instead.")
            plain = copy.copy(filters)
            plain.advanced = dict(adv, enabled=False)
            res = _render_with_fallback(info, project, plain, settings, patterns, out, progress, cancel, log, export)
            res.setdefault("warnings", []).append(
                "Advanced Editing (beta) failed, so the words were silenced instead of replaced.")
        if adv.get("mode") == "replace_lipsync" and res.get("replaced_words"):
            from . import lipsync
            res = lipsync.apply(info, project, filters, settings, res, progress, cancel, log)
        return res
    return _render_with_fallback(info, project, filters, settings, patterns, out, progress, cancel, log, export)


def _render_with_fallback(info, project, filters, settings, patterns, out, progress, cancel, log, export):
    try:
        return _render(info, project, filters, settings, patterns, out, progress, cancel, log,
                       settings.device, export)
    except MediaError as e:
        if settings.device == "cpu" or not project.active_scenes() or (cancel is not None and cancel.is_set()):
            raise
        log(f"Graphics card video encoding failed ({str(e)[-300:]}); retrying on the processor.")
        return _render(info, project, filters, settings, patterns, out, progress, cancel, log, "cpu", export)


AUDIO_NAMES = {"ac3": "Dolby Digital (AC3)", "eac3": "Dolby Digital Plus", "aac": "AAC", "flac": "FLAC",
               "alac": "ALAC", "dts": "DTS", "truehd": "Dolby TrueHD", "mp3": "MP3", "opus": "Opus",
               "libopus": "Opus", "libmp3lame": "MP3"}


def _slots(clips, cuts):
    """Where the replaced words are in the finished copy (after cuts)."""
    from .subtitles import TimeMap
    tm = TimeMap(cuts) if cuts else (lambda t: t)
    out = []
    for c in clips:
        a, b = tm(c["start"]), tm(c["end"])
        if a is not None and b is not None:
            out.append([round(a, 3), round(b, 3)])
    return out


def _remove(path, tries=10):
    """Delete a file that a just-stopped program may still hold open for a moment (Windows)."""
    import time
    for _ in range(tries):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.3)


def validate(out: Path, expected_duration, want_video, want_subs):
    """Check the finished file with ffprobe. Returns (ok, problems, info)."""
    from .media import probe
    problems = []
    try:
        got = probe(out)
    except Exception as e:
        return False, [f"The finished file can't be read: {e}"], None
    if want_video and not got.video:
        problems.append("The finished file has no video stream.")
    if not got.audio:
        problems.append("The finished file has no audio stream.")
    if expected_duration and abs(got.duration - expected_duration) > max(2.0, expected_duration * 0.01):
        problems.append(f"Length is {got.duration:.1f} s; expected about {expected_duration:.1f} s.")
    if want_subs and not got.subtitles:
        problems.append("Subtitles were expected but are missing.")
    return not problems, problems, got


def _render(info, project, filters, settings, patterns, out, progress, cancel, log, device, export=None):
    from .export import target_height
    quality = export.quality if export else settings.video_quality
    scale_h = target_height(info, export) if export else None
    track = project.audio_track
    st = info.audio[track].raw
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_ext = out.suffix.lower()
    tmp_files, tmp_dirs = [], []

    mutes = merge_ranges((m["start"], m["end"]) for m in project.active_mutes())
    by_action = scene_ranges(project, filters.scene_action)
    cuts, blurs, parts = by_action["cut"], by_action["blur"], by_action["blur_parts"]
    silent_blacks, sound_blacks = by_action["black"], by_action["black_keep"]
    blackouts = merge_ranges(silent_blacks + sound_blacks)
    mute_scenes = by_action["mute"]
    burned = [(float(x["start"]), float(x["end"]), *[int(v) for v in x["box"]])
              for x in getattr(project, "burned_edits", []) if x.get("on", True) and x.get("box")]
    cut_total = sum(b - a for a, b in cuts)
    if cuts and info.duration and info.duration - cut_total < 1.0:
        raise MediaError("Every part of the movie is marked for removal, so nothing would be left. "
                         "Untick some scenes and try again.")

    # ---- fast cuts: copy the picture between cuts instead of re-encoding it
    fast_list, fast_note, fast_work = None, "", None
    if (export is not None and getattr(export, "fast_cuts", False) and cuts and not
            (blackouts or blurs or parts or burned or scale_h)):
        from . import smartcut
        pl = smartcut.plan(info, cuts)
        if pl:
            fast_work = out.parent / f".{out.stem}.prude-parts"
            if progress:
                progress(0.0, "Copying the picture between cuts")
            try:
                got_parts = smartcut.copy_parts(info, pl[1], fast_work, cancel)
            except MediaError as e:
                log(str(e))
                got_parts = None
            if got_parts:
                fast_list, real = got_parts
                cuts = smartcut.cuts_from_kept(real, info.duration)
                cut_total = sum(b - a for a, b in cuts)
                fast_note = (f"Fast cuts: the picture was copied untouched; to start on a keyframe the "
                             f"cuts ran on {pl[2] + 0.25 * len(pl[1]):.1f} s longer in total.")
                log(fast_note)
            else:
                smartcut.cleanup(fast_work)
                fast_work = None
                log("Fast cuts didn't fit this file; re-encoding the picture instead.")
        else:
            log("Fast cuts don't fit this file (keyframes too far apart or unsupported format); "
                "re-encoding the picture instead.")

    # ---- subtitles
    from .subscan import IMAGE_SUB_CODECS, is_english_track, kept_keys
    subs, notes = [], []   # subs: (path, lang, title, forced, default)
    english_dialogue = project.dialogue == "en" or (project.dialogue != "foreign" and
                                                     float(getattr(project, "english_share", 1.0)) >= 0.25)
    style = getattr(filters, "star_style", "stars")
    kept = kept_keys(getattr(project, "subtitle_edits", []))
    try:
        import json as _json
        pic_cache = _json.loads((project.dir / "subtitles.json").read_text(encoding="utf-8"))
    except Exception:
        pic_cache = {}
    if settings.keep_subtitles or settings.make_subtitles:
        if settings.keep_subtitles:
            starred, primary = 0, False
            for s in info.subtitles:
                tags = s.get("tags", {})
                codec = s.get("codec_name") or ""
                label = tags.get("title") or LANG_NAMES.get(tags.get("language", ""), "Subtitles")
                forced = bool(s.get("disposition", {}).get("forced"))
                cues, from_pictures = None, False
                tmp = out.with_name(f"{out.stem}.sub{s['index']}.prude-temp.srt")
                if codec in TEXT_SUB_CODECS:
                    tmp_files.append(tmp)
                    if extract_text_sub(info.path, s["index"], tmp):
                        cues = read_srt(tmp)
                elif codec in IMAGE_SUB_CODECS:
                    cached = pic_cache.get(f"{s['index']}:{codec}")
                    if cached:
                        cues, from_pictures = [list(c) for c in cached], True
                        tmp_files.append(tmp)
                if not cues:
                    if codec in IMAGE_SUB_CODECS:
                        notes.append(f"Left out picture-based subtitles ({label}): "
                                     + ("only English ones are read." if not is_english_track(s)
                                        else "they couldn't be read."))
                    continue
                if is_english_track(s):
                    starred += clean_cues(cues, patterns, style, kept)
                cues = shift_cues(cues, cuts)
                if not cues:
                    continue
                write_srt(tmp, cues)
                show = (not english_dialogue) and is_english_track(s) and not primary
                primary = primary or show
                title = f"{label} (clean)" + (" - read from picture subtitles" if from_pictures else "")
                subs.append((tmp, tags.get("language"), title, forced or show, show))
            if subs:
                notes.insert(0, f"Cleaned {len(subs)} subtitle track(s); {starred} words changed in the subtitles.")
            if any(x[4] for x in subs):
                notes.append("The dialogue isn't English, so the English subtitles are set to show automatically.")
            if any("picture subtitles" in x[2] for x in subs):
                notes.append("Picture-based subtitles were converted to text with text recognition "
                             "(small reading mistakes are possible).")
        words = project.transcript.get("words") or []
        if settings.make_subtitles and words:
            side = out.with_name(f"{out.stem}.{project.language}.srt")
            cues = shift_cues(generate_cues(words, project.active_mutes(), patterns, style), cuts)
            if cues:
                write_srt(side, cues)
                subs.append((side, LANG3.get(project.language, project.language),
                             f"{'English' if project.language == 'en' else project.language} (Prude Awakening)",
                             False, False))
                notes.append(f"Made subtitles from the dialogue: {side.name}")

    # ---- filter graph
    # ---- blur only the exposed body parts (detected now, several times a second)
    vsrc, pgraph = "[0:v:0]", []
    if parts and info.video:
        from . import blurparts
        classes = blurparts.classes_for([t for t in filters.scenes if t.startswith("nude_")])
        samples = blurparts.detect(info, parts, classes, out.parent / f".{out.stem}.prude-blur",
                                   (lambda f, m: progress(0.03 * f, m)) if progress else None, cancel)
        found = [(a, b) for a, b in parts if any(a - 0.5 <= t <= b + 0.5 and bx for t, bx in samples)]
        missing = [r for r in parts if r not in found]
        if missing:
            log(f"No body parts found in {len(missing)} scene(s) marked for body-part blur; "
                "blurring the whole picture there instead.")
            blurs = merge_ranges(blurs + missing)
        if found:
            import uuid
            cmd_name = f"prude-blur-{uuid.uuid4().hex[:8]}.cmd"   # plain name: safe inside a filtergraph
            blurparts.write_commands(out.parent / cmd_name, samples, found, info.width, info.height)
            tmp_files.append(out.parent / cmd_name)
            pgraph, vsrc = blurparts.filter_parts(info, found, cmd_name, _between(
                [(a - 0.3, b + 0.3) for a, b in found]))
        shutil.rmtree(out.parent / f".{out.stem}.prude-blur", ignore_errors=True)
    # ---- words in subtitles burned into the picture: blur their spot while they show
    if burned and info.video:
        from . import burnin
        bgraph, vsrc = burnin.filter_graph(burned, vsrc)
        pgraph = pgraph + bgraph
        notes.append(f"Covered {len(burned)} word(s) in subtitles burned into the picture.")

    # ---- Advanced Editing (beta): a spoken clean word laid over the silence. The silence is
    # always there underneath, so a failure leaves a muted word and never the original.
    adv = getattr(filters, "advanced", None) or {}
    clips, spoken_chars = [], 0
    if adv.get("enabled") and adv.get("mode") in ("replace", "replace_lipsync") and mutes:
        from . import replace
        work = out.parent / f".{out.stem}.prude-voice"
        tmp_dirs.append(work)
        if progress:
            progress(0.0, "Making the replacement words (beta)")
        clips, failed, spoken_chars = replace.prepare(project, info, filters, settings, work, log, cancel)
        notes.append(f"Advanced Editing (beta): {len(clips)} word(s) replaced"
                     + (f", {len(failed)} left silent because the voice failed" if failed else "") + ".")
    base = 1 + len(subs) + (1 if fast_list else 0)
    inserts = [(base + n, c["start"]) for n, c in enumerate(clips)]
    graph = build_audio_chain(info, track, mutes, merge_ranges(silent_blacks + mute_scenes), cuts,
                              filters.mute_mode, fade=getattr(filters, "mute_fade", False), inserts=inserts)
    # ---- other audio tracks (other languages, commentary): kept as they are, NOT filtered
    others = []                 # (map target, raw stream, needs encoding)
    if export is not None and getattr(export, "other_audio", False):
        for j, t in enumerate(info.audio):
            if j == track:
                continue
            if cuts:
                removed = "+".join(f"gte(T,{b:.3f})*{b - a:.3f}" for a, b in cuts)
                graph.append(f"[0:a:{j}]aselect='not({_between(cuts)})',asetpts='PTS-({removed})/TB',"
                             f"aresample=async=1:first_pts=0[ao{j}]")
                others.append((f"[ao{j}]", t.raw, True))
            else:
                others.append((f"0:a:{j}", t.raw, out_ext in MP4_LIKE))
    vgraph = pgraph + build_video_chain(blackouts, [] if fast_list else cuts, blurs, vsrc, scale_h)
    graph += vgraph
    graph_text = ";".join(graph)

    if vgraph and info.dolby_vision_profile == 5:
        raise MediaError("This file is Dolby Vision profile 5 (streaming-style Dolby Vision without an HDR10 "
                         "base). Its colors can't be kept when the picture is changed, so scenes can't be cut, "
                         "blacked out or blurred in it. Word muting still works: set the scenes to "
                         "\"Silence\" or untick them, or use a different version of the movie.")
    cmd = [ffmpeg(), "-y", "-i", str(info.path)]
    for p, *_ in subs:
        cmd += ["-i", str(p)]
    if fast_list:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(fast_list)]
    for c in clips:
        cmd += ["-i", c["wav"]]
    if len(graph_text) > 20000:
        script = out.with_name(f"{out.stem}.prude-filter.txt")
        script.write_text(graph_text, encoding="utf-8")
        tmp_files.append(script)
        cmd += (["-/filter_complex", str(script)] if ffmpeg_major_version() >= 7
                else ["-filter_complex_script", str(script)])
    else:
        cmd += ["-filter_complex", graph_text]

    if vgraph:
        cmd += ["-map", "[vout]"]
    elif fast_list:
        cmd += ["-map", f"{len(subs) + 1}:v:0"]
    elif info.video:
        cmd += ["-map", f"0:{info.video['index']}"]
    cmd += ["-map", "[aout]"]
    for target, _raw, _enc in others:
        cmd += ["-map", target]
    for k in range(len(subs)):
        cmd += ["-map", f"{k + 1}:0"]

    cmd += ["-c", "copy"]
    how = "video copied untouched" + (" (fast cuts)" if fast_list else "")
    vcodec = info.video.get("codec_name")
    if vgraph:
        vargs, enc = video_encoder_args(info, quality, device)
        cmd += vargs
        how = f"video re-encoded on the {enc}"
        vcodec = "h264" if "libx264" in vargs else "hevc"
    aargs = audio_codec_args(st, out_ext)
    cmd += aargs
    for k, (_t, raw, enc) in enumerate(others, start=1):
        if enc:
            cmd += [a.replace(":a:0", f":a:{k}") for a in audio_codec_args(raw, out_ext)]
        tg = raw.get("tags", {})
        name = tg.get("title") or LANG_NAMES.get((tg.get("language") or "").lower(), "Audio")
        if tg.get("language"):
            cmd += [f"-metadata:s:a:{k}", f"language={tg['language']}"]
        cmd += [f"-metadata:s:a:{k}", f"title={name} (not filtered)", f"-disposition:a:{k}", "0"]
    if others:
        notes.append(f"Kept {len(others)} other audio track(s) as they are - they are NOT filtered.")
    if subs:
        cmd += ["-c:s", "mov_text" if out_ext in MP4_LIKE else "srt"]
        for j, (_p, lang, title, forced, default) in enumerate(subs):
            if lang:
                cmd += [f"-metadata:s:s:{j}", f"language={lang}"]
            disp = "+".join(x for x, on in (("default", default), ("forced", forced)) if on) or "0"
            cmd += [f"-metadata:s:s:{j}", f"title={title}", f"-disposition:s:{j}", disp]
    tags = st.get("tags", {})
    if tags.get("language"):
        cmd += ["-metadata:s:a:0", f"language={tags['language']}"]
    cmd += ["-metadata:s:a:0", "title=Filtered", "-disposition:a:0", "default", "-map_metadata", "0"]
    cmd += ["-map_chapters", "-1" if cuts else "0"]
    if out_ext in MP4_LIKE:
        if vcodec == "hevc":
            cmd += ["-tag:v", "hvc1"]
        cmd += ["-movflags", "+faststart"]
    partial = out.with_name(f"{out.stem}.partial{out.suffix}")   # renamed only when complete
    cmd += [str(partial)]

    log(f"Rendering: {how}; {len(mutes)} mutes, {len(cuts)} cuts, {len(blackouts)} blacked out, "
        f"{len(blurs)} blurred, {len(mute_scenes)} scenes muted.")
    try:
        log("ffmpeg " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd[1:])[:4000])
        run_ffmpeg(cmd, max(1.0, info.duration - cut_total),
                   (lambda f, m: progress(0.03 + 0.97 * f, m)) if (progress and parts) else progress,
                   cancel, "Re-encoding" if vgraph else "Copying", fps=info.fps, cwd=out.parent)
    except BaseException:
        _remove(partial)
        raise
    finally:
        for f in tmp_files:
            Path(f).unlink(missing_ok=True)
        if fast_work is not None:
            shutil.rmtree(fast_work, ignore_errors=True)
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    expected = max(1.0, info.duration - cut_total)
    ok, problems, got = validate(partial, expected, bool(info.video), bool(subs))
    import os
    os.replace(partial, out)
    if got is not None:
        got.path = out
    warnings = []
    if vgraph:
        why = []
        if cuts or blackouts or blurs or parts:
            why.append("scenes were changed")
        if burned:
            why.append("words in burned-in subtitles were covered")
        if scale_h:
            why.append(f"resized to {scale_h}p")
        warnings.append(f"Video re-encoded ({', '.join(why)}) at {'maximum' if quality == 'max' else 'very high'} "
                        f"quality - not bit-for-bit identical to the original.")
        if info.is_hdr:
            kept = got is not None and bool(got.hdr10_metadata()) if info.hdr10_metadata() else True
            if info.dolby_vision_profile:
                warnings.append("Dolby Vision was removed by re-encoding; the HDR10 picture is kept.")
            warnings.append("HDR kept (10-bit, HDR color)." + ("" if kept else
                            " The HDR brightness details couldn't be copied; TVs use their defaults."))
    else:
        warnings.append("Video copied bit-for-bit - identical picture quality.")
    src_a = AUDIO_NAMES.get(st.get("codec_name"), (st.get("codec_name") or "?").upper())
    dst_codec = aargs[aargs.index("-c:a:0") + 1]
    dst_a = AUDIO_NAMES.get(dst_codec, dst_codec.upper())
    lossless = dst_codec in ("flac", "alac")
    warnings.append(f"Audio re-encoded {src_a} -> {dst_a}" + (" (lossless)." if lossless else
                    " at high bitrate (needed to silence words)."))
    if fast_note:
        warnings.append(fast_note)
    warnings += notes
    result = {"output": str(out), "how": how, "mutes": len(mutes), "cuts": len(cuts),
            "blackouts": len(blackouts), "blurs": len(blurs), "muted_scenes": len(mute_scenes),
            "parts_blurred": len(parts), "burned_words": len(burned), "replaced_words": len(clips),
            "replacement_slots": _slots(clips, cuts),
            "elevenlabs_characters": spoken_chars,
            "removed_seconds": round(cut_total, 1), "notes": notes,
            "original_duration": round(info.duration, 2),
            "final_duration": round(got.duration if got else expected, 2),
            "size_bytes": out.stat().st_size if out.exists() else 0,
            "valid": ok, "problems": problems, "warnings": warnings,
            "resolution": f"{got.width}x{got.height}" if got and got.width else ""}
    return result
