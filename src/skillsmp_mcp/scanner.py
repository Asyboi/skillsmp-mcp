"""Cisco AI Skill Scanner driver.

The scanner is invoked as a subprocess CLI (not imported) for version
stability. Skill files are written to a private temp dir, scanned with the full
engine set, and the temp dir is removed afterward — even on error or timeout
(SR3). No skill code is executed; Cisco's analysis is static/AST.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config


class ScannerError(RuntimeError):
    pass


@dataclass
class Finding:
    rule_id: str
    severity: str
    description: str
    file_path: str
    analyzer: str


@dataclass
class ScanResult:
    available: bool
    status: str  # SAFE | UNSAFE | ERROR | SKIPPED
    max_severity: str | None = None
    findings: list[Finding] = field(default_factory=list)
    analyzers_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def is_blocked(self, block_severities: set[str]) -> bool:
        """True when the max severity falls in the blocking set."""
        if self.status != "UNSAFE" or not self.max_severity:
            return False
        return self.max_severity.strip().upper() in block_severities


def resolve_scanner_command() -> list[str] | None:
    """Resolve the scanner command per the PRD resolution order, or None."""
    override = config.scanner_cmd_override()
    if override:
        return override
    if shutil.which("skill-scanner"):
        return ["skill-scanner"]
    if shutil.which("uvx"):
        return ["uvx", "--from", "cisco-ai-skill-scanner", "skill-scanner"]
    if shutil.which("uv"):
        return ["uv", "x", "--from", "cisco-ai-skill-scanner", "skill-scanner"]
    return None


def build_scan_command(
    base_cmd: list[str],
    scan_dir: str,
    out_path: str,
    *,
    use_llm: bool,
    use_aidefense: bool,
    policy: str | None,
) -> tuple[list[str], list[str]]:
    """Build the scanner argv (full engine set) and any coverage warnings."""
    argv = list(base_cmd) + ["scan", str(scan_dir), "--use-behavioral", "--use-trigger"]
    warnings: list[str] = []
    if use_llm:
        argv += ["--use-llm", "--enable-meta"]
    else:
        warnings.append(
            "LLM semantic analyzer disabled (SKILL_SCANNER_LLM_API_KEY unset); "
            "coverage degraded to static + behavioral. Prose-based prompt "
            "injection may go undetected."
        )
    if use_aidefense:
        argv += ["--use-aidefense"]
    if policy:
        argv += ["--policy", policy]
    argv += ["--format", "json", "--output", str(out_path)]
    return argv, warnings


def parse_scan_report(report: dict[str, Any]) -> ScanResult:
    """Parse the scanner's JSON report into a ScanResult, tolerating aliases."""
    is_safe = bool(report.get("is_safe"))
    findings: list[Finding] = []
    for raw in report.get("findings", []) or []:
        findings.append(
            Finding(
                rule_id=str(raw.get("rule_id") or raw.get("ruleId") or "?"),
                severity=str(raw.get("severity") or "UNKNOWN").upper(),
                description=str(raw.get("description") or raw.get("message") or ""),
                file_path=str(raw.get("file_path") or raw.get("filePath") or ""),
                analyzer=str(raw.get("analyzer") or ""),
            )
        )
    return ScanResult(
        available=True,
        status="SAFE" if is_safe else "UNSAFE",
        max_severity=(str(report["max_severity"]).upper() if report.get("max_severity") else None),
        findings=findings,
        analyzers_used=list(report.get("analyzers_used") or []),
    )


def scan_directory(scan_dir: Path) -> ScanResult:
    """Run the full scanner over ``scan_dir`` and return a ScanResult.

    Never raises: a missing scanner yields SKIPPED; timeouts/bad reports yield
    ERROR. The output report is written to a private temp file that is cleaned
    up here (the caller owns ``scan_dir``).
    """
    base_cmd = resolve_scanner_command()
    if base_cmd is None:
        return ScanResult(
            available=False,
            status="SKIPPED",
            warnings=[
                "Cisco scanner not found. Install 'cisco-ai-skill-scanner' or set "
                "SKILLSMP_SCANNER_CMD. Install is blocked unless forced."
            ],
        )

    out_dir = tempfile.mkdtemp(prefix="skillsmp-scan-out-")
    out_path = Path(out_dir) / "report.json"
    try:
        argv, warnings = build_scan_command(
            base_cmd,
            str(scan_dir),
            str(out_path),
            use_llm=config.has_llm_key(),
            use_aidefense=config.has_aidefense_key(),
            policy=config.scanner_policy(),
        )
        sub_env = _subprocess_env()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=config.scanner_timeout(),
                env=sub_env,
            )
        except subprocess.TimeoutExpired:
            return ScanResult(
                available=True,
                status="ERROR",
                warnings=warnings,
                error=f"Scanner timed out after {config.scanner_timeout()}s.",
            )
        except (OSError, ValueError) as exc:
            return ScanResult(
                available=True, status="ERROR", warnings=warnings,
                error=f"Failed to launch scanner: {exc}",
            )

        if not out_path.exists():
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            return ScanResult(
                available=True, status="ERROR", warnings=warnings,
                error=f"Scanner produced no report (exit {proc.returncode}). {detail}".strip(),
            )
        try:
            report = json.loads(out_path.read_text())
        except (ValueError, OSError) as exc:
            return ScanResult(
                available=True, status="ERROR", warnings=warnings,
                error=f"Could not parse scanner report: {exc}",
            )

        result = parse_scan_report(report)
        result.warnings = warnings + result.warnings
        return result
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def _subprocess_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(config.scanner_subprocess_env())
    return env
