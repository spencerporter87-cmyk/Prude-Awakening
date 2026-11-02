"""Making the clean copy.

* Only muting words: the video is copied byte-for-byte (fast, zero quality loss).
* Cutting scenes or blacking them out: the video is re-encoded at very high quality
  (NVIDIA graphics card when available), audio and subtitles are kept in sync."""

from pathlib import Path

from .media import (LANG3, LANG_NAMES, TEXT_SUB_CODECS, channel_layouts, encoder_works, encoders,
                    ffmpeg, ffmpeg_major_version, run_ffmpeg, stream_bitrate)
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
                "-b:v", "0", "-spatial-aq", "1", "-bf", "3"]
        args += ["-profile:v", "main10", "-pix_fmt", "p010le"] if tenbit else ["-pix_fmt", "yuv420p"]
        return args + color, "graphics card (NVENC HEVC)"
    if "libx265" in encoders() and (tenbit or info.is_hdr):
        args = ["-c:v", "libx265", "-preset", "slow" if quality == "max" else "medium",
                "-crf", "16" if quality == "max" else "18", "-pix_fmt", "yuv420p10le",
                "-x265-params", "log-level=error"]
        return args + color, "processor (x265, 10-bit)"
    args = ["-c:v", "libx264", "-preset", "slow" if quality == "max" else "medium",
            "-crf", "15" if quality == "max" else "17", "-pix_fmt", "yuv420p"]
    return args + color, "processor (x264)"


def build_audio_chain(info, track, mutes, blackouts, cuts, mute_mode):
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
        graph.append(f"[c{fc}]volume=0:enable='{_between(mutes)}'[cfc]")
        graph.append(f"{join_in}join=inputs={len(chans)}:channel_layout={layout}:map={jmap}[{label}]")
        chain = f"[{label}]anull"
        whole_mutes = list(blackouts)
    else:
        chain = src
        whole_mutes = merge_ranges(list(mutes) + list(blackouts))
    if whole_mutes:
        chain += f",volume=0:enable='{_between(whole_mutes)}'"
    if cuts:
        removed = "+".join(f"gte(T,{b:.3f})*{b - a:.3f}" for a, b in cuts)
        chain += (f",aselect='not({_between(cuts)})',asetpts='PTS-({removed})/TB',"
                  f"aresample=async=1:first_pts=0")
    graph.append(chain + "[aout]")
    return graph


def build_video_chain(blackouts, cuts):
    if not blackouts and not cuts:
        return []
    chain = "[0:v:0]"
    parts = []
    if blackouts:
        parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{_between(blackouts)}'")
    if cuts:
        removed = "+".join(f"gte(T,{b:.3f})*{b - a:.3f}" for a, b in cuts)
        parts.append(f"select='not({_between(cuts)})'")
        parts.append(f"setpts='PTS-({removed})/TB'")
    return [chain + ",".join(parts) + "[vout]"]


def render(info, project, filters, settings, patterns, out: Path, progress=None, cancel=None,
           log=print):
    """Returns a dict summary. filters: FilterOptions; settings: Settings."""
    track = project.audio_track
    st = info.audio[track].raw
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out_ext = out.suffix.lower()
    tmp_files = []

    mutes = merge_ranges((m["start"], m["end"]) for m in project.active_mutes())
    scenes = merge_ranges((s["start"], s["end"]) for s in project.active_scenes())
    cuts = scenes if filters.scene_action == "cut" else []
    blackouts = scenes if filters.scene_action != "cut" else []
    cut_total = sum(b - a for a, b in cuts)

    # ---- subtitles
    subs, notes = [], []   # subs: (path, lang, title, forced)
    if settings.keep_subtitles or settings.make_subtitles:
        if settings.keep_subtitles:
            starred = 0
            for s in info.subtitles:
                tags = s.get("tags", {})
                label = tags.get("title") or LANG_NAMES.get(tags.get("language", ""), "Subtitles")
                if s.get("codec_name") in TEXT_SUB_CODECS:
                    tmp = out.with_name(f"{out.stem}.sub{s['index']}.prude-temp.srt")
                    tmp_files.append(tmp)
                    if extract_text_sub(info.path, s["index"], tmp):
                        cues = read_srt(tmp)
                        starred += clean_cues(cues, patterns)
                        write_srt(tmp, shift_cues(cues, cuts))
                        subs.append((tmp, tags.get("language"), f"{label} (clean)",
                                     bool(s.get("disposition", {}).get("forced"))))
                        continue
                notes.append(f"Left out picture-based subtitles ({label}) — they can't be edited.")
            if subs:
                notes.insert(0, f"Cleaned {len(subs)} subtitle track(s); {starred} words starred out.")
        words = project.transcript.get("words") or []
        if settings.make_subtitles and words:
            side = out.with_name(f"{out.stem}.{project.language}.srt")
            cues = generate_cues(words, project.active_mutes(), patterns)
            write_srt(side, shift_cues(cues, cuts))
            subs.append((side, LANG3.get(project.language, project.language),
                         f"{'English' if project.language == 'en' else project.language} (Prude Awakening)", False))
            notes.append(f"Made subtitles from the dialogue: {side.name}")

    # ---- filter graph
    graph = build_audio_chain(info, track, mutes, blackouts, cuts, filters.mute_mode)
    vgraph = build_video_chain(blackouts, cuts)
    graph += vgraph
    graph_text = ";".join(graph)

    cmd = [ffmpeg(), "-y", "-i", str(info.path)]
    for p, *_ in subs:
        cmd += ["-i", str(p)]
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
    elif info.video:
        cmd += ["-map", f"0:{info.video['index']}"]
    cmd += ["-map", "[aout]"]
    for k in range(len(subs)):
        cmd += ["-map", f"{k + 1}:0"]

    cmd += ["-c", "copy"]
    how = "video copied untouched"
    vcodec = info.video.get("codec_name")
    if vgraph:
        vargs, enc = video_encoder_args(info, settings.video_quality, settings.device)
        cmd += vargs
        how = f"video re-encoded on the {enc}"
        vcodec = "h264" if "libx264" in vargs else "hevc"
    cmd += audio_codec_args(st, out_ext)
    if subs:
        cmd += ["-c:s", "mov_text" if out_ext in MP4_LIKE else "srt"]
        for j, (_p, lang, title, forced) in enumerate(subs):
            if lang:
                cmd += [f"-metadata:s:s:{j}", f"language={lang}"]
            cmd += [f"-metadata:s:s:{j}", f"title={title}", f"-disposition:s:{j}", "forced" if forced else "0"]
    tags = st.get("tags", {})
    if tags.get("language"):
        cmd += ["-metadata:s:a:0", f"language={tags['language']}"]
    cmd += ["-metadata:s:a:0", "title=Filtered", "-disposition:a:0", "default", "-map_metadata", "0"]
    cmd += ["-map_chapters", "-1" if cuts else "0"]
    if out_ext in MP4_LIKE:
        if vcodec == "hevc":
            cmd += ["-tag:v", "hvc1"]
        cmd += ["-movflags", "+faststart"]
    cmd += [str(out)]

    log(f"Rendering: {how}; {len(mutes)} mutes, {len(cuts)} cuts, {len(blackouts)} blacked-out scenes.")
    try:
        run_ffmpeg(cmd, max(1.0, info.duration - cut_total), progress, cancel,
                   "Making the clean copy" + (" (re-encoding video)" if vgraph else ""))
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        for f in tmp_files:
            Path(f).unlink(missing_ok=True)
    return {"output": str(out), "how": how, "mutes": len(mutes), "cuts": len(cuts),
            "blackouts": len(blackouts), "removed_seconds": round(cut_total, 1), "notes": notes}
