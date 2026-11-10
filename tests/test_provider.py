"""Provider ids and delivery. HTTP is mocked. Nothing is sent."""
import json

import pytest

from src.resend_send import SendError, classify_last_event, find_email, list_emails


def test_sent_is_not_delivered():
    assert classify_last_event("sent") == "submitted"
    assert classify_last_event("delivery_delayed") == "submitted"
    assert classify_last_event("delivered") == "delivered"
    assert classify_last_event("opened") == "delivered"
    assert classify_last_event("clicked") == "delivered"
    assert classify_last_event("bounced") == "not_delivered"
    assert classify_last_event("") == "unknown"


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_complete_empty_list_means_the_issue_was_not_submitted():
    def get(url, headers=None, params=None, timeout=None):
        assert params["limit"] == "100"
        return Response(200, {"object": "list", "has_more": False, "data": []})

    found = find_email(api_key="re_test", subject="AI News, November 9, 2026", get=get)
    assert found is None


def test_truncated_list_is_not_treated_as_an_empty_account():
    def get(url, headers=None, params=None, timeout=None):
        return Response(200, {
            "object": "list",
            "has_more": True,
            "data": [{
                "id": "em_other",
                "subject": "AI News, October 19, 2026",
                "last_event": "delivered",
            }],
        })

    with pytest.raises(SendError, match="truncated"):
        find_email(api_key="re_test", subject="AI News, November 2, 2026", get=get)


def test_list_follows_has_more_and_records_the_matching_id():
    pages = []

    def get(url, headers=None, params=None, timeout=None):
        pages.append(params.get("after"))
        if not params.get("after"):
            return Response(200, {
                "has_more": True,
                "data": [{"id": "em_1", "subject": "Other", "last_event": "delivered"}],
            })
        return Response(200, {
            "has_more": False,
            "data": [{
                "id": "em_nov2",
                "subject": "AI News, November 2, 2026",
                "last_event": "sent",
            }],
        })

    found = find_email(api_key="re_test", subject="AI News, November 2, 2026", get=get)
    assert pages == [None, "em_1"]
    assert found["id"] == "em_nov2"
    assert found["last_event"] == "sent"
    assert found["delivered"] is False
    rows, complete = list_emails(api_key="re_test", get=get)
    assert complete is True
    assert len(rows) == 2


def test_fetch_timeout_retries_once_and_a_401_does_not():
    import requests
    from src.fetch import call_with_one_retry

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.Timeout("timed out")
        return ["ok"]

    assert call_with_one_retry(flaky, "OpenAI") == ["ok"]
    assert calls["n"] == 2

    def denied():
        calls["denied"] = calls.get("denied", 0) + 1
        response = requests.Response()
        response.status_code = 401
        raise requests.HTTPError("401 Client Error: Forbidden", response=response)

    with pytest.raises(requests.HTTPError):
        call_with_one_retry(denied, "Reuters")
    assert calls["denied"] == 1
