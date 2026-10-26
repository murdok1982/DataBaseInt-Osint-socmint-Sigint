#!/usr/bin/env python3
"""
audit_sdd.py — Auditoría de conformidad SDD v2.0 (Fase 4).

Recorre la cadena spec -> contrato -> código -> test y comprueba, de forma
INDEPENDIENTE de los tests, que:

  1. Los 3 contratos coinciden con su hash sellado en SEAL.md.
  2. Todo `operationId` del contrato (openapi.yaml) está implementado en la app.
  3. La app NO expone operaciones fuera del contrato (sin huérfanos).
  4. Los labels y relaciones del contrato de grafo están en SPEC-01.
  5. Los enums de `extraction-schema.json` están en SPEC-01.
  6. Toda operación no-pública tiene fila en la matriz RBAC.
  7. Cada spec tiene "Criterios de Aceptación".

Uso:  python scripts/audit_sdd.py
Sale con código 1 si algún check falla.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SPECS = ROOT / "specs"
BACKEND = ROOT / "apps" / "backend"

CHECKS: list[tuple[str, str, bool, str]] = []


def add(cid: str, desc: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((cid, desc, ok, detail))


# --- 1. Hashes sellados ------------------------------------------------------
def check_hashes() -> None:
    try:
        seal = (CONTRACTS / "SEAL.md").read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        add("HASH", "SEAL.md legible", False, str(exc))
        return
    for name in (
        "graph-constraints.cypher",
        "extraction-schema.json",
        "openapi.yaml",
        "v3/hermes-tools.json",
        "v3/db-contract-v3.1.sql",
    ):
        path = CONTRACTS / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        add(f"HASH:{name}", f"{name} == SEAL.md", digest in seal, digest)

    # El contrato v3.1 y la migración deben ser byte a byte iguales; si no, el
    # sello certifica un DDL distinto del que se aplica.
    migration = ROOT / "migrations" / "v3.1" / "0001_schema.sql"
    contract = CONTRACTS / "v3" / "db-contract-v3.1.sql"
    same = migration.exists() and contract.exists() and migration.read_bytes() == contract.read_bytes()
    add("HASH:v3.1-sync", "migración v3.1 == db-contract-v3.1.sql", same)


# --- 2/3. Cobertura de operaciones ------------------------------------------
def _app_operation_ids() -> tuple[set[str], set[str]]:
    sys.path.insert(0, str(BACKEND))
    from app.main import app  # noqa: WPS433 (import tardío deliberado)

    http_ops: set[str] = set()
    for item in app.openapi().get("paths", {}).values():
        for op in item.values():
            if isinstance(op, dict) and op.get("operationId"):
                http_ops.add(op["operationId"])

    ws_ops: set[str] = set()
    for route in app.routes:
        if route.__class__.__name__ == "APIWebSocketRoute":
            if getattr(route, "name", None):
                ws_ops.add(route.name)
    return http_ops, ws_ops


def _norm(op: str) -> str:
    """Normaliza operationId para comparar camelCase <-> snake_case."""
    return re.sub(r"[^a-z0-9]", "", op.lower())


def check_operations() -> set[str]:
    oapi = (CONTRACTS / "openapi.yaml").read_text(encoding="utf-8")
    contract_ops = set(re.findall(r"operationId:\s*([A-Za-z0-9_]+)", oapi))
    http_ops, ws_ops = _app_operation_ids()
    implemented = http_ops | ws_ops

    contract_norm = {_norm(o): o for o in contract_ops}
    impl_norm = {_norm(o): o for o in implemented}
    missing = sorted(contract_norm[k] for k in set(contract_norm) - set(impl_norm))
    extra = sorted(impl_norm[k] for k in set(impl_norm) - set(contract_norm))

    add(
        "OPS:contract->code",
        f"{len(contract_ops)} operationId del contrato implementados",
        not missing,
        f"faltan={missing}",
    )
    add(
        "OPS:no-orphans",
        "la app no expone operaciones fuera del contrato",
        not extra,
        f"huérfanas={extra}",
    )
    return contract_ops


# --- 4. Grafo ----------------------------------------------------------------
RELATIONS = [
    "CONTROLS", "FINANCED", "USES_ASSET", "LOCATED_AT", "ASSOCIATED_WITH",
    "PARTICIPATED_IN", "MENTIONED_IN", "INVESTIGATES", "HAS_REQUIREMENT",
    "CITES_ANALYSIS", "CORROBORATES", "CONTRADICTS", "EXTENDS",
    "ANSWERS_REQUIREMENT", "RAISED_FOR", "ABOUT",
]


def check_graph() -> None:
    cypher = (CONTRACTS / "graph-constraints.cypher").read_text(encoding="utf-8")
    spec01 = (SPECS / "01-graph-ontology.spec.md").read_text(encoding="utf-8")

    labels = sorted(set(re.findall(r"FOR\s*\(\w+:(\w+)\)", cypher)))
    missing_lab = [lab for lab in labels if lab not in spec01]
    add("GRAPH:labels", f"{len(labels)} labels en contrato y SPEC-01", not missing_lab, f"faltan={missing_lab}")

    missing_rel = [rel for rel in RELATIONS if rel not in cypher or rel not in spec01]
    add("GRAPH:relations", f"{len(RELATIONS)} relaciones en contrato y SPEC-01", not missing_rel, f"faltan={missing_rel}")


# --- 5. Extraction schema ----------------------------------------------------
def check_extraction() -> None:
    schema = json.loads((CONTRACTS / "extraction-schema.json").read_text(encoding="utf-8"))
    spec01 = (SPECS / "01-graph-ontology.spec.md").read_text(encoding="utf-8")
    labels = schema["$defs"]["entityLabel"]["enum"]
    preds = schema["$defs"]["predicateType"]["enum"]
    links = schema["$defs"]["linkCategory"]["enum"]
    add("EXTRACT:labels", "labels de extracción ⊆ SPEC-01", all(x in spec01 for x in labels), str(labels))
    add("EXTRACT:relations", "predicados de extracción ⊆ SPEC-01", all(x in spec01 for x in preds), str(preds))
    add("EXTRACT:links", "categorías de vínculo presentes", len(links) == 4, str(links))


# --- 6. RBAC -----------------------------------------------------------------
def check_rbac(contract_ops: set[str]) -> None:
    sys.path.insert(0, str(BACKEND))
    from app.core import rbac  # noqa: WPS433

    matrix: set[str] = set()
    for attr in ("ALLOWED_ROLES_BY_OPERATION", "MIN_ROLE_BY_OPERATION"):
        value = getattr(rbac, attr, None)
        if isinstance(value, dict):
            matrix |= set(value)

    public = {"authLogin", "authRefresh", "authLogout", "authMe", "getInfo"}
    ws = {"ingestProgressWs"}
    need = contract_ops - public - ws
    uncovered = need - matrix
    add("RBAC:coverage", f"{len(need)} operaciones protegidas tienen fila RBAC", not uncovered, f"sin cubrir={sorted(uncovered)}")


# --- 7. Specs ----------------------------------------------------------------
def check_specs() -> None:
    missing = []
    for spec in sorted(SPECS.glob("*.spec.md")):
        text = spec.read_text(encoding="utf-8")
        if "criterios de aceptación" not in text.lower():
            missing.append(spec.name)
    add("SPECS:acceptance", "cada spec tiene 'Criterios de Aceptación'", not missing, f"sin criterios={missing}")


def main() -> int:
    try:  # consola Windows: evitar UnicodeEncodeError (cp1252)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

    check_hashes()
    contract_ops = check_operations()
    check_graph()
    check_extraction()
    check_rbac(contract_ops)
    check_specs()

    print("=" * 78)
    print("AUDITORÍA DE CONFORMIDAD SDD v2.0 — OSINT Nexus")
    print("=" * 78)
    failed = 0
    for cid, desc, ok, detail in CHECKS:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {cid:<22} {desc}")
        if not ok and detail:
            print(f"        -> {detail}")
        if not ok:
            failed += 1
    print("-" * 78)
    total = len(CHECKS)
    print(f"RESULTADO: {total - failed}/{total} checks OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
