import subprocess
import sys
import threading
import time

from lab_service.jobs import Jobs
from lab_service.models import ExecResult
from lab_service.streaming import collect

E = {"X-Lab-Token": "e" * 32}
S = {"X-Lab-Token": "s" * 32}
BASE = "/v1/attacks/context-poisoning/jobs/"


def finished(client, job_id, headers=E):
    for _ in range(100):
        result = client.get("/v1/jobs/" + job_id, headers=headers).json()
        if result["status"] != "running":
            return result
        time.sleep(.01)
    raise AssertionError("Job did not finish")


def test_page_and_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Лаборатория агентов" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_job_roles_and_private_results(client, runner):
    assert client.post(BASE + "sandbox", headers=E, json={"command": "id"}).status_code == 403
    assert client.post(BASE + "evaluator", headers=S, json={"command": "id"}).status_code == 403
    assert client.post(BASE + "up", headers=S).status_code == 403
    response = client.post(BASE + "sandbox", headers=S, json={"command": "id"})
    assert response.status_code == 202
    job_id = response.json()["id"]
    result = finished(client, job_id, S)
    assert result["status"] == "completed"
    assert [c[0] for c in runner.calls] == ["sandbox"]
    assert client.get("/v1/jobs/" + job_id, headers=E).status_code == 404
    assert client.get("/v1/jobs", headers=E).json() == []
    assert client.get("/v1/jobs").status_code == 403
    assert len(client.get("/v1/jobs", headers=S).json()) == 1


def test_job_failure_and_validation(client, runner):
    assert client.post(BASE + "sandbox", headers=S).status_code == 422
    assert client.post(BASE + "bogus", headers=E).status_code == 422
    assert client.post("/v1/attacks/unknown/jobs/up", headers=E).status_code == 404
    runner.result = ExecResult(exit_code=124, timed_out=True)
    job_id = client.post(BASE + "up", headers=E).json()["id"]
    assert finished(client, job_id)["status"] == "failed"


def test_job_rejects_overlapping_vm_operations(client, runner):
    release = threading.Event()
    original = runner.up
    def slow(attack):
        release.wait(5)
        return original(attack)
    runner.up = slow
    try:
        assert client.post(BASE + "up", headers=E).status_code == 202
        assert client.post("/v1/attacks/tool-misuse/jobs/down", headers=E).status_code == 409
    finally:
        release.set()


def test_log_cursor_is_bounded_and_result_errors():
    from lab_service.jobs import log_sink
    jobs = Jobs()
    def operation():
        for i in range(300):
            log_sink.get()("stdout", str(i))
        raise RuntimeError("synthetic failure")
    try:
        job_id = jobs.submit("evaluator", "test", "up", "vm", operation)
        for _ in range(100):
            item = jobs.get(job_id, "evaluator")
            if item["status"] != "running":
                break
            time.sleep(.01)
        assert item["status"] == "failed"
        assert item["error"] == "synthetic failure"
        assert len(item["events"]) == 256 and item["truncated"]
        assert jobs.get(job_id, "evaluator", item["cursor"])["events"] == []
    finally:
        jobs.close()


def test_streaming_delivers_before_process_exit():
    p = subprocess.Popen([sys.executable, "-u", "-c", "import time,sys; print('first'); sys.stderr.write('error\\n'); time.sleep(.5); print('last')"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    events = []
    result = collect(p, 5, lambda stream, text: events.append((stream, text, p.poll())))
    assert result.exit_code == 0 and result.stdout == "first\nlast\n"
    assert result.stderr == "error\n"
    assert any("first" in text and status is None for _, text, status in events)


def test_streaming_timeout_retains_output():
    p = subprocess.Popen([sys.executable, "-u", "-c", "import time; print('before timeout'); time.sleep(20)"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    result = collect(p, .2, lambda *_: None)
    assert result.timed_out and result.exit_code == 124
    assert "before timeout" in result.stdout and p.poll() is not None
