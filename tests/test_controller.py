import copy

from recorder.controller import GROUP, LABEL_CHANNEL, LABEL_STREAM_ID
from recorder.controller.jobs import BANDWIDTH_ANNOTATION, ChannelSpec, build_job, job_name
from recorder.controller.kube import ApiError
from recorder.controller.reconciler import Reconciler
from recorder.controller.scheduler import JobInfo, plan
from recorder.events import EventEmitter, ListSink

from helpers import stream

TEMPLATE = {
    "metadata": {"labels": {"app.kubernetes.io/name": "twitch-stream-recorder"}},
    "spec": {"backoffLimit": 3, "template": {"spec": {"restartPolicy": "Never", "containers": [
        {"name": "recorder", "image": "img", "command": ["python", "-m", "recorder.cli", "record"]}]}}},
}


def channel(login, priority=0, paused=False, bandwidth=None):
    return ChannelSpec(name=login.replace("_", "-"), namespace="rec", uid=f"uid-{login}", login=login,
                       priority=priority, paused=paused, bandwidth=bandwidth)


def resource(login, status=None, **spec):
    return {"metadata": {"name": login.replace("_", "-"), "namespace": "rec", "uid": f"uid-{login}",
                         "generation": 1},
            "spec": {"login": login, **spec}, "status": status or {}}


def test_job_name_is_dns_safe_and_bounded():
    assert job_name("valorant_tw", "40000000001") == "rec-valorant-tw-40000000001"
    assert len(job_name("a" * 25, "9" * 60)) <= 63


def test_build_job_stamps_identity_without_mutating_template():
    original = copy.deepcopy(TEMPLATE)
    job = build_job(channel("valorant_tw", bandwidth="20M"), stream(login="valorant_tw"), TEMPLATE)
    assert TEMPLATE == original
    assert job["metadata"]["name"] == "rec-valorant-tw-111"
    assert job["metadata"]["labels"][LABEL_CHANNEL] == "valorant-tw"
    assert job["metadata"]["ownerReferences"][0]["uid"] == "uid-valorant_tw"
    assert job["metadata"]["annotations"][f"{GROUP}/category"] == "Music"
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"][LABEL_STREAM_ID] == "111"
    assert pod["metadata"]["annotations"][BANDWIDTH_ANNOTATION] == "20M"
    assert pod["spec"]["containers"][0]["args"] == ["--login", "valorant_tw", "--quality", "best",
                                                   "--stream-id", "111", "--container", "mkv"]


def test_plan_respects_priority_cap_pause_and_existing_jobs():
    chans = [channel("a"), channel("b", priority=10), channel("c", paused=True), channel("d")]
    live = {c: stream(login=c, stream_id=f"{c}1") for c in "abcd"}
    jobs = [JobInfo(job_name("d", "d1"), "d", "d1", finished=False, failed=False)]
    p = plan(chans, live, jobs, max_concurrent=2)
    assert [c.login for c, _ in p.create] == ["b"]
    assert [c.login for c in p.deferred] == ["a"]
    assert plan(chans, live, jobs, max_concurrent=0).deferred == []


def test_job_info_reads_conditions():
    job = {"metadata": {"name": "rec-a-1", "labels": {LABEL_CHANNEL: "a", LABEL_STREAM_ID: "1"}},
           "status": {"conditions": [{"type": "Failed", "status": "True"}]}}
    info = JobInfo.from_job(job)
    assert info.finished and info.failed


class FakeKube:
    def __init__(self, resources):
        self.resources = resources
        self.jobs = []
        self.events = []
        self.status_patches = []

    def list_custom(self, *a):
        return self.resources

    def list_jobs(self, ns, selector):
        return self.jobs

    def create_job(self, ns, job):
        if any(j["metadata"]["name"] == job["metadata"]["name"] for j in self.jobs):
            raise ApiError(409, "exists")
        self.jobs.append(job)
        return job

    def create_event(self, ns, event):
        self.events.append(event)

    def patch_custom_status(self, group, version, ns, plural, name, status):
        self.status_patches.append((name, status))
        for r in self.resources:
            if r["metadata"]["name"] == name:
                r["status"] = {k: v for k, v in status.items() if v is not None}


class SeqHelix:
    def __init__(self, *ticks):
        self.ticks = list(ticks)

    def get_streams(self, logins):
        return self.ticks.pop(0)


def test_reconciler_schedules_job_and_reports_category_change():
    kube = FakeKube([resource("kana")])
    music = stream()
    game = stream(category=("516575", "VALORANT"), title="Ranked")
    sink = ListSink()
    rec = Reconciler(kube, SeqHelix({"kana": music}, {"kana": game}, {"kana": game}, {}), "rec", TEMPLATE,
                     EventEmitter([sink]), now=lambda: "2026-10-15T08:00:00Z")

    rec.tick()
    assert [j["metadata"]["name"] for j in kube.jobs] == ["rec-kana-111"]
    status = kube.resources[0]["status"]
    assert (status["phase"], status["categoryName"], status["activeJob"]) == ("Recording", "Music", "rec-kana-111")
    assert [e["reason"] for e in kube.events] == ["RecordingScheduled", "StreamOnline"]

    rec.tick()  # same broadcast, now a game: no new Job, one MetadataChanged event
    assert len(kube.jobs) == 1
    assert kube.events[-1]["reason"] == "MetadataChanged"
    assert "category Music -> VALORANT" in kube.events[-1]["message"]
    assert kube.resources[0]["status"]["categoryName"] == "VALORANT"

    patches = len(kube.status_patches)
    rec.tick()  # nothing changed: no status write
    assert len(kube.status_patches) == patches

    kube.jobs[0]["status"] = {"conditions": [{"type": "Complete", "status": "True"}]}
    rec.tick()  # stream ended
    assert kube.resources[0]["status"]["phase"] == "Offline"
    assert kube.events[-1]["reason"] == "StreamOffline"
    assert [e["event"] for e in sink.events].count("recording.scheduled") == 1


def test_reconciler_defers_over_cap():
    kube = FakeKube([resource("a"), resource("b", priority=5)])
    live = {"a": stream(login="a", stream_id="1"), "b": stream(login="b", stream_id="2")}
    sink = ListSink()
    Reconciler(kube, SeqHelix(live), "rec", TEMPLATE, EventEmitter([sink]), max_concurrent=1).tick()
    assert [j["metadata"]["name"] for j in kube.jobs] == ["rec-b-2"]
    phases = {r["metadata"]["name"]: r["status"]["phase"] for r in kube.resources}
    assert phases == {"a": "Deferred", "b": "Recording"}
    assert "recording.deferred" in [e["event"] for e in sink.events]
