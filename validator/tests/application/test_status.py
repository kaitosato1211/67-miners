from __future__ import annotations

from uuid import uuid4

from harnyx_validator.application.status import BatchActivityTracker


def test_batch_activity_tracker_evicts_finished_batch() -> None:
    tracker = BatchActivityTracker()
    batch_id = uuid4()
    artifact_id = uuid4()

    tracker.mark_batch_started(batch_id)
    tracker.mark_artifact_started(batch_id, artifact_id)
    tracker.mark_task_session_started(batch_id)

    active = tracker.snapshot(batch_id)
    assert active.last_activity_stage == "task_session_started"
    assert active.active_artifact_count == 1
    assert active.active_task_session_count == 1

    tracker.mark_batch_finished(batch_id)

    finished = tracker.snapshot(batch_id)
    assert finished.last_activity_at is None
    assert finished.last_activity_stage is None
    assert finished.active_artifact_count == 0
    assert finished.active_task_session_count == 0
