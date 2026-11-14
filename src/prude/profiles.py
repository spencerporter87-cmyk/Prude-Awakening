"""Viewers: one movie, several people, each with their own choices.

One scan serves everybody: the speech transcript and the picture analysis are kept, so adding a viewer only
re-applies a different set of filters to what was already found (seconds, not hours). Each viewer keeps their own
ticked words and scenes, painted blurs, filter settings and leak-check results.

The project always holds ONE viewer "live" in its usual fields (mutes, scenes, paint, ...), so everything that
already works on a project keeps working. The other viewers wait in project.profiles as snapshots."""

from copy import deepcopy
from dataclasses import replace

from . import editlist

DEFAULT_NAME = "Default"


def names(project):
    """Every viewer of this movie, the live one first."""
    live = [project.profile] if project.profile else []
    return live + [n for n in project.profiles if n != project.profile]


def _snap(project, filters):
    snap = editlist.snapshot(project, filters)
    snap["leaks"] = deepcopy(getattr(project, "leaks", None) or {})
    return snap


def _load(project, snap):
    editlist.restore(project, snap)
    project.leaks = deepcopy(snap.get("leaks") or {})


def ensure_live(project, name=""):
    """Older projects have no viewer name yet: give the live choices one."""
    if not project.profile:
        project.profile = name or DEFAULT_NAME
    return project.profile


def adopt(project, name, filters=None):
    """A scan with this viewer's filters just ran into the live fields: that is now this viewer's state."""
    project.profiles.pop(name, None)             # the live state replaces any old snapshot of the same viewer
    project.profile = name
    return name


def stash(project, filters=None):
    """Save the live viewer's choices so another viewer can go live."""
    if project.profile:
        project.profiles[project.profile] = _snap(project, filters)


def switch(project, name, live_filters=None):
    """Make another viewer live. Returns that viewer's filters (a dict)."""
    if name == project.profile:
        return dict(live_filters or project.scan_filters or {})
    if name not in project.profiles:
        raise KeyError(f"There is no viewer called “{name}”.")
    stash(project, live_filters)
    snap = project.profiles.pop(name)
    _load(project, snap)
    project.profile = name
    project.save()
    return dict(snap.get("filters") or {})


def _by_hand(items):
    return [deepcopy(x) for x in items or [] if x.get("manual") or x.get("origin") == "manual"]


def create(project, name, filters, settings, live_filters=None, progress=None, cancel=None, log=print):
    """Add a viewer with `filters` (a FilterOptions) and make it live. What you made by hand (added words and scenes,
    painted blurs) comes along; the found words and scenes are worked out again for the new filters from the
    scan that already exists."""
    from .pipeline import scan
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the viewer a name.")
    ensure_live(project)
    if name in names(project):
        raise ValueError(f"There is already a viewer called “{name}”.")
    old = project.profile
    stash(project, live_filters)
    try:
        project.mutes, project.scenes = _by_hand(project.mutes), _by_hand(project.scenes)
        project.subtitle_edits, project.burned_edits = [], _by_hand(project.burned_edits)
        project.leaks = {}
        project.profile = name
        scan(project, filters, settings, progress, cancel, log)
        project.scan_filters = dict(filters.__dict__)
    except BaseException:                        # cancelled or failed: put everything back as it was
        snap = project.profiles.pop(old, None)
        if snap:
            _load(project, snap)
        project.profile = old
        project.profiles.pop(name, None)
        project.save()
        raise
    project.save()
    return project


def delete(project, name):
    """Remove a viewer who is not the live one."""
    if name == project.profile:
        raise ValueError("Switch to another viewer first.")
    project.profiles.pop(name, None)
    project.save()


def snapshots(project, live_filters=None):
    """{viewer: portable snapshot} of everybody, for an edit list."""
    out = {}
    ensure_live(project)
    for n in names(project):
        if n == project.profile:
            s = editlist.snapshot(project, live_filters, portable=True)
        else:
            s = deepcopy(project.profiles[n])
            s.pop("leaks", None)
            for sc in s.get("scenes", []):
                for k in editlist.LOCAL_ONLY:
                    sc.pop(k, None)
        out[n] = s
    return out


def import_doc(project, doc):
    """Bring every viewer of an edit list into the project. A viewer with a name already used gets '(imported)'.
    Returns {name in the file: name here}."""
    if not is_empty(project):
        ensure_live(project)                    # (an empty live state needs no name and no snapshot)
    mapping = {}
    for n, snap in doc["profiles"].items():
        name = n
        while name in names(project):
            name += " (imported)"
        project.profiles[name] = deepcopy(snap)
        mapping[n] = name
    return mapping


def is_empty(project):
    return not (project.mutes or project.scenes or project.paint or project.subtitle_edits or project.burned_edits)


def open_doc(project, doc, live_filters=None):
    """Use an edit list: its viewers join this movie's, and the one that was active in the file goes live.
    Returns (viewer, that viewer's filters)."""
    if is_empty(project) and not project.profiles:
        project.profile = ""                    # nothing to keep: don't leave an empty viewer behind
    mapping = import_doc(project, doc)
    target = mapping.get(doc.get("active")) or next(iter(mapping.values()))
    return target, switch(project, target, live_filters)


def viewer_options(export, name, multi):
    """Export options for one viewer's copy when several are made at once: each needs its own file name."""
    if not multi:
        return export
    naming = export.naming if export.naming in ("plex_edition", "simple") else "plex_edition"
    return replace(export, naming=naming, edition=f"{name} Edit", viewer=name)


def render_all(project, settings, which, export, live_filters=None, progress=None, cancel=None, log=print,
               check=True, listen=False):
    """Save one clean copy per viewer in `which`. Returns [(viewer, result)]. The viewer who was live is live again."""
    from .pipeline import render_project
    from .settings import FilterOptions
    start = project.profile
    cur_filters = live_filters
    out = []
    try:
        for i, name in enumerate(which):
            if name != project.profile:
                cur_filters = switch(project, name, cur_filters)
            f = FilterOptions.from_dict(cur_filters or project.scan_filters)
            cur_filters = asdict_filters(f)

            def prog(stage, frac, msg, i=i, name=name):
                if progress:
                    progress(stage, (i + frac) / len(which), f"{name}: {msg}" if msg else name)
            res = render_project(project, f, settings, None, prog, cancel, log,
                                 export=viewer_options(export, name, len(which) > 1))
            if check and res.get("valid", True):
                from . import leakcheck
                try:
                    lk = leakcheck.run(project, f, settings, result=res, picture=True, listen=listen,
                                       progress=prog, cancel=cancel, log=log)
                    res = dict(res, leak_summary=lk["summary"], leaks=len(lk["findings"]))
                except Exception as e:           # noqa: BLE001 - the copy is saved; the check is a bonus
                    if type(e).__name__ == "Cancelled":
                        raise
                    log(f"The check of {name}'s copy couldn't run: {e}")
            out.append((name, res))
    finally:
        if start and start != project.profile and start in project.profiles:
            switch(project, start, cur_filters)
    return out


def asdict_filters(f):
    from dataclasses import asdict
    return asdict(f)
