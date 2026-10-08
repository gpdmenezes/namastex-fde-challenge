
"""Tests for best-effort dataset sanitization."""

import pandas as pd
import pytest

from scripts.sanitize_dataset import (
    sanitize_dataframe,
    sanitize_text,
    main,
)


def test_redacts_known_pii_patterns():
    text = (
        "CPF 123.456.789-00, email ana@example.com, "
        "telefone (11) 91234-5678, CEP 01310-100 "
        "e placa ABC1D23."
    )

    sanitized = sanitize_text(text)

    assert "[CPF]" in sanitized
    assert "[EMAIL]" in sanitized
    assert "[TELEFONE]" in sanitized
    assert "[CEP]" in sanitized
    assert "[PLACA]" in sanitized

    for value in (
        "123.456.789-00",
        "ana@example.com",
        "91234-5678",
        "01310-100",
        "ABC1D23",
    ):
        assert value not in sanitized


def test_minimizes_columns_and_preserves_original():
    original = pd.DataFrame({
        "conversation_id": ["original-123", "original-123"],
        "message_index": [0, 1],
        "sender_role": ["lead", "vendedor"],
        "message_type": ["text", "text"],
        "message_body": [
            "Meu CEP é 01310-100.",
            "Meu email é ana@example.com.",
        ],
        "sender_name": ["Nome Privado", "Vendedor Privado"],
        "conversation_outcome": ["ganho", "ganho"],
        "lead_idade_informada": [35, 35],
        "veiculo_texto": ["Carro particular", "Carro particular"],
        "timestamp": [
            "2026-01-01T12:00:00",
            "2026-01-01T12:01:00",
        ],
    })

    snapshot = original.copy(deep=True)
    sanitized = sanitize_dataframe(original)

    pd.testing.assert_frame_equal(original, snapshot)

    assert list(sanitized.columns) == [
        "conversation_id",
        "message_index",
        "sender_role",
        "message_type",
        "message_body",
    ]

    assert sanitized["conversation_id"].tolist() == [
        "conv-000001",
        "conv-000001",
    ]
    assert sanitized["message_index"].tolist() == [0, 1]

    output = "\n".join(sanitized["message_body"])

    assert "01310-100" not in output
    assert "ana@example.com" not in output
    assert "[CEP]" in output
    assert "[EMAIL]" in output


def test_missing_schema_fails_explicitly():
    with pytest.raises(ValueError, match="Colunas"):
        sanitize_dataframe(pd.DataFrame({"message_body": ["teste"]}))


def test_original_file_cannot_be_overwritten(tmp_path, monkeypatch):
    original = tmp_path / "original.parquet"
    original.write_bytes(b"original bytes")

    monkeypatch.setattr(
        "sys.argv",
        [
            "sanitize_dataset",
            "--input", str(original),
            "--output", str(original),
        ],
    )

    with pytest.raises(SystemExit):
        main()

    assert original.read_bytes() == b"original bytes"
