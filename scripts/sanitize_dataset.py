
"""Best-effort regex redaction of historical conversation data."""

import argparse
import re
from pathlib import Path

import pandas as pd


PATTERNS = (
    (
        re.compile(
            r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
        ),
        "[EMAIL]",
    ),
    (
        re.compile(
            r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)"
        ),
        "[CPF]",
    ),
    (
        re.compile(
            r"(?<!\d)(?:\+?55[\s-]?)?"
            r"(?:\(?\d{2}\)?[\s-]?)?"
            r"9\d{4}[\s-]?\d{4}(?!\d)"
        ),
        "[TELEFONE]",
    ),
    (
        re.compile(r"(?<!\d)\d{5}-?\d{3}(?!\d)"),
        "[CEP]",
    ),
    (
        re.compile(
            r"(?i)\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b"
        ),
        "[PLACA]",
    ),
)


def sanitize_text(text: str) -> str:
    result = str(text)

    for pattern, replacement in PATTERNS:
        result = pattern.sub(replacement, result)

    return result


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Minimize columns and replace original conversation identifiers."""

    required = {
        "conversation_id",
        "message_index",
        "sender_role",
        "message_type",
        "message_body",
    }

    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Colunas necessárias ausentes: {sorted(missing)}"
        )

    identifiers = df["conversation_id"].drop_duplicates().tolist()

    id_mapping = {
        identifier: f"conv-{index:06d}"
        for index, identifier in enumerate(identifiers, start=1)
    }

    sanitized = pd.DataFrame({
        "conversation_id": df["conversation_id"].map(id_mapping),
        "message_index": df["message_index"],
        "sender_role": df["sender_role"],
        "message_type": df["message_type"],
        "message_body": (
            df["message_body"]
            .fillna("")
            .map(sanitize_text)
        ),
    })

    return sanitized.sort_values(
        ["conversation_id", "message_index"],
        kind="stable",
    ).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("dataset/conversations.parquet"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_data/conversations_sanitized.parquet"),
    )
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        parser.error("O arquivo de saída não pode ser o original.")

    df = pd.read_parquet(args.input)
    sanitized = sanitize_dataframe(df)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sanitized.to_parquet(args.output, index=False)

    print(f"Mensagens processadas: {len(sanitized)}")
    print(
        "Conversas processadas:",
        sanitized["conversation_id"].nunique(),
    )
    print(f"Saída: {args.output}")
    print(
        "ATENÇÃO: sanitização heurística, NÃO anonimização garantida. "
        "Não publicar sem revisão adicional."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
