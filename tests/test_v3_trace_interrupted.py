"""An episode that logged goal_interrupted ends as INTERRUPTED (<reason>), not FAIL (v3.0h)."""

from schema_voyager.trace import EpisodeLog


def end_line(log, success):
    return log._format("episode_end", {
        "success": success, "actions": 7, "llm_tokens": 0, "blocks_walked": 12, "repeated_failures": 0,
    })


def test_interrupted_episode_end_names_the_reason():
    for reason in ("bot", "cancelled"):
        log = EpisodeLog(None, "L")
        log.event("goal_interrupted", goal_id="u001", reason=reason)

        line = end_line(log, success=False)

        assert line == f"[L] INTERRUPTED ({reason}) actions=7 llm_tokens=0 walked=12 repeated_failures=0"
        assert "FAIL" not in line


def test_failure_without_interruption_stays_fail():
    assert " FAIL actions=" in end_line(EpisodeLog(None, "L"), success=False)


def test_success_is_unchanged():
    assert " SUCCESS actions=" in end_line(EpisodeLog(None, "L"), success=True)


def test_episode_logs_do_not_share_the_interruption():
    interrupted, other = EpisodeLog(None, "L"), EpisodeLog(None, "L")
    interrupted.event("goal_interrupted", goal_id="u001", reason="bot")

    assert " FAIL actions=" in end_line(other, success=False)
