"""Aceptación del despliegue (``/deploy``) — sin Docker vivo.

Autorizado por: **Constitución Ley 5** (aislamiento en VPS), **SPEC-04 §2/§2.1/§3**
(redes, TLS 1.3, ``limit_req``, sin ``ports:`` de DB, JWT 15 min, 2FA de ADMIN) y
**SPEC-06 §3/§4** (copias cifradas verificables, rotación de logs, healthcheck).

Lee los artefactos de ``/deploy`` como datos y ejecuta ``restore.sh`` en modo
verificación contra una copia manipulada (requiere ``bash`` y ``sha256sum``).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
COMPOSE = DEPLOY / "docker-compose.prod.yml"
SCRIPTS = DEPLOY / "scripts"


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


# =====================================================================
# Aislamiento de red (Ley 5 · SPEC-04 §2/§3)
# =====================================================================
def test_only_nginx_publishes_ports(compose: dict[str, Any]) -> None:
    published = {name for name, svc in compose["services"].items() if svc.get("ports")}
    assert published == {"nginx"}


def test_database_services_live_only_on_the_internal_network(
    compose: dict[str, Any],
) -> None:
    assert compose["networks"]["internal_intel_net"].get("internal") is True
    for name in ("neo4j", "redis"):
        assert compose["services"][name]["networks"] == ["internal_intel_net"], name


def test_only_backend_and_worker_are_dual_homed(compose: dict[str, Any]) -> None:
    dual = {
        name
        for name, svc in compose["services"].items()
        if {"public_net", "internal_intel_net"} <= set(svc.get("networks", []))
    }
    assert dual == {"backend", "worker"}


def test_nginx_enforces_tls13_and_login_rate_limit() -> None:
    conf = (DEPLOY / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert re.search(r"ssl_protocols\s+TLSv1\.3;", conf)
    assert "limit_req_zone" in conf and "zone=login_limit" in conf
    login_block = re.search(r"location = /api/v1/auth/login \{(.*?)\}", conf, re.S)
    assert login_block and "limit_req zone=login_limit" in login_block.group(1)
    assert "Strict-Transport-Security" in conf


# =====================================================================
# Plantilla de entorno (SPEC-04 §2 v2.2)
# =====================================================================
def test_env_example_respects_token_policy_and_admin_2fa() -> None:
    env = _env_example()
    assert int(env["ACCESS_TOKEN_EXPIRE_MINUTES"]) <= 15
    assert int(env["REFRESH_TOKEN_EXPIRE_DAYS"]) <= 7
    assert env["ADMIN_TOTP_REQUIRED"] == "true"
    assert env["ENVIRONMENT"] == "production"


def test_env_example_contains_only_placeholders_for_secrets() -> None:
    env = _env_example()
    for key in ("JWT_SECRET_KEY", "NEO4J_PASSWORD", "PREFLIGHT_SECRET", "INFORMANT_API_KEY", "LLM_API_KEY"):
        assert env[key].startswith("CHANGE_ME"), key


# =====================================================================
# Observabilidad (SPEC-06 §4)
# =====================================================================
def test_every_service_rotates_its_logs(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        options = (svc.get("logging") or {}).get("options") or {}
        assert options.get("max-size") and options.get("max-file"), name


def test_backend_declares_healthcheck_on_get_info(compose: dict[str, Any]) -> None:
    check = compose["services"]["backend"]["healthcheck"]
    assert "/api/v1/info" in " ".join(check["test"])


# =====================================================================
# Copias de seguridad (SPEC-06 §3)
# =====================================================================
def test_backup_script_encrypts_and_writes_checksums() -> None:
    script = (SCRIPTS / "backup.sh").read_text(encoding="utf-8")
    assert "umask 077" in script
    assert "--symmetric --cipher-algo AES256" in script
    assert "sha256sum" in script and "SHA256SUMS" in script
    assert "neo4j-admin database backup" in script
    assert "BACKUP_RETENTION_DAYS" in script


def test_backup_script_dumps_the_postgres_ledger_encrypted() -> None:
    """PostgreSQL custodia el ledger v3: su volcado forma parte de la copia."""
    script = (SCRIPTS / "backup.sh").read_text(encoding="utf-8")
    assert "pg_dump --format=custom" in script
    assert "postgres.dump.gpg" in script
    assert "sha256sum neo4j.tar.gz.gpg postgres.dump.gpg storage.tar.gz.gpg" in script


def test_restore_script_restores_the_postgres_ledger() -> None:
    script = (SCRIPTS / "restore.sh").read_text(encoding="utf-8")
    assert "pg_restore --clean --if-exists" in script
    assert "postgres.dump.gpg" in script


def _bash() -> str:
    bash = shutil.which("bash")
    if not bash or not shutil.which("sha256sum"):
        pytest.skip("bash/sha256sum no disponibles en este entorno")
    return bash


#: Sumas de una copia completa v3 (Neo4j + PostgreSQL + informes).
_CHECKSUM_COMMAND = "sha256sum neo4j.tar.gz.gpg postgres.dump.gpg storage.tar.gz.gpg > SHA256SUMS"


def _fake_backup(tmp_path: Path) -> tuple[Path, Path]:
    backup = tmp_path / "20261017T023000Z"
    backup.mkdir()
    (backup / "neo4j.tar.gz.gpg").write_bytes(b"cifrado-neo4j")
    (backup / "postgres.dump.gpg").write_bytes(b"cifrado-postgres")
    (backup / "storage.tar.gz.gpg").write_bytes(b"cifrado-storage")
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("frase-de-prueba", encoding="utf-8")
    return backup, passphrase


def _run_restore(bash: str, backup: Path, passphrase: Path, *args: str) -> Any:
    def posix(path: Path) -> str:
        return path.as_posix()

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("NEO4J_", "BACKUP_"))
    }
    env.update(
        BACKUP_PASSPHRASE_FILE=posix(passphrase),
        ENV_FILE=posix(backup / "no-existe.env"),
    )
    return subprocess.run(
        [bash, posix(SCRIPTS / "restore.sh"), posix(backup), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_restore_verifies_and_stops_without_explicit_confirmation(tmp_path: Path) -> None:
    bash = _bash()
    backup, passphrase = _fake_backup(tmp_path)
    subprocess.run([bash, "-c", _CHECKSUM_COMMAND], cwd=backup, check=True)
    result = _run_restore(bash, backup, passphrase)
    assert result.returncode == 0, result.stderr
    assert "--yes" in result.stdout


def test_restore_refuses_a_tampered_backup(tmp_path: Path) -> None:
    bash = _bash()
    backup, passphrase = _fake_backup(tmp_path)
    subprocess.run([bash, "-c", _CHECKSUM_COMMAND], cwd=backup, check=True)
    (backup / "neo4j.tar.gz.gpg").write_bytes(b"manipulado")
    result = _run_restore(bash, backup, passphrase, "--yes")
    assert result.returncode != 0
    assert "NO se restaura" in result.stderr


def test_restore_refuses_a_pre_v3_backup_without_the_ledger_dump(tmp_path: Path) -> None:
    """Una copia v2 (sin volcado de PostgreSQL) no se restaura a medias."""
    bash = _bash()
    backup, passphrase = _fake_backup(tmp_path)
    (backup / "postgres.dump.gpg").unlink()
    subprocess.run(
        [bash, "-c", "sha256sum neo4j.tar.gz.gpg storage.tar.gz.gpg > SHA256SUMS"],
        cwd=backup,
        check=True,
    )
    result = _run_restore(bash, backup, passphrase, "--yes")
    assert result.returncode != 0
    assert "falta postgres.dump.gpg" in result.stderr


# =====================================================================
# Regresiones detectadas por el CI (sin Docker en local)
# =====================================================================
def test_compose_services_have_valid_environment_blocks(compose: dict[str, Any]) -> None:
    """``environment`` vacío es ``null`` y ``docker compose config`` lo rechaza."""
    for name, svc in compose["services"].items():
        if "environment" in svc:
            assert isinstance(svc["environment"], (dict, list)), name


def test_v3_db_tests_resolve_the_sealed_contract() -> None:
    """La ruta del contrato v3 debe existir desde el propio conftest de Testcontainers."""
    conftest = Path(__file__).resolve().parent / "v3" / "db" / "conftest.py"
    root = conftest.resolve().parents[5]
    assert (root / "contracts" / "v3" / "db-contract.sql").is_file()
