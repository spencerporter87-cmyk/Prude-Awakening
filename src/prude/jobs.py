"""Batch processing: a queue of movies/episodes that is saved to disk, so it survives
closing the app. Jobs run one at a time (the graphics card does one heavy thing at once)."""

import json
import re
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .media import VIDEO_EXTS, Cancelled, pick_audio_track, probe
from .paths import app_data_dir, write_atomic
from .render import paint_in_use

QUEUED, RUNNING, REVIEW, DONE, FAILED, CANCELLED = (
    "queued", "running", "needs_review", "done", "failed", "cancelled")
STATUS_TEXT = {QUEUED: "Waiting", RUNNING: "Working", REVIEW: "Ready to review", DONE: "Saved",
               FAILED: "Failed", CANCELLED: "Cancelled"}

# how much of a job's progress bar each step takes (scan first, then saving)
STAGE_SPAN = {"gpu": (0.0, 0.02), "audio": (0.0, 0.04), "speech": (0.04, 0.35), "words": (0.35, 0.355), "subs": (0.355, 0.358), "burned": (0.358, 0.36),
              "models": (0.36, 0.38), "frames": (0.38, 0.48), "analyze": (0.48, 0.6), "render": (0.6, 0.92),
              "check": (0.92, 1.0)}


@dataclass
class Job:
    source: str
    preset: str = "Custom"
    filters: dict = field(default_factory=dict)
    export: dict = field(default_factory=dict)
    mode: str = "save"                 # save | review
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = QUEUED
    progress: float = 0.0
    message: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    attempts: int = 0
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    log: list = field(default_factory=list)

    @property
    def name(self):
        return Path(self.source).name

    def add_log(self, msg):
        self.log = (self.log + [f"{time.strftime('%H:%M:%S')}  {msg}"])[-300:]


def _natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def expand_sources(paths):
    """Files and folders (searched inside) -> video files, in natural order."""
    out = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out += sorted((f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTS
                           and "(clean)" not in f.name and "{edition-" not in f.name
                           and ".partial." not in f.name), key=_natural)
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            out.append(p)
    seen, uniq = set(), []
    for f in out:
        k = str(f.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


class Queue:
    def __init__(self, path=None):
        self.path = Path(path) if path else app_data_dir() / "queue.json"
        self.lock = threading.RLock()
        self.jobs = []
        self.load()

    # ------------------------------------------------------------ storage
    def load(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.jobs = [Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
                             for d in data.get("jobs", [])]
            except Exception:
                self.jobs = []
            for j in self.jobs:          # the app was closed (or crashed) mid-job
                if j.status == RUNNING:
                    j.status, j.progress = QUEUED, 0.0
                    j.message = "Interrupted - will start again (finished steps are reused)"
                    j.add_log("Interrupted; queued again.")

    def save(self):
        with self.lock:
            write_atomic(self.path, json.dumps({"jobs": [asdict(j) for j in self.jobs]}))

    # ------------------------------------------------------------ changes
    def add(self, sources, preset, filters, export, mode="save"):
        added = []
        with self.lock:
            active = {(str(Path(j.source).resolve()).lower(), j.preset) for j in self.jobs
                      if j.status in (QUEUED, RUNNING)}
            for s in sources:
                key = (str(Path(s).resolve()).lower(), preset)
                if key in active:
                    continue
                j = Job(source=str(Path(s).resolve()), preset=preset, filters=dict(filters),
                        export=dict(export), mode=mode)
                j.add_log(f"Added with preset '{preset}' ({'save automatically' if mode == 'save' else 'stop for review'}).")
                self.jobs.append(j)
                added.append(j)
            self.save()
        return added

    def get(self, job_id):
        return next((j for j in self.jobs if j.id == job_id), None)

    def next_job(self):
        with self.lock:
            return next((j for j in self.jobs if j.status == QUEUED), None)

    def update(self, job, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(job, k, v)
            self.save()

    def retry(self, job):
        with self.lock:
            if job.status in (FAILED, CANCELLED, DONE, REVIEW):
                job.status, job.progress, job.error, job.message = QUEUED, 0.0, "", "Waiting to try again"
                job.add_log("Queued again.")
                self.save()

    def remove(self, job):
        with self.lock:
            if job.status != RUNNING:
                self.jobs = [j for j in self.jobs if j.id != job.id]
                self.save()

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j.status in (QUEUED, RUNNING, FAILED)]
            self.save()

    def counts(self):
        out = {}
        for j in self.jobs:
            out[j.status] = out.get(j.status, 0) + 1
        return out


def process_job(job, settings, progress=None, cancel=None, log=print):
    """Scan one movie and either save it with the preset's choices or stop for review.
    progress(fraction, message) covers the whole job."""
    from .export import ExportOptions
    from .pipeline import render_project, scan
    from .project import Project
    from .settings import FilterOptions

    review_only = job.mode == "review"

    def stage_progress(stage, frac, msg):
        if not progress:
            return
        a, b = STAGE_SPAN.get(stage, (0.0, 1.0))
        total = a + (b - a) * max(0.0, min(1.0, frac))
        progress(total / 0.6 if review_only else total, msg)

    src = Path(job.source)
    if not src.exists():
        raise FileNotFoundError(f"The file is no longer there: {src}")
    project = Project.open(src)
    info = probe(src)
    if not info.audio:
        raise ValueError("This file has no audio track.")
    if not project.transcript:
        project.audio_track = pick_audio_track(info, project.language)
    f = FilterOptions.from_dict(job.filters)
    scan(project, f, settings, stage_progress, cancel, log)
    subs_on = [x for x in project.subtitle_edits + project.burned_edits if x.get("on", True)]
    found = {"mutes": len(project.mutes), "scenes": len(project.scenes), "subtitle_words": len(subs_on)}
    if review_only:
        return dict(found, review=True)
    if not project.active_mutes() and not project.active_scenes() and not subs_on and not paint_in_use(project):
        return dict(found, skipped="Nothing to change - no copy was made.")
    opts = ExportOptions.from_dict(job.export)
    result = render_project(project, f, settings, progress=stage_progress, cancel=cancel, log=log, export=opts)
    out = dict(found, **{k: v for k, v in result.items() if k != "notes"})
    if opts.check_copy and result.get("valid", True):
        from . import leakcheck
        try:
            lk = leakcheck.run(project, f, settings, result=result, picture=True, listen=opts.check_listen,
                               progress=stage_progress, cancel=cancel, log=log)
            out["leaks"] = len(lk["findings"])
        except Cancelled:
            raise
        except Exception as e:                      # noqa: BLE001 - the copy is saved; the check is a bonus
            log(f"The check of the finished copy couldn't run: {e}")
    return out


def run_queue(queue, settings, on_change=None, should_stop=None, cancel=None, log_fn=None):
    """Work through the queue until it's empty or should_stop() says so (checked between jobs).
    on_change(job) is called whenever a job changes. Used by the app's batch runner and tests."""
    while not (should_stop and should_stop()):
        job = queue.next_job()
        if job is None:
            return
        if cancel is not None:
            cancel.clear()
        queue.update(job, status=RUNNING, started=time.time(), attempts=job.attempts + 1,
                     progress=0.0, message="Starting…", error="")
        job.add_log(f"Started (attempt {job.attempts}).")
        if on_change:
            on_change(job)
        last = [0.0]

        def prog(frac, msg):
            job.progress, job.message = frac, msg
            if on_change and (time.time() - last[0] > 0.25 or frac >= 1.0):
                last[0] = time.time()
                on_change(job)

        def jlog(msg):
            job.add_log(msg)
            if log_fn:
                log_fn(f"[{job.name}] {msg}")
        try:
            res = process_job(job, settings, prog, cancel, jlog)
            status = REVIEW if res.get("review") else DONE
            msg = (f"Found {res['mutes']} words and {res['scenes']} scenes - open it to review" if res.get("review")
                   else res.get("skipped") or f"Saved: {Path(res['output']).name}"
                   + (f" - {res['leaks']} thing(s) to look at" if res.get("leaks") else ""))
            if res.get("valid") is False:
                msg = "Saved, but the check found problems - see the log"
                job.add_log("Check problems: " + "; ".join(res.get("problems", [])))
            queue.update(job, status=status, progress=1.0, message=msg, result=res, finished=time.time())
            job.add_log(msg)
        except Cancelled:
            queue.update(job, status=CANCELLED, message="Cancelled", finished=time.time())
            job.add_log("Cancelled.")
        except Exception as e:
            queue.update(job, status=FAILED, message="Failed - see the log", error=str(e) or type(e).__name__,
                         finished=time.time())
            job.add_log("Failed: " + (str(e) or type(e).__name__))
            job.add_log(traceback.format_exc()[-2500:])
        queue.save()
        if on_change:
            on_change(job)
