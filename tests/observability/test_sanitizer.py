from src.observability.sanitizer import sanitized_json


def test_hyphenated_credentials_in_nested_headers_are_masked() -> None:
    result = sanitized_json({"headers": {
        "X-API-Key": "fake-api-key-value",
        "access-token": "fake-access-token-value",
        "Set-Cookie": "fake-cookie-value",
        "content-type": "application/json",
    }})
    assert "fake-api-key-value" not in result
    assert "fake-access-token-value" not in result
    assert "fake-cookie-value" not in result
    assert "application/json" in result


def test_credentials_are_masked_in_prompt_text_and_json_strings() -> None:
    value = {
        "message": (
            '问题：请检查。 API_KEY=sk-example-sensitive-123456789 '
            'Authorization: Bearer fake-bearer-123456 '
            'https://example.test/?token=fake-query-123456\n'
            'Cookie: a=fake-cookie-1; session=fake-cookie-2'
        ),
        "tool_result": (
            '{"password":"fake-password-123456",'
            '"api_key":{"nested":"fake-nested-secret"}}'
        ),
        "time_to_first_token_ms": 123,
    }
    result = sanitized_json(value)
    assert "问题：请检查" in result
    assert all(secret not in result for secret in (
        "sk-example-sensitive-123456789", "fake-bearer-123456",
        "fake-query-123456", "fake-password-123456", "fake-nested-secret",
        "fake-cookie-1", "fake-cookie-2",
    ))
    assert "[REDACTED]" in result
    assert '"time_to_first_token_ms":123' in result
