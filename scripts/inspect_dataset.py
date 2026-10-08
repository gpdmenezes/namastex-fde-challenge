
from pathlib import Path
import pandas as pd

PATH = Path("dataset/conversations.parquet")
df = pd.read_parquet(PATH)

required = {
    "conversation_id", "message_index", "timestamp",
    "sender_role", "message_type", "message_body",
    "channel", "conversation_outcome",
    "lead_idade_informada", "veiculo_texto",
}

missing = required - set(df.columns)
if missing:
    raise ValueError(f"Colunas ausentes: {sorted(missing)}")

def section(title):
    print(f"\n{'=' * 12} {title} {'=' * 12}")

# 1. Schema e dimensoes
section("DIMENSOES E SCHEMA")
print(f"Mensagens: {len(df)}")
print(f"Conversas: {df['conversation_id'].nunique()}")
print(f"Colunas: {len(df.columns)}")
print(df.dtypes.to_string())

section("NULOS (%)")
print((df.isna().mean() * 100).round(2).to_string())

# 2. Distribuicoes
section("DISTRIBUICOES")
for col in ["sender_role", "message_type", "channel"]:
    print(f"\n{col}:")
    print(df[col].value_counts(dropna=False).to_string())

conversations = df.drop_duplicates("conversation_id")
print("\nconversation_outcome (por conversa):")
print(conversations["conversation_outcome"].value_counts(
    dropna=False
).to_string())

lengths = df.groupby("conversation_id").size()
print("\nMensagens por conversa:")
print(lengths.describe().round(2).to_string())
print("\nDistribuicao dos comprimentos:")
print(lengths.value_counts().sort_index().to_string())

# 3. Midias
section("MIDIAS")
media = df[df["message_type"].isin(["image", "audio", "document"])]
markers = media["message_body"].fillna("").str.match(
    r"^\[(documento|imagem|audio|áudio)\]",
    case=False,
)
print(f"Mensagens de midia: {len(media)}")
print(f"Com marcador reconhecido: {int(markers.sum())}")
print(f"Sem marcador reconhecido: {int((~markers).sum())}")

# 4. Integridade e ordenacao
section("INTEGRIDADE E ORDEM")
ordered = df.sort_values(
    ["conversation_id", "message_index"], kind="stable"
).copy()

duplicates = ordered.duplicated(
    ["conversation_id", "message_index"]
).sum()

index_stats = ordered.groupby("conversation_id")[
    "message_index"
].agg(["min", "max", "nunique"])

not_zero = (index_stats["min"] != 0).sum()
gaps = (
    index_stats["max"] - index_stats["min"] + 1
    != index_stats["nunique"]
).sum()

ordered["_ts"] = pd.to_datetime(
    ordered["timestamp"], errors="coerce", utc=True
)
deltas = ordered.groupby("conversation_id")["_ts"].diff()
backwards = deltas < pd.Timedelta(0)

print(f"Indices duplicados: {duplicates}")
print(f"Conversas sem inicio no indice 0: {not_zero}")
print(f"Conversas com lacunas de indice: {gaps}")
print(f"Timestamps invalidos/nulos: {ordered['_ts'].isna().sum()}")
print(f"Inversoes temporais: {backwards.sum()}")
print(
    "Conversas com inversao temporal:",
    ordered.loc[backwards, "conversation_id"].nunique()
)

# 5. PII - somente contagens, nunca valores
section("PII - DETECCAO HEURISTICA")
patterns = {
    "CPF": r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)",
    "Email": r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    "Telefone": r"(?<!\d)(?:\+?55[\s-]?)?(?:\(?\d{2}\)?[\s-]?)?9\d{4}-?\d{4}(?!\d)",
    "Placa": r"(?i)\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b",
    "CEP": r"(?<!\d)\d{5}-?\d{3}(?!\d)",
}

body = df["message_body"].fillna("").astype(str)
for name, pattern in patterns.items():
    matches = body.str.contains(pattern, regex=True, na=False)
    print(f"{name}: {int(matches.sum())} mensagens")

print(
    "sender_name preenchido:",
    int(df["sender_name"].notna().sum())
    if "sender_name" in df.columns else "coluna ausente"
)

# 6. Metadata leakage e consistencia
section("METADADOS E POSSIVEL LEAKAGE")
first = ordered.drop_duplicates("conversation_id")

metadata = [
    "conversation_outcome",
    "lead_idade_informada",
    "veiculo_texto",
]

for col in metadata:
    first_present = first[col].notna().sum()
    inconsistent = (
        df.groupby("conversation_id")[col]
        .nunique(dropna=False)
        .gt(1)
        .sum()
    )
    print(f"\n{col}:")
    print(f"  Presente na primeira mensagem: {first_present}")
    print(f"  Conversas com valores inconsistentes: {inconsistent}")

section("FIM")
print("Nenhum conteudo de mensagem ou valor de PII foi impresso.")
