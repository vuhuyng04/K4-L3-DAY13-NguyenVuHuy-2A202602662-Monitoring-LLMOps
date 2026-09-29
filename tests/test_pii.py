from app.pii import scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd() -> None:
    out = scrub_text("CCCD cua toi la 012345678901.")
    assert "012345678901" not in out
    assert "REDACTED_CCCD" in out


def test_scrub_credit_card_formats_without_partial_leak() -> None:
    for card in ("4111111111111111", "4111 1111 1111 1111", "4111-1111-1111-1111"):
        out = scrub_text(f"Card: {card}")
        assert out == "Card: [REDACTED_CREDIT_CARD]"


def test_scrub_passport() -> None:
    out = scrub_text("Passport C1234567 expires soon")
    assert "C1234567" not in out
    assert "REDACTED_PASSPORT_VN" in out


def test_scrub_keeps_non_pii_text() -> None:
    text = "Refund within 7 days for order 12345"
    assert scrub_text(text) == text


def test_scrub_value_is_recursive() -> None:
    from app.pii import scrub_value

    out = scrub_value(
        {"detail": "mail a@b.com", "nested": [{"phone": "0901234567"}], "latency_ms": 12}
    )
    assert out == {
        "detail": "mail [REDACTED_EMAIL]",
        "nested": [{"phone": "[REDACTED_PHONE_VN]"}],
        "latency_ms": 12,
    }


def test_scrub_event_runs_before_log_is_written(monkeypatch, tmp_path) -> None:
    import json

    import structlog

    from app import logging_config

    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    logging_config.configure_logging()
    structlog.get_logger().info(
        "request_failed",
        service="api",
        payload={"detail": "user 012345678901 card 4111 1111 1111 1111 mail x@y.vn"},
    )
    raw = log_path.read_text(encoding="utf-8")
    assert "012345678901" not in raw
    assert "4111 1111" not in raw
    assert "x@y.vn" not in raw
    assert json.loads(raw.splitlines()[-1])["payload"]["detail"].count("REDACTED") == 3
