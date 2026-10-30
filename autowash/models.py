"""Résultats d'analyse (constats) et actions de correction."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


class Severity(IntEnum):
    OK = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3

    @property
    def label(self) -> str:
        return {0: "OK", 1: "Info", 2: "Attention", 3: "Critique"}[self.value]


@dataclass
class Action:
    label: str
    run: Callable[[], str]  # renvoie un message de résultat, lève une exception en cas d'échec
    admin: bool = False
    auto: bool = True  # peut être appliquée par « Tout corriger »
    confirm: str | None = None
    batch: bool = True  # peut être cochée pour une action groupée (faux pour les simples raccourcis « Ouvrir… »)
    undo: dict | None = None  # comment annuler l'action (voir journal.register_undo), None si impossible

    @property
    def significant(self) -> bool:
        """Modifie vraiment le système : mérite un point de restauration et une entrée dans l'historique."""
        return self.batch and (self.admin or not self.auto or self.undo is not None)


@dataclass
class Finding:
    title: str
    severity: Severity
    detail: str = ""
    actions: list[Action] = field(default_factory=list)
    category: str = ""

    @property
    def auto_action(self) -> Action | None:
        """Correction appliquée par « Tout corriger » : la 1re action, pour les problèmes importants."""
        if self.severity >= Severity.WARNING and self.actions and self.actions[0].auto:
            return self.actions[0]
        return None


def score(results: dict[str, list[Finding]]) -> int:
    """Note sur 100. La pénalité est plafonnée par catégorie : 10 logiciels à mettre à jour ne pèsent pas plus
    qu'un problème grave."""
    penalty = {Severity.CRITICAL: 15, Severity.WARNING: 4}
    return max(0, 100 - sum(min(30, sum(penalty.get(f.severity, 0) for f in findings))
                            for findings in results.values()))


def run_checks(checks: list[Callable[[], list[Finding]]], category: str) -> list[Finding]:
    """Lance les vérifications en parallèle ; une vérification qui plante devient un constat « Info »."""
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = [(check, pool.submit(check)) for check in checks]
    findings: list[Finding] = []
    for check, future in futures:
        try:
            findings.extend(future.result())
        except Exception as exc:
            label = (check.__doc__ or check.__name__).strip()
            findings.append(Finding(f"{label} : vérification impossible", Severity.INFO, str(exc)))
    for finding in findings:
        finding.category = category
    findings.sort(key=lambda f: -f.severity)
    return findings
