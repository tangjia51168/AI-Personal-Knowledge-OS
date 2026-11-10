from worker.celery_app import celery_app, smoke


def test_smoke_task_body():
    # Unit execution only, NOT evidence of Redis transport / remote Worker execution.
    assert smoke.run() == {"status": "ok", "worker": "knowledge-os", "schema_version": 1}


def test_queue_accepts_only_json():
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_expires == 3600
