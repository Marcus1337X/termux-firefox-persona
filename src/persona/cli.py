"""CLI for preset generation, isolated Firefox windows and local qualification."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .control import atomic_json
from .environment import doctor
from .manager import PersonaManager


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tbp persona", description=__doc__)
    root.add_argument("--root", help="Private state directory (default: ~/.tbp/personas)")
    root.add_argument("--backend", choices=["software", "native"], default="software")
    commands = root.add_subparsers(dest="action", required=True)
    commands.add_parser("doctor", help="Check local prerequisites")
    commands.add_parser("templates", help="List preset candidates and local qualifications")
    commands.add_parser("list", help="List saved Personas and running windows")
    bootstrap = commands.add_parser("bootstrap", help="Qualify preset variants sequentially on this device")
    bootstrap.add_argument("--template", default="linux-firefox-native-phase0")
    bootstrap.add_argument("--limit", type=int, help="Only qualify the first N variants")
    create = commands.add_parser("create", help="Randomly create and save a Persona")
    create.add_argument("--seed", type=int)
    create.add_argument("--template", help="Select a preset family")
    create.add_argument("--experimental", action="store_true", help="Allow unqualified candidates for local testing")
    create.add_argument("--start", action="store_true", help="Start the new Persona window")
    for action in ("start", "stop", "status", "show", "probe", "qualify"):
        cmd = commands.add_parser(action)
        cmd.add_argument("persona_id")
    command = commands.add_parser("command", help="Operate on one Persona using existing browser actions")
    command.add_argument("persona_id")
    command.add_argument("operation", help="goto, eval, click, type, screenshot, tab_new, tab_list, ...")
    command.add_argument("--params", default="{}", help="JSON parameters for this operation")
    command.add_argument("--timeout", type=float, default=60)
    return root


async def run(args) -> dict | list:
    if args.action == "doctor":
        return doctor()
    manager = PersonaManager(args.root, backend=args.backend)
    if args.action == "bootstrap":
        return await manager.bootstrap(args.template, limit=args.limit,
            progress=lambda message: print(message, file=sys.stderr, flush=True))
    if args.action == "templates":
        snapshot = manager.current_snapshot()
        catalog = manager.catalog()
        results = []
        for template in catalog.templates:
            qualifications = []
            for index in range(len(template.variants)):
                config = template.expand(index, snapshot.environment["firefox_version"])
                report = catalog.report_for(template, config, snapshot)
                eligible = report is not None and report.usable_for(template, config, snapshot)
                qualifications.append({"variant_index": index,
                                       "variant_id": config.get("variant_id"),
                                       "eligible": eligible,
                                       "status": "validated" if eligible else (
                                           "disabled" if template.status == "disabled" else "candidate")})
            eligible_count = sum(item["eligible"] for item in qualifications)
            total = len(template.variants)
            status = "disabled" if template.status == "disabled" else (
                "validated" if total and eligible_count == total else "candidate")
            results.append({"id": template.template_id, "version": template.version,
                            "status": status, "variants": total,
                            "eligible_count": eligible_count, "total_variants": total,
                            "variant_qualifications": qualifications,
                            "required_capabilities": list(template.required_capabilities),
                            "metadata": dict(template.metadata)})
        return results
    if args.action == "create":
        persona = manager.create(seed=args.seed, experimental=args.experimental, template_id=args.template)
        result = persona.to_dict()
        if args.start:
            result["runtime"] = await manager.start(persona.persona_id)
        return result
    if args.action == "list":
        return manager.list()
    if args.action == "start":
        return await manager.start(args.persona_id)
    if args.action == "stop":
        return await manager.stop(args.persona_id)
    if args.action == "status":
        return manager.status(args.persona_id)
    if args.action == "show":
        return manager.store.load(args.persona_id).to_dict()
    if args.action == "command":
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise ValueError("--params must be a JSON object")
        return await manager.command(args.persona_id, args.operation, params, timeout=args.timeout)
    if args.action in {"probe", "qualify"}:
        if args.action == "qualify":
            return (await manager.qualify(args.persona_id)).to_dict()
        report = await manager.command(args.persona_id, "probe", timeout=90)
        directory = manager.paths(args.persona_id)["directory"]
        atomic_json(directory / "last-probe.json", report)
        if args.action == "probe":
            return report
    raise ValueError("Unknown action")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.action in {"qualify", "bootstrap"} and not result.get("passed"):
            raise SystemExit(1)
        if args.action == "doctor" and not result.get("ready"):
            raise SystemExit(1)
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"success": False, "error": str(exc) or "Interrupted"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
