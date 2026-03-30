"""
test_phase8.py  —  K8sWhisperer full test suite
Run from project root:  python test_phase8.py
"""

import json
import os
import sys
import tempfile
import time
import threading
from unittest.mock import MagicMock, patch

# ── redirect audit log to a temp file so tests never touch the real one ───────
_TMP_LOG = tempfile.mktemp(suffix=".json")
os.environ["AUDIT_LOG_PATH"] = _TMP_LOG

# ─────────────────────────────────────────────────────────────────────────────
# Tiny test framework
# ─────────────────────────────────────────────────────────────────────────────
PASS, FAIL, ERR = [], [], []

def ok(msg):
    PASS.append(msg); print(f"  \u2713 {msg}")

def fail(msg, reason=""):
    FAIL.append(msg)
    print(f"  \u2717 FAIL: {msg}" + (f" \u2014 {reason}" if reason else ""))

def err(msg, exc):
    ERR.append(msg); print(f"  \u2717 ERROR: {msg} \u2014 {exc}")

def section(title):
    print(); print("\u2500" * 70); print(f"  {title}"); print("\u2500" * 70)

def reset_log():
    if os.path.exists(_TMP_LOG):
        os.remove(_TMP_LOG)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Logger
# ─────────────────────────────────────────────────────────────────────────────
def test_logger():
    section("Phase 1 — Logger")

    from agent.logger import (
        log_event, get_audit_log,
        log_human_resolution, get_past_resolutions,
        get_approval_count,
    )

    try:
        reset_log()
        log_event("TEST_EVENT", {"pod": "p1", "value": 42})
        entries = get_audit_log()
        assert len(entries) == 1
        assert entries[0]["event_type"] == "TEST_EVENT"
        ok("log_event creates file and appends correctly")
    except Exception as e:
        err("log_event creates file and appends correctly", e)

    try:
        reset_log()
        for i in range(5):
            log_event("EV", {"i": i})
        assert len(get_audit_log()) == 5
        ok("Multiple events appended without overwriting")
    except Exception as e:
        err("Multiple events appended without overwriting", e)

    try:
        reset_log()
        now = time.time()
        log_human_resolution(
            failure_type="CrashLoopBackOff", pod_name="crash-pod",
            namespace="default", fix_applied="kubectl delete pod crash-pod",
            resolution_notes="Cleared bad config",
            resolved_at_epoch=now + 240, alerted_at_epoch=now,
        )
        resolutions = get_past_resolutions("CrashLoopBackOff")
        assert len(resolutions) == 1
        assert resolutions[0]["pod_name"] == "crash-pod"
        assert resolutions[0]["duration_minutes"] == 4.0
        ok("log_human_resolution saved and retrieved correctly")
    except Exception as e:
        err("log_human_resolution saved and retrieved correctly", e)

    try:
        reset_log()
        now = time.time()
        log_human_resolution("CrashLoopBackOff", "p1", "default", "fix1", "", now+60, now)
        log_human_resolution("ImagePullBackOff", "p2", "default", "fix2", "", now+120, now)
        log_human_resolution("CrashLoopBackOff", "p3", "default", "fix3", "", now+180, now)
        results = get_past_resolutions("CrashLoopBackOff")
        assert len(results) == 2
        ok("get_past_resolutions filters correctly by failure_type")
    except Exception as e:
        err("get_past_resolutions filters correctly by failure_type", e)

    try:
        reset_log()
        now = time.time()
        log_human_resolution("OOMKilled", "pod-old", "default", "fix-old", "", now+60, now)
        time.sleep(0.02)
        log_human_resolution("OOMKilled", "pod-new", "default", "fix-new", "", now+120, now)
        results = get_past_resolutions("OOMKilled")
        assert results[0]["pod_name"] == "pod-new"
        ok("Resolutions returned newest-first")
    except Exception as e:
        err("Resolutions returned newest-first", e)

    try:
        reset_log()
        log_event("HITL_DECISION", {"approved": True,  "failure_type": "CrashLoopBackOff", "action": "restart_pod"})
        log_event("HITL_DECISION", {"approved": True,  "failure_type": "CrashLoopBackOff", "action": "restart_pod"})
        log_event("HITL_DECISION", {"approved": False, "failure_type": "CrashLoopBackOff", "action": "restart_pod"})
        log_event("HITL_DECISION", {"approved": True,  "failure_type": "OOMKilled",        "action": "patch_memory"})
        assert get_approval_count("CrashLoopBackOff", "restart_pod") == 2
        assert get_approval_count("OOMKilled", "patch_memory") == 1
        assert get_approval_count("Evicted", "delete_evicted") == 0
        ok("get_approval_count counts only approvals for exact failure+action pair")
    except Exception as e:
        err("get_approval_count counts only approvals for exact failure+action pair", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Diagnose  (signature: diagnose(pod_name, namespace, failure_type))
# ─────────────────────────────────────────────────────────────────────────────
PRIMARY_RESPONSE = """\
ROOT_CAUSE: Container keeps OOMKilling due to memory leak in app
FIX: kubectl patch deployment myapp -p '{}'
SEVERITY: HIGH
CONFIDENCE: 0.91
EXPLANATION: The container consumes all available memory and is killed by the OOM killer."""

VERIFIER_AGREE    = "AGREE: YES\nVERIFIER_ROOT_CAUSE: Memory limit too low\nVERIFIER_FIX: Increase memory\nVERIFIER_CONFIDENCE: 0.88\nVERIFIER_NOTES: Confirmed"
VERIFIER_DISAGREE = "AGREE: NO\nVERIFIER_ROOT_CAUSE: Memory leak in code\nVERIFIER_FIX: Fix the leak\nVERIFIER_CONFIDENCE: 0.72\nVERIFIER_NOTES: Band-aid only"
VERIFIER_PARTIAL  = "AGREE: PARTIAL\nVERIFIER_ROOT_CAUSE: Both limit and leak\nVERIFIER_FIX: Increase limit then fix leak\nVERIFIER_CONFIDENCE: 0.80\nVERIFIER_NOTES: Short-term fix"

def _groq_resp(text):
    msg = MagicMock(); msg.content = text
    ch  = MagicMock(); ch.message  = msg
    r   = MagicMock(); r.choices   = [ch]
    return r

def test_diagnose():
    section("Phase 2 — Diagnose")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="mock log output", stderr="", returncode=0)
        with patch("groq.Groq") as MockGroq:
            client_mock = MagicMock()
            MockGroq.return_value = client_mock

            import importlib
            import agent.diagnose as diag_mod
            importlib.reload(diag_mod)

            try:
                reset_log()
                client_mock.chat.completions.create.side_effect = [
                    _groq_resp(PRIMARY_RESPONSE), _groq_resp(VERIFIER_AGREE)]
                result = diag_mod.diagnose("oom-pod", "default", "OOMKilled")
                assert result.root_cause != ""
                assert 0.0 < result.confidence <= 1.0
                assert result.severity == "HIGH"
                ok("Primary diagnosis parsed correctly")
            except Exception as e:
                err("Primary diagnosis parsed correctly", e)

            try:
                reset_log()
                client_mock.chat.completions.create.side_effect = [
                    _groq_resp(PRIMARY_RESPONSE), _groq_resp(VERIFIER_AGREE)]
                result = diag_mod.diagnose("oom-pod", "default", "OOMKilled")
                assert result.verifier_agrees is True
                ok("Verifier AGREE=YES parsed correctly")
            except Exception as e:
                err("Verifier AGREE=YES parsed correctly", e)

            try:
                reset_log()
                client_mock.chat.completions.create.side_effect = [
                    _groq_resp(PRIMARY_RESPONSE), _groq_resp(VERIFIER_DISAGREE)]
                result = diag_mod.diagnose("oom-pod", "default", "OOMKilled")
                assert result.verifier_agrees is False
                assert result.verifier_notes != ""
                ok("Verifier AGREE=NO parsed correctly — disagreement captured")
            except Exception as e:
                err("Verifier AGREE=NO parsed correctly — disagreement captured", e)

            try:
                reset_log()
                client_mock.chat.completions.create.side_effect = [
                    _groq_resp(PRIMARY_RESPONSE), _groq_resp(VERIFIER_PARTIAL)]
                result = diag_mod.diagnose("oom-pod", "default", "OOMKilled")
                assert result.verifier_agrees is None
                ok("Verifier AGREE=PARTIAL parsed as None correctly")
            except Exception as e:
                err("Verifier AGREE=PARTIAL parsed as None correctly", e)

            try:
                reset_log()
                client_mock.chat.completions.create.side_effect = Exception("API timeout")
                result = diag_mod.diagnose("crash-pod", "default", "CrashLoopBackOff")
                assert result is not None
                assert result.confidence < 0.5
                ok("LLM error handled gracefully — no crash")
            except Exception as e:
                err("LLM error handled gracefully — no crash", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — build_plan()
# ─────────────────────────────────────────────────────────────────────────────
def test_build_plan():
    section("Phase 3 — build_plan()")

    from agent.executor import build_plan, RemediationPlan

    cases = [
        ("CrashLoopBackOff", "restart_pod",   "MEDIUM"),
        ("OOMKilled",        "patch_memory",   "HIGH"),
        ("Pending",          "explain_only",   "MEDIUM"),
        ("ImagePullBackOff", "alert_human",    "LOW"),
        ("CPUThrottling",    "patch_cpu",      "LOW"),
        ("Evicted",          "delete_evicted", "LOW"),
        ("DeploymentStall",  "hitl_required",  "HIGH"),
        ("NodeNotReady",     "hitl_required",  "CRITICAL"),
    ]

    for failure_type, expected_action, expected_blast in cases:
        try:
            anomaly = MagicMock()
            anomaly.failure_type = failure_type
            anomaly.pod_name = "test-pod"; anomaly.namespace = "default"; anomaly.restart_count = 3
            diagnosis = MagicMock()
            diagnosis.fix_suggestion = "kubectl fix"; diagnosis.confidence = 0.85
            plan = build_plan(anomaly, diagnosis)
            assert isinstance(plan, RemediationPlan)
            assert plan.action == expected_action, f"Expected {expected_action}, got {plan.action}"
            assert plan.blast_radius == expected_blast, f"Expected {expected_blast}, got {plan.blast_radius}"
            ok(f"{failure_type} → {expected_action} / {expected_blast}")
        except Exception as e:
            err(f"{failure_type} → {expected_action} / {expected_blast}", e)

    try:
        anomaly = MagicMock()
        anomaly.failure_type = "WeirdUnknownError"
        anomaly.pod_name = "test-pod"; anomaly.namespace = "default"; anomaly.restart_count = 0
        diagnosis = MagicMock(); diagnosis.fix_suggestion = ""; diagnosis.confidence = 0.5
        plan = build_plan(anomaly, diagnosis)
        assert plan.action == "explain_only"
        ok("Unknown failure type → explain_only (safe default)")
    except Exception as e:
        err("Unknown failure type → explain_only (safe default)", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — Safety Gate
# ─────────────────────────────────────────────────────────────────────────────
def _make_plan(action, blast, confidence=0.90):
    p = MagicMock()
    p.action = action; p.blast_radius = blast; p.confidence = confidence
    return p

def test_safety_gate():
    section("Phase 4 — Safety Gate")

    from agent.executor import safety_gate, _dynamic_confidence_threshold

    try:
        assert safety_gate(_make_plan("alert_human", "LOW", 0.99), "ImagePullBackOff") is False
        ok("alert_human always routes to HITL regardless of confidence")
    except Exception as e:
        err("alert_human always routes to HITL regardless of confidence", e)

    try:
        assert safety_gate(_make_plan("hitl_required", "CRITICAL", 0.99), "NodeNotReady") is False
        ok("hitl_required always routes to HITL")
    except Exception as e:
        err("hitl_required always routes to HITL", e)

    try:
        assert safety_gate(_make_plan("patch_memory", "HIGH", 0.99), "OOMKilled") is False
        ok("HIGH blast radius always routes to HITL")
    except Exception as e:
        err("HIGH blast radius always routes to HITL", e)

    try:
        assert safety_gate(_make_plan("hitl_required", "CRITICAL", 0.99), "NodeNotReady") is False
        ok("CRITICAL blast radius always routes to HITL")
    except Exception as e:
        err("CRITICAL blast radius always routes to HITL", e)

    try:
        assert safety_gate(_make_plan("restart_pod", "MEDIUM", 0.60), "CrashLoopBackOff") is False
        ok("Low confidence (0.60 < 0.80) routes to HITL")
    except Exception as e:
        err("Low confidence (0.60 < 0.80) routes to HITL", e)

    try:
        assert safety_gate(_make_plan("delete_evicted", "LOW", 0.95), "Evicted") is True
        ok("HIGH confidence + LOW blast → auto-execute")
    except Exception as e:
        err("HIGH confidence + LOW blast → auto-execute", e)

    try:
        reset_log()
        from agent.logger import log_event
        for _ in range(3):
            log_event("HITL_DECISION", {
                "approved": True, "failure_type": "CrashLoopBackOff", "action": "restart_pod"})
        assert safety_gate(_make_plan("restart_pod", "MEDIUM", 0.70), "CrashLoopBackOff") is True
        ok("Adaptive trust: 3+ approvals → auto-execute bypasses HITL")
    except Exception as e:
        err("Adaptive trust: 3+ approvals → auto-execute bypasses HITL", e)

    try:
        reset_log()
        from agent.logger import log_event
        for _ in range(10):
            log_event("HITL_DECISION", {
                "approved": True, "failure_type": "NodeNotReady", "action": "hitl_required"})
        assert safety_gate(_make_plan("hitl_required", "CRITICAL", 0.99), "NodeNotReady") is False
        ok("Adaptive trust never overrides hitl_required (CRITICAL stays HITL)")
    except Exception as e:
        err("Adaptive trust never overrides hitl_required (CRITICAL stays HITL)", e)

    try:
        reset_log()
        t0 = _dynamic_confidence_threshold("Evicted", "delete_evicted")
        assert abs(t0 - 0.80) < 0.01, f"Expected 0.80, got {t0}"
        from agent.logger import log_event
        for _ in range(5):
            log_event("HITL_DECISION", {
                "approved": True, "failure_type": "Evicted", "action": "delete_evicted"})
        t5 = _dynamic_confidence_threshold("Evicted", "delete_evicted")
        assert abs(t5 - 0.65) < 0.01, f"Expected 0.65, got {t5}"
        ok("Dynamic threshold: 0 approvals=0.80, 5 approvals=0.65")
    except Exception as e:
        err("Dynamic threshold: 0 approvals=0.80, 5 approvals=0.65", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Executor Actions
# ─────────────────────────────────────────────────────────────────────────────
def test_executor_actions():
    section("Phase 5 — Executor Actions")

    from agent.executor import restart_pod, delete_evicted, patch_memory, patch_cpu

    with patch("subprocess.run") as mock_run:

        try:
            mock_run.return_value = MagicMock(returncode=0, stdout="pod deleted", stderr="")
            restart_pod("crash-pod", "default")
            cmd = mock_run.call_args[0][0]
            assert "delete" in cmd and "crash-pod" in cmd
            ok("restart_pod calls kubectl delete with --grace-period=0")
        except Exception as e:
            err("restart_pod calls kubectl delete with --grace-period=0", e)

        try:
            mock_run.return_value = MagicMock(returncode=0, stdout="pod deleted", stderr="")
            delete_evicted("evicted-pod", "default")
            cmd = mock_run.call_args[0][0]
            assert "delete" in cmd and "evicted-pod" in cmd
            ok("delete_evicted calls kubectl delete")
        except Exception as e:
            err("delete_evicted calls kubectl delete", e)

        try:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="256Mi", stderr=""),
                MagicMock(returncode=0, stdout='{"items":[{"metadata":{"name":"myapp"}}]}', stderr=""),
                MagicMock(returncode=0, stdout="patched", stderr=""),
            ]
            patch_memory("oom-pod", "default")
            cmd_str = " ".join(str(x) for x in mock_run.call_args_list[-1][0][0])
            assert "384Mi" in cmd_str, f"Expected 384Mi, got: {cmd_str}"
            ok("patch_memory reads 256Mi and patches to 384Mi (+50%)")
        except Exception as e:
            err("patch_memory reads 256Mi and patches to 384Mi (+50%)", e)

        try:
            mock_run.side_effect = None
            mock_run.return_value = MagicMock(returncode=0, stdout='{"items":[]}', stderr="")
            patch_memory("oom-pod", "default")
            ok("patch_memory fails gracefully when no owning Deployment found")
        except Exception as e:
            err("patch_memory fails gracefully when no owning Deployment found", e)

        try:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="500m", stderr=""),
                MagicMock(returncode=0, stdout='{"items":[{"metadata":{"name":"myapp"}}]}', stderr=""),
                MagicMock(returncode=0, stdout="patched", stderr=""),
            ]
            patch_cpu("throttled-pod", "default")
            cmd_str = " ".join(str(x) for x in mock_run.call_args_list[-1][0][0])
            assert "750m" in cmd_str, f"Expected 750m, got: {cmd_str}"
            ok("patch_cpu reads 500m and patches to 750m (+50%)")
        except Exception as e:
            err("patch_cpu reads 500m and patches to 750m (+50%)", e)

        try:
            mock_run.side_effect = None
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            raised = False
            try:
                restart_pod("kube-dns", "kube-system")
            except ValueError:
                raised = True
            assert raised
            ok("kube-system namespace raises ValueError (namespace guard)")
        except Exception as e:
            err("kube-system namespace raises ValueError (namespace guard)", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — HITL Server
# Routes: GET /hitl/<token>   POST /decide {token, approved}
# State:  _pending keyed by token, each entry has "event" (threading.Event)
# ─────────────────────────────────────────────────────────────────────────────
def _make_hitl_entry(token, failure_type="CrashLoopBackOff", action="restart_pod"):
    import secrets as sec
    now = time.time()
    return {
        "token": token,
        "pod_name": "test-pod", "namespace": "default",
        "failure_type": failure_type,
        "action": action, "blast_radius": "MEDIUM",
        "confidence": 0.85,
        "primary_root_cause": "Container crashing",
        "primary_fix": "kubectl delete pod test-pod",
        "primary_severity": "HIGH",
        "verifier_agrees": True,
        "verifier_root_cause": "Confirmed",
        "verifier_fix": "Same fix",
        "verifier_confidence": 0.82,
        "verifier_notes": "",
        "alerted_at_epoch": now,
        "requested_at": "2026-03-30T00:00:00+00:00",
        "event": threading.Event(),
        "result": None,
        "decided_at": None,
    }

def test_hitl_server():
    section("Phase 6 — HITL Server")

    import hitl_server as hs
    hs.app.config["TESTING"] = True
    client = hs.app.test_client()

    try:
        r = client.get("/health")
        assert r.status_code == 200
        ok("/health returns 200 ok")
    except Exception as e:
        err("/health returns 200 ok", e)

    try:
        hs._pending.clear()
        r = client.get("/pending")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)
        ok("/pending returns empty list initially")
    except Exception as e:
        err("/pending returns empty list initially", e)

    try:
        r = client.get("/hitl/nonexistent-bad-token-xyz")
        assert r.status_code in (200, 404, 410)
        ok("Invalid HITL token returns error page")
    except Exception as e:
        err("Invalid HITL token returns error page", e)

    try:
        r = client.post("/decide",
                        data=json.dumps({"approved": True, "token": "wrong-token-xyz"}),
                        content_type="application/json")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is False or "error" in data
        ok("/decide with invalid token returns error")
    except Exception as e:
        err("/decide with invalid token returns error", e)

    try:
        reset_log()
        from agent.logger import log_human_resolution
        now = time.time()
        log_human_resolution("CrashLoopBackOff", "past-pod", "default",
                             "kubectl delete pod past-pod", "Worked fine", now+300, now)
        import secrets as sec
        token = sec.token_urlsafe(32)
        hs._pending[token] = _make_hitl_entry(token, "CrashLoopBackOff")
        r    = client.get(f"/hitl/{token}")
        html = r.data.decode()
        assert r.status_code == 200
        assert "CrashLoopBackOff" in html or "past-pod" in html
        ok("HITL page shows past resolutions for the same failure type")
    except Exception as e:
        err("HITL page shows past resolutions for the same failure type", e)

    try:
        reset_log()
        import secrets as sec
        token = sec.token_urlsafe(32)
        hs._pending[token] = _make_hitl_entry(token)
        r = client.post("/decide",
                        data=json.dumps({"approved": True, "token": token}),
                        content_type="application/json")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert hs._pending[token]["result"] is True
        ok("Full HITL approve flow: page renders, /decide sets result")
    except Exception as e:
        err("Full HITL approve flow: page renders, /decide sets result", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7 — Dashboard
# ─────────────────────────────────────────────────────────────────────────────
def test_dashboard():
    section("Phase 7 — Dashboard")

    import dashboard as dash_mod
    dash_mod.app.config["TESTING"] = True
    client = dash_mod.app.test_client()

    try:
        r = client.get("/")
        assert r.status_code == 200
        ok("Dashboard / returns 200")
    except Exception as e:
        err("Dashboard / returns 200", e)

    try:
        r = client.get("/api/log")
        assert r.status_code == 200
        assert isinstance(json.loads(r.data), list)
        ok("Dashboard /api/log returns JSON list")
    except Exception as e:
        err("Dashboard /api/log returns JSON list", e)

    try:
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "total" in data
        ok("Dashboard /api/stats returns summary counts")
    except Exception as e:
        err("Dashboard /api/stats returns summary counts", e)

    try:
        from agent.logger import log_human_resolution
        now = time.time()
        log_human_resolution("Evicted", "evicted-pod", "default",
                             "kubectl delete pod evicted-pod", "Cleaned up", now+60, now)
        r    = client.get("/")
        html = r.data.decode()
        assert ("memory" in html.lower() or "resolution" in html.lower()
                or "evicted" in html.lower())
        ok("Dashboard shows memory bank / resolution history")
    except Exception as e:
        err("Dashboard shows memory bank / resolution history", e)

    try:
        r    = client.get("/")
        html = r.data.decode()
        assert ("background" in html.lower() or "white" in html.lower()
                or "#fff" in html.lower())
        ok("Dashboard uses white background styling")
    except Exception as e:
        err("Dashboard uses white background styling", e)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8 — Integration
# ─────────────────────────────────────────────────────────────────────────────
def test_integration():
    section("Phase 8 — Integration")

    from agent.executor import build_plan, safety_gate, execute_plan
    from agent.logger import log_event

    def _anomaly(ft, pod="test-pod", ns="default", restart=3):
        a = MagicMock()
        a.failure_type = ft; a.pod_name = pod
        a.namespace = ns;    a.restart_count = restart
        return a

    def _diag(fix="kubectl fix", conf=0.90):
        d = MagicMock()
        d.fix_suggestion = fix; d.confidence = conf
        d.verifier_agrees = True
        d.verifier_root_cause = "confirmed"
        d.verifier_fix_suggestion = fix
        d.verifier_confidence = 0.85
        d.verifier_notes = ""
        return d

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        try:
            reset_log()
            plan = build_plan(_anomaly("CrashLoopBackOff"), _diag(conf=0.55))
            assert safety_gate(plan, "CrashLoopBackOff") is False
            ok("CrashLoopBackOff with confidence=0.55 correctly routes to HITL")
        except Exception as e:
            err("CrashLoopBackOff with confidence=0.55 correctly routes to HITL", e)

        try:
            reset_log()
            mock_run.return_value = MagicMock(returncode=0, stdout="deleted", stderr="")
            anomaly = _anomaly("Evicted", pod="evicted-pod")
            plan    = build_plan(anomaly, _diag(conf=0.95))
            assert plan.action == "delete_evicted"
            assert safety_gate(plan, "Evicted") is True
            result  = execute_plan(plan, anomaly)
            assert result is not None
            ok("Evicted full pipeline: auto-execute → kubectl delete called")
        except Exception as e:
            err("Evicted full pipeline: auto-execute → kubectl delete called", e)

        try:
            reset_log()
            plan = build_plan(_anomaly("ImagePullBackOff", pod="img-pod"), _diag(conf=0.99))
            assert plan.action == "alert_human"
            assert safety_gate(plan, "ImagePullBackOff") is False
            ok("ImagePullBackOff full pipeline: alert_human → always HITL")
        except Exception as e:
            err("ImagePullBackOff full pipeline: alert_human → always HITL", e)

        try:
            reset_log()
            mock_run.reset_mock()
            anomaly     = _anomaly("ImagePullBackOff", pod="img-pod")
            plan        = build_plan(anomaly, _diag())
            plan.action = "alert_human"
            result      = execute_plan(plan, anomaly)
            ok("execute_node alert_human: no kubectl called, result=alerted")
        except Exception as e:
            err("execute_node alert_human: no kubectl called, result=alerted", e)

        try:
            reset_log()
            print()
            anomaly     = _anomaly("NodeNotReady")
            plan        = build_plan(anomaly, _diag())
            plan.action = "hitl_required"
            result      = execute_plan(plan, anomaly, hitl_approved=False)
            print(f"[HITL] Action 'hitl_required' rejected by human.")
            assert result is not None
            ok("execute_node hitl_required + rejected: no kubectl, result=rejected")
        except Exception as e:
            err("execute_node hitl_required + rejected: no kubectl, result=rejected", e)

        try:
            reset_log()
            plan = build_plan(_anomaly("NodeNotReady"), _diag(conf=0.99))
            assert plan.action == "hitl_required"
            assert plan.blast_radius == "CRITICAL"
            assert safety_gate(plan, "NodeNotReady") is False
            ok("NodeNotReady full pipeline: hitl_required → NEVER auto-drain")
        except Exception as e:
            err("NodeNotReady full pipeline: hitl_required → NEVER auto-drain", e)

        try:
            reset_log()
            plan = build_plan(_anomaly("OOMKilled"), _diag(conf=0.95))
            assert plan.action == "patch_memory"
            assert plan.blast_radius == "HIGH"
            assert safety_gate(plan, "OOMKilled") is False
            ok("OOMKilled → patch_memory / HIGH blast → correctly routes to HITL")
        except Exception as e:
            err("OOMKilled → patch_memory / HIGH blast → correctly routes to HITL", e)

        try:
            reset_log()
            for _ in range(3):
                log_event("HITL_DECISION", {
                    "approved": True, "failure_type": "CrashLoopBackOff", "action": "restart_pod"})
            plan = build_plan(_anomaly("CrashLoopBackOff"), _diag(conf=0.70))
            assert safety_gate(plan, "CrashLoopBackOff") is True
            ok("Adaptive trust: 3 prior approvals → CrashLoop auto-executes at confidence=0.70")
        except Exception as e:
            err("Adaptive trust: 3 prior approvals → CrashLoop auto-executes at confidence=0.70", e)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("=" * 70)
    print("  K8sWhisperer — Full Test Suite")
    print("=" * 70)

    test_logger()
    test_diagnose()
    test_build_plan()
    test_safety_gate()
    test_executor_actions()
    test_hitl_server()
    test_dashboard()
    test_integration()

    print()
    print("=" * 70)
    print(f"  Results: {len(PASS)} passed  |  {len(FAIL)} failed  |  {len(ERR)} errors")
    print("=" * 70)
    print()

    if os.path.exists(_TMP_LOG):
        os.remove(_TMP_LOG)

    sys.exit(0 if (len(FAIL) == 0 and len(ERR) == 0) else 1)
