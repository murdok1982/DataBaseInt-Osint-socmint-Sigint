"""Gateway de ejecución del harness Hermes.

El LLM sólo puede invocar las cinco tools del contrato. El gateway:

1. valida el envelope y los argumentos con Pydantic (``extra="forbid"``);
2. recalcula ``normalized_value`` e ``idempotency_key``;
3. exige ``check_opsec`` antes de interacción HUMINT;
4. delega en un ``HermesBackend`` inyectado (sin SQL en el LLM);
5. audita la llamada con hash de argumentos, nunca con selectores en claro.

Un fallo de validación **no** llega al backend. Un ``ABORT`` de OPSEC devuelve
``status=ABORTED`` y no se degrada a warning.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Protocol, runtime_checkable

from pydantic import ValidationError

from .idempotency import (
    IdempotencyKeyMismatch,
    arguments_sha256,
    expected_key,
    verify_key,
)
from .models import (
    TOOL_MODELS,
    CheckOpsec,
    IngestMultiIntObservable,
    ProposeRelationship,
    RecordSigintTelemetry,
    ToolEnvelope,
    ToolError,
    ToolResult,
)
from .normalization import InvalidSelector, normalize_selector
from .opsec import OpsecContext, OpsecDecision, evaluate_opsec, opsec_event_payload

CONTEXT_KEYS = frozenset({"tool_call_id", "agent_id", "run_id", "mission_id"})
MAX_TOOL_CALLS_PER_RUN = 50


class GatewayError(RuntimeError):
    """Error de transporte/validación del gateway."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@runtime_checkable
class HermesBackend(Protocol):
    """Puerto que implementa la persistencia (``svc_hermes``)."""

    def search_existing_entities(self, payload: Any) -> Any: ...

    def ingest_multi_int_observable(self, payload: Any) -> Any: ...

    def record_sigint_telemetry(self, payload: Any) -> Any: ...

    def propose_relationship(self, payload: Any) -> Any: ...

    #: Opcionales: si no existen, OPSEC es fail-closed.
    def opsec_context(self, payload: CheckOpsec) -> OpsecContext: ...

    def record_opsec_event(self, payload: Mapping[str, Any]) -> None: ...

    def record_tool_call(self, **kwargs: Any) -> None: ...


def _error_result(
    tool_call_id: Any,
    code: str,
    message: str,
    *,
    status: str = "ERROR",
    retryable: bool = False,
    warnings: list[str] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        status=status,  # type: ignore[arg-type]
        warnings=warnings or [],
        error=ToolError(
            code=code, message=message, retryable=retryable, details={}
        ),
    )


class ToolGateway:
    """Ejecuta una tool validada y devuelve siempre ``ToolResult``."""

    def __init__(self, backend: HermesBackend) -> None:
        self.backend = backend
        self._calls_per_run: dict[str, int] = {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def call(self, envelope: ToolEnvelope | Mapping[str, Any]) -> ToolResult:
        try:
            env = (
                envelope
                if isinstance(envelope, ToolEnvelope)
                else ToolEnvelope.model_validate(envelope)
            )
        except ValidationError as exc:
            return _error_result(
                getattr(envelope, "tool_call_id", None)
                if not isinstance(envelope, Mapping)
                else envelope.get("tool_call_id"),
                "SCHEMA_INVALID",
                str(exc),
            )

        run_key = str(env.run_id)
        self._calls_per_run[run_key] = self._calls_per_run.get(run_key, 0) + 1
        if self._calls_per_run[run_key] > MAX_TOOL_CALLS_PER_RUN:
            return _error_result(
                env.tool_call_id,
                "RATE_LIMITED",
                "Máximo de tool calls por run excedido.",
                retryable=True,
            )

        started = time.monotonic()
        result: ToolResult
        try:
            result = self._execute(env)
        except GatewayError as exc:
            result = _error_result(
                env.tool_call_id,
                exc.code,
                exc.message,
                status="ABORTED" if exc.code == "OPSEC_VIOLATION" else "ERROR",
                retryable=exc.retryable,
            )
        except ValidationError as exc:
            result = _error_result(
                env.tool_call_id, "SCHEMA_INVALID", str(exc)
            )
        except IdempotencyKeyMismatch as exc:
            result = _error_result(
                env.tool_call_id, "IDEMPOTENCY_KEY_MISMATCH", str(exc)
            )
        except InvalidSelector as exc:
            result = _error_result(env.tool_call_id, "SCHEMA_INVALID", str(exc))
        except Exception as exc:  # noqa: BLE001 - se traduce a error de contrato
            code = getattr(exc, "code", "INTERNAL")
            result = _error_result(
                env.tool_call_id, str(code), str(exc), retryable=True
            )

        self._audit(
            env,
            result,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )
        return result

    # ------------------------------------------------------------------
    # Validación y despacho
    # ------------------------------------------------------------------
    def _execute(self, env: ToolEnvelope) -> ToolResult:
        if env.tool_name not in TOOL_MODELS:
            raise GatewayError(
                "TOOL_NOT_ALLOWED", f"Tool no permitida: {env.tool_name}"
            )
        if not isinstance(env.arguments, Mapping):
            raise GatewayError("SCHEMA_INVALID", "arguments debe ser un objeto.")

        duplicated = CONTEXT_KEYS & set(env.arguments)
        if duplicated:
            raise GatewayError(
                "SCHEMA_INVALID",
                "arguments no debe repetir campos del envelope: "
                + ", ".join(sorted(duplicated)),
            )

        model_cls = TOOL_MODELS[env.tool_name]
        data = {
            "tool_call_id": env.tool_call_id,
            "agent_id": env.agent_id,
            "run_id": env.run_id,
            "mission_id": env.mission_id,
            **dict(env.arguments),
        }
        payload = model_cls.model_validate(data)

        if isinstance(payload, IngestMultiIntObservable):
            normalized = normalize_selector(payload.observable_type, payload.value)
            if (
                payload.normalized_value is not None
                and payload.normalized_value != normalized
            ):
                raise GatewayError(
                    "SCHEMA_INVALID",
                    "normalized_value no coincide con la normalización del gateway.",
                )
            payload.normalized_value = normalized
            verify_key(payload, payload.idempotency_key)
        elif isinstance(payload, RecordSigintTelemetry):
            normalized = normalize_selector(
                payload.selector.observable_type, payload.selector.value
            )
            payload.selector.value = normalized
            verify_key(payload, payload.idempotency_key)
        elif isinstance(payload, ProposeRelationship):
            verify_key(payload, payload.idempotency_key)
        elif isinstance(payload, CheckOpsec):
            return self._check_opsec(payload)

        capability = getattr(self.backend, env.tool_name, None)
        if capability is None:
            raise GatewayError(
                "TOOL_NOT_ALLOWED",
                f"El backend no implementa la tool {env.tool_name}.",
            )
        output = capability(payload)
        return self._coerce_result(env, output)

    # ------------------------------------------------------------------
    # OPSEC
    # ------------------------------------------------------------------
    def _check_opsec(self, payload: CheckOpsec) -> ToolResult:
        context_method = getattr(self.backend, "opsec_context", None)
        if context_method is None:
            # Fail-closed: sin contexto verificado no se permite interacción.
            decision = OpsecDecision(
                decision="ABORT",
                reasons=("OPSEC_CONTEXT_UNAVAILABLE",),
                risk_level="HIGH",
            )
        else:
            raw = context_method(payload)
            decision = (
                raw
                if isinstance(raw, OpsecDecision)
                else evaluate_opsec(payload.action, raw)
            )
        event = opsec_event_payload(
            sockpuppet_id=str(payload.sockpuppet_id),
            interaction_id=str(payload.interaction_id),
            mission_id=str(payload.mission_id),
            action=payload.action,
            decision=decision,
        )
        recorder = getattr(self.backend, "record_opsec_event", None)
        if recorder is not None:
            recorder(event)
        if not decision.allowed:
            return ToolResult(
                tool_call_id=payload.tool_call_id,
                status="ABORTED",
                data={"decision": "ABORT", "reasons": list(decision.reasons)},
                warnings=list(decision.reasons),
                error=ToolError(
                    code="OPSEC_VIOLATION",
                    message="La política OPSEC abortó la interacción.",
                    retryable=False,
                    details={"reasons": list(decision.reasons)},
                ),
            )
        return ToolResult(
            tool_call_id=payload.tool_call_id,
            status="OK",
            data={"decision": "ALLOW", "risk_level": decision.risk_level},
            warnings=[],
        )

    # ------------------------------------------------------------------
    # Resultado y auditoría
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_result(env: ToolEnvelope, output: Any) -> ToolResult:
        if isinstance(output, ToolResult):
            if output.tool_call_id != env.tool_call_id:
                return ToolResult(
                    tool_call_id=env.tool_call_id,
                    status=output.status,
                    data=output.data,
                    proposal_id=output.proposal_id,
                    evidence_id=output.evidence_id,
                    entity_id=output.entity_id,
                    candidates=output.candidates,
                    warnings=output.warnings,
                    error=output.error,
                )
            return output
        if isinstance(output, Mapping):
            known = {
                "status",
                "data",
                "proposal_id",
                "evidence_id",
                "entity_id",
                "candidates",
                "warnings",
                "error",
            }
            body = {k: v for k, v in output.items() if k in known}
            body.setdefault("status", "OK")
            body.setdefault("data", {})
            return ToolResult(tool_call_id=env.tool_call_id, **body)
        return ToolResult(
            tool_call_id=env.tool_call_id,
            status="OK",
            data={"result": output},
        )

    def _audit(self, env: ToolEnvelope, result: ToolResult, *, latency_ms: float) -> None:
        recorder = getattr(self.backend, "record_tool_call", None)
        if recorder is None:
            return
        try:
            recorder(
                tool_call_id=env.tool_call_id,
                run_id=env.run_id,
                mission_id=env.mission_id,
                agent_id=env.agent_id,
                tool_name=env.tool_name,
                arguments_sha256=arguments_sha256(dict(env.arguments)),
                status=result.status,
                latency_ms=round(latency_ms, 3),
                error_code=(result.error.code if result.error else None),
            )
        except Exception:  # pragma: no cover - la auditoría no rompe la tool
            return


__all__ = [
    "CONTEXT_KEYS",
    "GatewayError",
    "HermesBackend",
    "MAX_TOOL_CALLS_PER_RUN",
    "ToolGateway",
]
