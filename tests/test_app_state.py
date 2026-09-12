"""Tests for generation result guards that do not require a Tk window."""

from ui.app import EditorApp


def make_app_shell():
    app = EditorApp.__new__(EditorApp)
    app.active_request_id = 7
    app.revision_id = 4
    app.generating = True
    app.current_session = None
    app.status_messages = []
    app.finished = False
    app.set_status = app.status_messages.append

    def finish():
        app.finished = True
        app.generating = False

    app._finish_generation = finish
    return app


def test_stale_revision_result_is_discarded_before_session_creation():
    app = make_app_shell()
    event = (
        "generation_result",
        7,
        3,
        "Original text.",
        "Proposed text.",
        "Fix grammar.",
        "model",
    )

    app._handle_generation_result(event)

    assert app.finished is True
    assert app.current_session is None
    assert "stale result from model was discarded" in app.status_messages[-1]


def test_result_from_superseded_request_is_ignored():
    app = make_app_shell()
    event = (
        "generation_result",
        6,
        4,
        "Original text.",
        "Proposed text.",
        "Fix grammar.",
        "model",
    )

    app._handle_generation_result(event)

    assert app.finished is False
    assert app.current_session is None


def test_cancelled_active_request_restores_idle_state():
    app = make_app_shell()

    app._handle_generation_cancelled(7)

    assert app.finished is True
    assert app.generating is False
    assert app.status_messages[-1] == (
        "Generation cancelled. Your text was not changed."
    )
