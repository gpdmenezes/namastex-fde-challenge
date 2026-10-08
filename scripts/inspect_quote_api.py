
import argparse
import json
import time
from collections import Counter
from datetime import date, timedelta

import httpx


BASE_URL = "http://localhost:8000"


def classify(status, body):
    if 200 <= status < 300:
        return "success"
    if status == 422:
        if body.get("error") == "cotacao_recusada":
            return "commercial_decline"
        if isinstance(body.get("detail"), list):
            return "schema_validation"
        return "unknown_422"
    if status == 400:
        return "bad_request"
    if status in (500, 502, 503):
        return "transient_error"
    return "other_http_error"


def probe(client, method, path, payload=None):
    start = time.perf_counter()

    try:
        kwargs = {"json": payload} if payload is not None else {}
        response = client.request(method, path, **kwargs)
        elapsed = round((time.perf_counter() - start) * 1000)

        try:
            raw = response.json()
        except ValueError:
            raw = None

        body = raw if isinstance(raw, dict) else {}
        kind = classify(response.status_code, body)

        if raw is None or not isinstance(raw, dict):
            kind = "unexpected_response"

        return {
            "status": response.status_code,
            "ms": elapsed,
            "kind": kind,
            "body": body,
        }

    except httpx.TimeoutException:
        return {
            "status": None,
            "ms": round((time.perf_counter() - start) * 1000),
            "kind": "timeout",
            "body": {},
        }
    except httpx.RequestError as exc:
        return {
            "status": None,
            "ms": round((time.perf_counter() - start) * 1000),
            "kind": type(exc).__name__,
            "body": {},
        }


def print_result(label, result, endpoint="/quote"):
    body = result["body"]
    info = {
        "http": result["status"],
        "ms": result["ms"],
        "kind": result["kind"],
    }

    if result["kind"] == "success":
        info["response_fields"] = sorted(body.keys())

        if endpoint == "/health":
            info["health_status"] = body.get("status")

        elif endpoint == "/planos":
            plans = body.get("planos", [])
            info["plan_ids"] = [
                p.get("id") for p in plans if isinstance(p, dict)
            ]
            info["plan_fields"] = (
                sorted(plans[0].keys()) if plans else []
            )
            info["rules"] = sorted(
                body.get("regras", {}).keys()
            )

        else:
            for key in (
                "plano_id",
                "premio_mensal",
                "franquia",
                "moeda",
                "multiplicadores",
            ):
                if key in body:
                    info[key] = body[key]

            carencia = body.get("carencia", {})
            info["carencia_dias"] = carencia.get("dias")
            info["carencia_coberturas"] = carencia.get("coberturas")

            pro_rata = body.get("primeiro_pagamento_pro_rata")
            if pro_rata:
                info["primeiro_pagamento"] = pro_rata

    else:
        if "error" in body:
            info["error"] = body["error"]

        if "motivo" in body:
            info["motivo"] = body["motivo"]

        if "detalhe" in body:
            info["detalhe"] = str(body["detalhe"])[:120]

        if isinstance(body.get("detail"), list):
            info["validation_errors"] = [
                {
                    "loc": ".".join(map(str, e.get("loc", []))),
                    "type": e.get("type"),
                }
                for e in body["detail"]
            ]

    print(f"{label}: {json.dumps(info, ensure_ascii=False)}")


def contract_tests(client):
    print("\n=== CONTRATOS ===")

    for path in ("/health", "/planos"):
        r = probe(client, "GET", path)
        print_result(path, r, path)

    today = date.today()
    year = today.year
    next_month = (today.replace(day=28) + timedelta(days=4))
    next_month = next_month.replace(day=1)

    base = {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": year - 4,
        "cep": "01310-100",
        "data_inicio": next_month.replace(day=15).isoformat(),
    }

    def changed(**updates):
        return {**base, **updates}

    def without(*keys):
        return {k: v for k, v in base.items() if k not in keys}

    cases = [
        ("baseline", base),

        # Limites de idade
        ("age_17", changed(idade=17)),
        ("age_24", changed(idade=24)),
        ("age_75", changed(idade=75)),
        ("age_76", changed(idade=76)),

        # Idade do veiculo
        ("vehicle_6", changed(veiculo_ano=year - 6)),
        ("vehicle_20", changed(veiculo_ano=year - 20)),
        ("vehicle_21", changed(veiculo_ano=year - 21)),

        # CEP
        ("cep_high_risk", changed(cep="07000-000")),
        ("cep_missing", without("cep")),
        ("cep_invalid_format", changed(cep="INVALIDO")),

        # Vigencia
        ("start_first_day", changed(
            data_inicio=next_month.isoformat()
        )),
        ("start_missing", without("data_inicio")),

        # Plano
        ("premium", changed(plano_id="premium")),
        ("unknown_plan", changed(plano_id="inexistente")),

        # Validacao
        ("missing_age", without("idade")),
        ("invalid_age", changed(idade=-1)),
        ("invalid_date", changed(data_inicio="not-a-date")),
    ]

    print("\n=== MATRIZ DE COTACAO ===")
    results = {}

    for label, payload in cases:
        result = probe(client, "POST", "/quote", payload)
        results[label] = result
        print_result(label, result)

    print("\n=== COMPARACOES COM O BASELINE ===")

    baseline = results["baseline"]
    base_body = baseline["body"]

    if baseline["kind"] != "success":
        print("Baseline falhou; comparacoes indisponiveis.")
        return

    base_price = base_body.get("premio_mensal")

    for label, result in results.items():
        if label == "baseline":
            continue

        if result["kind"] == "success":
            price = result["body"].get("premio_mensal")
            if isinstance(price, (int, float)) and isinstance(
                base_price, (int, float)
            ):
                delta = round(price - base_price, 2)
                print(f"{label}: delta_premio={delta:+.2f}")
            else:
                print(f"{label}: premio ausente")
        else:
            print(f"{label}: {result['kind']}")

    print("\n=== RESUMO CONTRATOS ===")
    print(dict(Counter(r["kind"] for r in results.values())))


def resilience_tests(client, trials):
    print("\n=== RESILIENCIA / RETRY ===")
    print(f"Operacoes: {trials}; maximo de 3 tentativas cada")

    payload = {
        "plano_id": "completo",
        "idade": 35,
        "veiculo_ano": date.today().year - 4,
        "cep": "01310-100",
    }

    retryable = {"transient_error", "timeout"}
    all_attempts = []
    outcomes = Counter()

    for operation in range(1, trials + 1):
        start = time.perf_counter()

        for attempt in range(1, 4):
            result = probe(client, "POST", "/quote", payload)
            all_attempts.append(result)

            print(
                f"operation={operation} attempt={attempt} "
                f"http={result['status']} "
                f"ms={result['ms']} "
                f"kind={result['kind']}"
            )

            if result["kind"] not in retryable:
                break

            if attempt < 3:
                time.sleep(0.25 * (2 ** (attempt - 1)))

        outcomes[result["kind"]] += 1

        total_ms = round(
            (time.perf_counter() - start) * 1000
        )

        print(
            f"  final={result['kind']} "
            f"attempts={attempt} total_ms={total_ms}"
        )

    print("\n=== RESUMO RESILIENCIA ===")
    print("Total HTTP attempts:", len(all_attempts))
    print("Tipos observados:", dict(Counter(
        r["kind"] for r in all_attempts
    )))
    print("Desfecho das operacoes:", dict(outcomes))

    latencies = sorted(r["ms"] for r in all_attempts)
    if latencies:
        print(
            f"Latencia por tentativa: "
            f"min={latencies[0]}ms "
            f"max={latencies[-1]}ms"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["contract", "resilience"],
        required=True,
    )
    parser.add_argument(
        "--trials", type=int, default=8
    )
    args = parser.parse_args()

    if not 1 <= args.trials <= 20:
        parser.error("--trials deve estar entre 1 e 20")

    timeout = httpx.Timeout(
        connect=1.0,
        read=2.5,
        write=2.0,
        pool=2.0,
    )

    with httpx.Client(
        base_url=BASE_URL,
        timeout=timeout,
        trust_env=False,
    ) as client:
        if args.mode == "contract":
            contract_tests(client)
        else:
            resilience_tests(client, args.trials)


if __name__ == "__main__":
    main()
