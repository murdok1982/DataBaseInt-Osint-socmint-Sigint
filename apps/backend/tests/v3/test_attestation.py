"""Atestación HMAC de las decisiones (sin Docker: sólo la parte Python)."""

import hashlib
import hmac

import pytest

from app.db.attestation import issue_attestation, load_key

KEY = "clave-de-prueba-atestacion-0123456789abcdef"
PROPOSAL = "0190a7c4-0000-7000-8000-000000000001"


def test_atestacion_coincide_con_el_hmac_que_verifica_la_base():
    token = issue_attestation(
        KEY, proposal_id=PROPOSAL, status="REJECTED", version=3,
        actor="analista-9", clearance="TLP:AMBER", ttl_seconds=60, now=1_000_000,
    )
    exp, actor, clearance, mac = token.split("|")
    assert (exp, actor, clearance) == ("1000060", "analista-9", "TLP:AMBER")
    message = f"{PROPOSAL}|REJECTED|3|1000060|analista-9|TLP:AMBER"
    expected = hmac.new(KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert mac == expected


def test_revisor_con_separador_se_rechaza():
    with pytest.raises(ValueError):
        issue_attestation(KEY, proposal_id=PROPOSAL, status="REJECTED", version=1,
                          actor="ana|lopez", clearance="TLP:CLEAR")


def test_clave_corta_o_ausente_falla_al_cargar():
    with pytest.raises(RuntimeError):
        load_key(None)
    with pytest.raises(RuntimeError):
        load_key("corta")
    assert load_key(f"  {KEY}  ") == KEY
