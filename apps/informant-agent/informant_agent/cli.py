"""CLI del Informante IA (``python -m informant_agent`` / ``informant-agent``).

Subcomandos
-----------
``run``     Ciclo completo ``preflight → extraer → validar → ingerir`` sobre un
            directorio de fuentes.
``extract`` Extrae y valida **sin red** (contexto vacío o ``--context`` JSON):
            útil para depurar el contrato.

Variables de entorno: ver :mod:`informant_agent.config`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .api_client import HttpInformantApiClient, InformantApiError, PreflightRequired
from .collectors import LocalJsonCollector, TextDirCollector
from .config import Settings
from .extractor import ContextGuidedExtractor
from .models import PreflightResponse
from .pipeline import InformantPipeline, derive_keywords


def _collector_for(settings: Settings, source_dir: Path, *, text: bool):
    if text:
        return TextDirCollector(source_dir)
    return LocalJsonCollector(source_dir)


def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    source_dir = Path(args.source_dir or (settings.source_dir or ""))
    if not args.source_dir and not settings.source_dir:
        print("ERROR: define --source-dir o INFORMANT_SOURCE_DIR.", file=sys.stderr)
        return 2
    api_key = args.api_key or settings.api_key
    if not api_key:
        print("ERROR: define --api-key o INFORMANT_API_KEY.", file=sys.stderr)
        return 2

    collector = _collector_for(settings, source_dir, text=args.text)
    documents = list(collector.collect())
    if not documents:
        print(f"Sin fuentes en {source_dir}.", file=sys.stderr)
        return 1

    results = []
    with HttpInformantApiClient(
        base_url=args.base_url or settings.base_url,
        api_key=api_key,
        timeout=settings.timeout,
    ) as api:
        pipeline = InformantPipeline(api)
        for document in documents:
            try:
                result = pipeline.run(document)
            except (PreflightRequired, InformantApiError) as exc:
                print(f"[FALLO] {document.title}: {exc}", file=sys.stderr)
                return 1
            job = result.job
            results.append(
                {
                    "title": document.title,
                    "job_id": getattr(job, "job_id", None),
                    "status": getattr(job, "status", None),
                    "triplets": result.triplet_count,
                    "links": result.link_categories,
                }
            )
            print(
                f"[OK] {document.title} → job={getattr(job, 'job_id', None)} "
                f"({result.triplet_count} tripletas, {len(result.link_categories)} vínculos)"
            )

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    source_dir = Path(args.source_dir or (settings.source_dir or ""))
    if not args.source_dir and not settings.source_dir:
        print("ERROR: define --source-dir o INFORMANT_SOURCE_DIR.", file=sys.stderr)
        return 2

    collector = _collector_for(settings, source_dir, text=args.text)
    if args.context:
        context = PreflightResponse.model_validate(json.loads(Path(args.context).read_text("utf-8")))
    else:
        context = PreflightResponse(preflight_token="offline")

    extractor = ContextGuidedExtractor()
    for document in collector.collect():
        payload = extractor.build(document, context)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="informant-agent",
        description="Informante IA (AI_INFORMANT) — recolección OSINT M2M de OSINT Nexus.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Ciclo completo (con pre-vuelo y envío M2M).")
    run.add_argument("--source-dir", help="Directorio de fuentes (*.json o *.txt).")
    run.add_argument("--text", action="store_true", help="Trata el directorio como *.txt.")
    run.add_argument("--base-url", help="Base de la API (INFORMANT_API_BASE_URL).")
    run.add_argument("--api-key", help="API Key M2M (INFORMANT_API_KEY).")
    run.set_defaults(func=_cmd_run)

    extract = sub.add_parser("extract", help="Extrae y valida sin red.")
    extract.add_argument("--source-dir", help="Directorio de fuentes.")
    extract.add_argument("--text", action="store_true", help="Trata el directorio como *.txt.")
    extract.add_argument("--context", help="JSON de PreflightResponse para guiar la extracción.")
    extract.set_defaults(func=_cmd_extract)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
