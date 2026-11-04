"""How sure the app is about each finding, in plain words (for the Review screen).

Words: how clearly the speech recognizer heard them (and whether the subtitles agree).
Scenes: how far the detector's score is above the line for flagging."""

HIGH, MEDIUM, LOW, MINE = "high", "medium", "low", "mine"
NAMES = {HIGH: "Sure", MEDIUM: "Probably", LOW: "Unsure", MINE: "Yours"}
COLORS = {HIGH: "#2E7D4F", MEDIUM: "#B8860B", LOW: "#C8412B", MINE: "#6B566E"}


def word(m):
    """(level, reason) for a silenced word."""
    if m.get("manual"):
        return MINE, "You added this."
    if m.get("unsure"):
        return LOW, "The subtitles show this word but it wasn't clearly heard. Play it to check."
    if m.get("origin") == "subtitles":
        return HIGH, "Written in the movie's subtitles."
    if m.get("origin") == "burned-in":
        return MEDIUM, "Read from text printed in the picture (text recognition can slip)."
    if m.get("origin") == "subtitle-check":
        return HIGH, "Found in the subtitles and heard at the same moment."
    p = m.get("conf_p")
    if p is None:
        return MEDIUM, "Heard by speech recognition."
    if p >= 0.8:
        return HIGH, f"Heard clearly ({int(p * 100)}% sure of the word)."
    if p >= 0.5:
        return MEDIUM, f"Heard, but not very clearly ({int(p * 100)}% sure of the word)."
    return LOW, f"Barely heard ({int(p * 100)}% sure of the word): it may be a different word."


def scene(s):
    """(level, reason) for a scene."""
    if s.get("manual"):
        return MINE, "You added this."
    score, thr = s.get("score"), s.get("threshold")
    if score is None or thr is None:
        return MEDIUM, "Found by the scene detector."
    margin = (float(score) - float(thr)) / max(0.05, 1.0 - float(thr))
    pct = int(float(score) * 100)
    if margin >= 0.5:
        return HIGH, f"Clearly matched ({pct}% score)."
    if margin >= 0.2:
        return MEDIUM, f"Matched ({pct}% score), but not by a wide margin."
    return LOW, f"Only just over the line ({pct}% score): worth a look."


def for_item(item, kind):
    return word(item) if kind in ("mute", "sub", "burned") else scene(item)


def is_unsure(item, kind="mute"):
    return for_item(item, kind)[0] == LOW
