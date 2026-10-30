"""Interface graphique d'Autowash (CustomTkinter)."""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

import customtkinter as ctk

from . import MODULES, journal, programs, restore
from .models import Action, Finding, Severity, score
from .system import human_size, is_admin, launch, relaunch_as_admin

ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets" / "autowash.ico"

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

COLORS = {Severity.CRITICAL: "#e5484d", Severity.WARNING: "#f5a524", Severity.INFO: "#3b82f6", Severity.OK: "#30a46c"}
MUTED = ("gray40", "gray65")
CARD = ("gray95", "gray17")
TEXT = ("gray10", "gray90")


def _font(size: int = 13, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")


def _counts(findings: list[Finding]) -> str:
    crit = sum(f.severity == Severity.CRITICAL for f in findings)
    warn = sum(f.severity == Severity.WARNING for f in findings)
    parts = [f"{crit} critique(s)"] if crit else []
    parts += [f"{warn} à surveiller"] if warn else []
    return " · ".join(parts) or "Aucun problème"


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Autowash")
        self.geometry("1180x780")
        self.minsize(960, 640)
        if ICON.exists():
            self.iconbitmap(str(ICON))
        self.admin = is_admin()
        self.results: dict[str, list[Finding]] = {}
        self.scanning: set[str] = set()
        self._full_scan = False  # une analyse complète est en cours : son score sera enregistré
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy = 0

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=(18, 0))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        if not self.admin:
            self._build_admin_banner()

        self.pages: dict[str, ctk.CTkFrame] = {"dashboard": DashboardPage(self.content, self)}
        for module in MODULES:
            page_class = ProgramsPage if module is programs else CategoryPage
            self.pages[module.NAME] = page_class(self.content, self, module)
        self.pages["history"] = HistoryPage(self.content, self)

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.grid(row=1, column=1, sticky="ew", padx=24, pady=10)
        status.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(status, text="Prêt", text_color=MUTED, anchor="w")
        self.status.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(status, mode="indeterminate", width=180)
        self.progress.grid(row=0, column=1)
        self.progress.grid_remove()

        self.show("dashboard")
        self.after(100, self._poll)
        self.after(300, self.scan_all)
        if self.admin:  # purge des points de restauration de plus de 7 jours
            self.after(2000, lambda: self.run_bg("Purge des anciens points de restauration…", restore.prune,
                                                 lambda *_: None))

    # --- Mise en page ---------------------------------------------------------

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=230, corner_radius=0)
        bar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="Autowash", font=_font(24, True)).pack(padx=22, pady=(26, 0), anchor="w")
        ctk.CTkLabel(bar, text="Sécurité et santé du PC", text_color=MUTED).pack(padx=22, pady=(0, 18), anchor="w")
        self.nav: dict[str, ctk.CTkButton] = {}
        entries = [("dashboard", "Tableau de bord")] + [(m.NAME, m.NAME) for m in MODULES] + [("history", "Historique")]
        for key, label in entries:
            button = ctk.CTkButton(bar, text=label, anchor="w", height=36, font=_font(14),
                                   fg_color="transparent", text_color=TEXT, hover_color=("gray80", "gray25"),
                                   command=lambda k=key: self.show(k))
            button.pack(fill="x", padx=12, pady=2)
            self.nav[key] = button
        ctk.CTkButton(bar, text="Analyse complète", height=42, font=_font(14, True),
                      command=self.scan_all).pack(side="bottom", fill="x", padx=12, pady=20)

    def _build_admin_banner(self) -> None:
        banner = ctk.CTkFrame(self.content, fg_color=("#fff4d6", "#3a2e10"), corner_radius=8)
        banner.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        banner.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(banner, text="Mode limité : les corrections système nécessitent les droits administrateur.",
                     text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        ctk.CTkButton(banner, text="Relancer en administrateur", width=200,
                      command=self.relaunch_admin).grid(row=0, column=1, padx=10, pady=8)

    def show(self, key: str) -> None:
        for name, page in self.pages.items():
            if name == key:
                page.grid(row=1, column=0, sticky="nsew")
                if hasattr(page, "on_show"):
                    page.on_show()
            else:
                page.grid_remove()
        for name, button in self.nav.items():
            button.configure(fg_color=("gray82", "gray25") if name == key else "transparent")

    def _refresh_nav(self) -> None:
        for module in MODULES:
            issues = sum(f.severity >= Severity.WARNING for f in self.results.get(module.NAME, []))
            self.nav[module.NAME].configure(text=f"{module.NAME}   ({issues})" if issues else module.NAME)

    # --- Tâches de fond ---------------------------------------------------------

    def post(self, callback: Callable[[], None]) -> None:
        """Exécute callback dans le thread de l'interface (appelable depuis n'importe quel thread)."""
        self._queue.put(callback)

    def run_bg(self, label: str, func: Callable, on_done: Callable[[object, Exception | None], None]) -> None:
        self._busy += 1
        self.status.configure(text=label)
        self.progress.grid()
        self.progress.start()

        def worker() -> None:
            try:
                result, error = func(), None
            except Exception as exc:
                result, error = None, exc
            self.post(lambda: self._finish(on_done, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, on_done, result, error) -> None:
        self._busy -= 1
        if self._busy == 0:
            self.progress.stop()
            self.progress.grid_remove()
            self.status.configure(text=f"Prêt — dernière opération à {time.strftime('%H:%M')}")
        on_done(result, error)

    def _poll(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()()
        self.after(100, self._poll)

    # --- Analyses et corrections ------------------------------------------------

    def scan(self, module) -> None:
        if module.NAME in self.scanning:
            return
        self.scanning.add(module.NAME)
        self.pages[module.NAME].set_loading()
        self.pages["dashboard"].refresh()

        def done(findings, error) -> None:
            self.scanning.discard(module.NAME)
            if error:
                findings = [Finding("Analyse impossible", Severity.WARNING, str(error), category=module.NAME)]
            self.results[module.NAME] = findings
            self.pages[module.NAME].show_findings(findings)
            if self._full_scan and not self.scanning:
                self._full_scan = False
                everything = [f for fs in self.results.values() for f in fs]
                journal.record_score(score(self.results), sum(f.severity == Severity.CRITICAL for f in everything),
                                     sum(f.severity == Severity.WARNING for f in everything))
            self.pages["dashboard"].refresh()
            self._refresh_nav()

        self.run_bg(f"Analyse : {module.NAME}…", module.scan, done)

    def scan_all(self) -> None:
        self._full_scan = True
        for module in MODULES:
            self.scan(module)

    def module_named(self, name: str):
        return next((m for m in MODULES if m.NAME == name), None)

    def _execute(self, module, label: str, action: Action) -> str:
        """Exécute une action et l'inscrit dans l'historique (sauf les simples raccourcis « Ouvrir… »)."""
        category = module.NAME if module else "Autowash"
        try:
            message = action.run()
        except Exception as exc:
            if action.batch:
                journal.record(category, label, action.label, False, str(exc))
            raise
        if action.batch:
            journal.record(category, label, action.label, True, message, action.undo)
        return message

    def _checkpoint(self, reason: str) -> str | None:
        """Point de restauration avant une modification (mode administrateur uniquement)."""
        if not self.admin:
            return None
        self.post(lambda: self.status.configure(text="Création d'un point de restauration…"))
        try:
            return restore.create(reason)
        except Exception as exc:
            return f"Point de restauration impossible : {exc}"

    def relaunch_admin(self) -> None:
        if relaunch_as_admin("--no-admin"):
            self.destroy()

    def _check_admin(self, what: str) -> bool:
        if self.admin:
            return True
        if messagebox.askyesno("Droits administrateur requis",
                               f"{what} nécessite les droits administrateur.\n\nRelancer Autowash en administrateur ?"):
            self.relaunch_admin()
        return False

    def run_action(self, action: Action, module=None, label: str | None = None) -> None:
        if action.admin and not self._check_admin(f"« {action.label} »"):
            return
        if action.confirm and not messagebox.askyesno(action.label, action.confirm):
            return

        def work() -> str:
            checkpoint = self._checkpoint(action.label) if action.significant else None
            message = self._execute(module, label or action.label, action)
            return f"{message}\n\n{checkpoint}" if checkpoint else message

        def done(message, error) -> None:
            if error:
                messagebox.showerror(action.label, str(error))
            elif message and message != "Ouvert.":
                messagebox.showinfo(action.label, message)
            if module and not error and message != "Ouvert.":
                self.scan(module)

        self.run_bg(f"{action.label}…", work, done)

    def fix_all(self, modules) -> None:
        plan = [(m, f.title, f.auto_action) for m in modules for f in self.results.get(m.NAME, []) if f.auto_action]
        if not plan:
            pending = [m.NAME for m in modules if m.NAME not in self.results]
            messagebox.showinfo("Tout corriger", "Aucune correction automatique à appliquer."
                                + (f"\n\nAnalyse en attente : {', '.join(pending)}." if pending else ""))
            return
        self.run_plan("Tout corriger", plan)

    def run_plan(self, title: str, plan: list[tuple[object, str, Action]]) -> None:
        """Applique une liste d'actions (module, libellé, action) après une seule confirmation."""
        seen: set[int] = set()
        plan = [p for p in plan if not (id(p[2]) in seen or seen.add(id(p[2])))]  # actions partagées : une fois
        admin_needed = [p for p in plan if p[2].admin]
        if admin_needed and not self.admin:
            answer = messagebox.askyesnocancel(
                "Droits administrateur requis",
                f"{len(admin_needed)} action(s) sur {len(plan)} nécessitent les droits administrateur.\n\n"
                "Oui : relancer Autowash en administrateur\nNon : appliquer seulement les autres")
            if answer is None:
                return
            if answer:
                self.relaunch_admin()
                return
            plan = [p for p in plan if not p[2].admin]
            if not plan:
                return
        lines = "\n".join(f"• {label} → {a.label}" for _, label, a in plan[:15])
        if len(plan) > 15:
            lines += f"\n… et {len(plan) - 15} autre(s)"
        if not messagebox.askyesno(title, f"Actions qui vont être appliquées :\n\n{lines}\n\nContinuer ?"):
            return

        def work() -> list[str]:
            checkpoint = self._checkpoint(title)
            report = [f"ℹ {checkpoint}"] if checkpoint else []
            for n, (module, label, action) in enumerate(plan, 1):
                self.post(lambda n=n, t=label: self.status.configure(text=f"{title} ({n}/{len(plan)}) : {t}"))
                try:
                    report.append(f"✔ {self._execute(module, label, action)}")
                except Exception as exc:
                    report.append(f"✘ {label} : {exc}")
            return report

        def done(report, error) -> None:
            messagebox.showinfo(f"{title} : terminé", "\n".join(report or [str(error)]))
            for module in {id(m): m for m, _, _ in plan}.values():
                self.scan(module)

        self.run_bg(f"{title}…", work, done)


class FindingCard(ctk.CTkFrame):
    def __init__(self, master, app: App, finding: Finding, module, show_category: bool = False,
                 selected: ctk.BooleanVar | None = None, checkbox_slot: bool = False) -> None:
        super().__init__(master, fg_color=CARD, corner_radius=10)
        color = COLORS[finding.severity]
        self.grid_columnconfigure(2, weight=1)
        if selected is not None:
            ctk.CTkCheckBox(self, text="", width=24, variable=selected).grid(row=0, column=0, rowspan=3, padx=(12, 0))
        elif checkbox_slot:  # garde l'alignement avec les cartes cochables
            ctk.CTkFrame(self, width=24, height=1, fg_color="transparent").grid(row=0, column=0, padx=(12, 0))
        ctk.CTkFrame(self, width=5, height=10, fg_color=color, corner_radius=3).grid(
            row=0, column=1, rowspan=3, sticky="ns", padx=(10, 12), pady=10)

        tag = finding.severity.label.upper() + (f"  ·  {finding.category}" if show_category else "")
        ctk.CTkLabel(self, text=tag, text_color=color, font=_font(11, True), anchor="w").grid(
            row=0, column=2, sticky="w", pady=(8, 0))
        ctk.CTkLabel(self, text=finding.title, font=_font(14, True), anchor="w", justify="left",
                     wraplength=540).grid(row=1, column=2, sticky="w")
        if finding.detail:
            ctk.CTkLabel(self, text=finding.detail, text_color=MUTED, anchor="w", justify="left",
                         wraplength=540).grid(row=2, column=2, sticky="w", pady=(2, 8))

        buttons = ctk.CTkFrame(self, fg_color="transparent", width=1, height=1)
        buttons.grid(row=0, column=3, rowspan=3, padx=12, pady=8)
        for i, action in enumerate(finding.actions):
            label = action.label + (" (admin)" if action.admin and not app.admin else "")
            style = {} if i == 0 and finding.severity >= Severity.WARNING else {
                "fg_color": "transparent", "border_width": 1, "text_color": TEXT}
            ctk.CTkButton(buttons, text=label, width=190, command=lambda a=action: app.run_action(a, module, finding.title),
                          **style).pack(pady=3)


class CategoryPage(ctk.CTkFrame):
    def __init__(self, master, app: App, module) -> None:
        super().__init__(master, fg_color="transparent")
        self.app, self.module = app, module
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self, text=module.NAME, font=_font(26, True), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(self, text=module.DESCRIPTION, text_color=MUTED, anchor="w").grid(row=1, column=0, sticky="w")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(14, 6))
        ctk.CTkButton(bar, text="Analyser", width=110, command=lambda: app.scan(module)).pack(side="left")
        ctk.CTkButton(bar, text="Tout corriger", width=130, fg_color=COLORS[Severity.OK], hover_color="#268a5a",
                      command=lambda: app.fix_all([module])).pack(side="left", padx=8)
        for tool in module.TOOLS:
            ctk.CTkButton(bar, text=tool.label, fg_color="transparent", border_width=1, text_color=TEXT,
                          command=lambda t=tool: app.run_action(t, module)).pack(side="left", padx=4)

        selection = ctk.CTkFrame(self, fg_color="transparent")
        selection.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        self.summary = ctk.CTkLabel(selection, text="", text_color=MUTED, anchor="w")
        self.summary.pack(side="left")
        self.batch_label = getattr(module, "BATCH_LABEL", "Appliquer la sélection")
        self.batch_button = ctk.CTkButton(selection, text=self.batch_label, state="disabled",
                                          command=self.apply_selection)
        self.batch_button.pack(side="right")
        self.select_all = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(selection, text="Tout cocher", variable=self.select_all,
                        command=self._toggle_all).pack(side="right", padx=12)
        self.selection: list[tuple[Finding, ctk.BooleanVar]] = []

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=4, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)

    def set_loading(self) -> None:
        self.summary.configure(text="Analyse en cours…")

    def show_findings(self, findings: list[Finding]) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        self.selection = []
        self.select_all.set(False)
        for finding in findings:
            var = None
            if finding.actions and finding.actions[0].batch:
                var = ctk.BooleanVar(value=False)
                var.trace_add("write", lambda *_: self._refresh_selection())
                self.selection.append((finding, var))
            FindingCard(self.list, self.app, finding, self.module, selected=var,
                        checkbox_slot=True).pack(fill="x", pady=4, padx=(0, 6))
        self._refresh_selection()
        self.summary.configure(text=f"{_counts(findings)} — analysé à {time.strftime('%H:%M')}")

    def _toggle_all(self) -> None:
        for _, var in self.selection:
            var.set(self.select_all.get())

    def _refresh_selection(self) -> None:
        count = sum(var.get() for _, var in self.selection)
        self.batch_button.configure(text=f"{self.batch_label} ({count})" if count else self.batch_label,
                                    state="normal" if count else "disabled")

    def apply_selection(self) -> None:
        plan = [(self.module, f.title, f.actions[0]) for f, var in self.selection if var.get()]
        if plan:
            self.app.run_plan(self.batch_label, plan)


class ProgramsPage(CategoryPage):
    """Page Programmes : bloatwares en cartes, puis la liste complète dans un tableau à cocher."""

    COLUMNS = (("check", "", 36), ("name", "Nom", 330), ("publisher", "Éditeur", 190),
               ("version", "Version", 120), ("size", "Taille", 80), ("date", "Installé le", 90))

    def __init__(self, master, app: App, module) -> None:
        super().__init__(master, app, module)
        self.checked: set[str] = set()
        self.rows: dict[str, programs.Program] = {}
        self.sort_key, self.sort_reverse = "name", False
        self.grid_rowconfigure(5, weight=2)

        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        box.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=8)
        ctk.CTkLabel(top, text="Tous les programmes", font=_font(15, True)).pack(side="left")
        ctk.CTkLabel(top, text="Rechercher :", text_color=MUTED).pack(side="left", padx=(16, 4))
        self.search = ctk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill())
        ctk.CTkEntry(top, textvariable=self.search, width=200).pack(side="left")
        self.hide_components = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(top, text="Masquer runtimes et pilotes", variable=self.hide_components,
                        command=self._fill).pack(side="left", padx=12)
        self.uninstall_button = ctk.CTkButton(top, text="Désinstaller", state="disabled",
                                              fg_color=COLORS[Severity.CRITICAL], hover_color="#c23b3f",
                                              command=self._uninstall_checked)
        self.uninstall_button.pack(side="right")

        self._style()
        self.tree = ttk.Treeview(box, columns=[c[0] for c in self.COLUMNS], show="headings", selectmode="none",
                                 style="Autowash.Treeview")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self._sort(k))
            self.tree.column(key, width=width, minwidth=30, stretch=key == "name",
                             anchor="center" if key == "check" else "w")
        self.tree.tag_configure("component", foreground="gray55")
        scroll = ctk.CTkScrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 10))
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double)

    def _style(self) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg, head = ("#242424", "#e6e6e6", "#303030") if dark else ("#f7f7f7", "#1a1a1a", "#e2e2e2")
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Autowash.Treeview", background=bg, fieldbackground=bg, foreground=fg, borderwidth=0,
                        rowheight=int(26 * scale), font=("Segoe UI", 10))
        style.configure("Autowash.Treeview.Heading", background=head, foreground=fg, relief="flat",
                        font=("Segoe UI", 10, "bold"))
        style.map("Autowash.Treeview.Heading", background=[("active", head)])

    def show_findings(self, findings: list[Finding]) -> None:
        super().show_findings(findings)
        self.checked &= {p.id for p in programs.PROGRAMS}
        self._fill()

    def _fill(self) -> None:
        query = self.search.get().lower()
        rows = [p for p in programs.PROGRAMS
                if (p.id in self.checked or not (self.hide_components.get() and p.component))
                and (query in p.name.lower() or query in p.publisher.lower())]
        sort_keys = {"check": lambda p: p.id not in self.checked, "name": lambda p: p.name.lower(),
                     "publisher": lambda p: p.publisher.lower(), "version": lambda p: p.version,
                     "size": lambda p: p.size, "date": lambda p: p.installed}
        rows.sort(key=sort_keys[self.sort_key], reverse=self.sort_reverse)
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        for n, p in enumerate(rows):
            iid = str(n)
            self.rows[iid] = p
            self.tree.insert("", "end", iid=iid, tags=("component",) if p.component else (), values=(
                "☑" if p.id in self.checked else "☐", p.name, p.publisher, p.version,
                human_size(p.size) if p.size else "", p.installed))
        self._refresh_uninstall()

    def _sort(self, key: str) -> None:
        self.sort_reverse = not self.sort_reverse if key == self.sort_key else key in ("size", "date")
        self.sort_key = key
        self._fill()

    def _on_click(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or self.tree.identify_region(event.x, event.y) != "cell":
            return
        program = self.rows[iid]
        self.checked ^= {program.id}
        self.tree.set(iid, "check", "☑" if program.id in self.checked else "☐")
        self._refresh_uninstall()

    def _on_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.app.run_action(programs.uninstall_action(self.rows[iid]), self.module, self.rows[iid].name)

    def _refresh_uninstall(self) -> None:
        count = len(self.checked)
        self.uninstall_button.configure(text=f"Désinstaller ({count})" if count else "Désinstaller",
                                        state="normal" if count else "disabled")

    def _uninstall_checked(self) -> None:
        plan = [(self.module, p.name + (" (composant système)" if p.component else ""), programs.uninstall_action(p))
                for p in programs.PROGRAMS if p.id in self.checked]
        self.app.run_plan("Désinstaller", plan)


class DashboardPage(ctk.CTkFrame):
    def __init__(self, master, app: App) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self, text="Tableau de bord", font=_font(26, True), anchor="w").grid(row=0, column=0, sticky="w")

        top = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12)
        top.grid(row=1, column=0, sticky="ew", pady=14)
        top.grid_columnconfigure(1, weight=1)
        self.score = ctk.CTkLabel(top, text="…", font=_font(52, True), width=140)
        self.score.grid(row=0, column=0, rowspan=2, padx=20, pady=16)
        self.verdict = ctk.CTkLabel(top, text="Analyse en cours…", font=_font(20, True), anchor="w")
        self.verdict.grid(row=0, column=1, sticky="sw")
        self.verdict_detail = ctk.CTkLabel(top, text="", text_color=MUTED, anchor="w")
        self.verdict_detail.grid(row=1, column=1, sticky="nw")
        chart_box = ctk.CTkFrame(top, fg_color="transparent")
        chart_box.grid(row=0, column=2, rowspan=2, padx=10, pady=10)
        ctk.CTkLabel(chart_box, text="Évolution du score", text_color=MUTED, font=_font(11)).pack(anchor="w")
        self.chart = tk.Canvas(chart_box, width=220, height=64, highlightthickness=0, bd=0)
        self.chart.pack()
        actions = ctk.CTkFrame(top, fg_color="transparent")
        actions.grid(row=0, column=3, rowspan=2, padx=20)
        ctk.CTkButton(actions, text="Tout corriger (recommandé)", width=230, height=38, font=_font(14, True),
                      fg_color=COLORS[Severity.OK], hover_color="#268a5a",
                      command=lambda: app.fix_all(MODULES)).pack(pady=4)
        ctk.CTkButton(actions, text="Relancer l'analyse", width=230, fg_color="transparent", border_width=1,
                      text_color=TEXT, command=app.scan_all).pack(pady=4)

        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=2, column=0, sticky="ew")
        self.cards: dict[str, ctk.CTkLabel] = {}
        per_row = 5
        for i, module in enumerate(MODULES):
            col = i % per_row
            cards.grid_columnconfigure(col, weight=1, uniform="cards")
            card = ctk.CTkFrame(cards, fg_color=CARD, corner_radius=12, cursor="hand2")
            card.grid(row=i // per_row, column=col, sticky="nsew", pady=4,
                      padx=(0 if col == 0 else 5, 0 if col == per_row - 1 else 5))
            title = ctk.CTkLabel(card, text=module.NAME, font=_font(15, True), anchor="w")
            title.pack(fill="x", padx=14, pady=(10, 0))
            self.cards[module.NAME] = ctk.CTkLabel(card, text="En attente", text_color=MUTED, anchor="w",
                                                   justify="left", wraplength=140)
            self.cards[module.NAME].pack(fill="x", padx=14, pady=(0, 10))
            for widget in (card, title, self.cards[module.NAME]):  # toute la carte est cliquable
                widget.bind("<Button-1>", lambda _e, k=module.NAME: app.show(k))

        ctk.CTkLabel(self, text="À traiter en priorité", font=_font(18, True), anchor="w").grid(
            row=3, column=0, sticky="w", pady=(18, 6))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=4, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)

    def refresh(self) -> None:
        modules = {m.NAME: m for m in MODULES}
        for name, label in self.cards.items():
            findings = self.app.results.get(name)
            if name in self.app.scanning:
                label.configure(text="Analyse en cours…", text_color=MUTED)
            elif findings is not None:
                worst = max((f.severity for f in findings), default=Severity.OK)
                label.configure(text=_counts(findings),
                                text_color=COLORS[worst] if worst >= Severity.WARNING else COLORS[Severity.OK])

        everything = [f for findings in self.app.results.values() for f in findings]
        crit = sum(f.severity == Severity.CRITICAL for f in everything)
        warn = sum(f.severity == Severity.WARNING for f in everything)
        value = score(self.app.results)
        if self.app.scanning:
            self.verdict.configure(text="Analyse en cours…", text_color=TEXT)
        elif value >= 85:
            self.verdict.configure(text="Votre PC est en bon état", text_color=COLORS[Severity.OK])
        elif value >= 60:
            self.verdict.configure(text="Quelques points à corriger", text_color=COLORS[Severity.WARNING])
        else:
            self.verdict.configure(text="Action requise", text_color=COLORS[Severity.CRITICAL])
        self.score.configure(text=str(value) if everything else "…", text_color=_score_color(value))
        self.verdict_detail.configure(text=f"{crit} problème(s) critique(s), {warn} point(s) à surveiller")
        self._draw_chart()

        for child in self.list.winfo_children():
            child.destroy()
        priority = sorted((f for f in everything if f.severity >= Severity.WARNING), key=lambda f: -f.severity)
        for finding in priority[:10]:
            FindingCard(self.list, self.app, finding, modules[finding.category], show_category=True).pack(
                fill="x", pady=4, padx=(0, 6))
        if everything and not priority:
            ctk.CTkLabel(self.list, text="Rien d'urgent.", text_color=MUTED).pack(anchor="w")

    def _draw_chart(self) -> None:
        """Courbe des 30 dernières analyses complètes (score de 0 à 100)."""
        canvas, history = self.chart, journal.scores()[-30:]
        dark = ctk.get_appearance_mode() == "Dark"
        canvas.configure(bg="#2b2b2b" if dark else "#f2f2f2")
        canvas.delete("all")
        width, height, pad = 220, 64, 6
        grid = "#3a3a3a" if dark else "#dddddd"
        for level in (0, 50, 100):
            y = height - pad - level / 100 * (height - 2 * pad)
            canvas.create_line(pad, y, width - pad, y, fill=grid, dash=(2, 3))
        if len(history) < 2:
            canvas.create_text(width / 2, height / 2, text="Disponible après 2 analyses",
                               fill="gray55", font=("Segoe UI", 9))
            return
        step = (width - 2 * pad) / (len(history) - 1)
        points = [(pad + i * step, height - pad - h["score"] / 100 * (height - 2 * pad)) for i, h in enumerate(history)]
        color = _score_color(history[-1]["score"])
        canvas.create_line(*[c for p in points for c in p], fill=color, width=2, smooth=True)
        x, y = points[-1]
        canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)


class HistoryPage(ctk.CTkFrame):
    """Historique des actions (avec annulation) et points de restauration du système."""

    def __init__(self, master, app: App) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(self, text="Historique", font=_font(26, True), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(self, text="Tout ce qu'Autowash a modifié, avec annulation quand c'est possible, et les points "
                                "de restauration du système.", text_color=MUTED, anchor="w").grid(row=1, column=0, sticky="w")

        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=10)
        box.grid(row=2, column=0, sticky="ew", pady=14)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="Points de restauration", font=_font(15, True), anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        self.points = ctk.CTkLabel(box, text="", text_color=MUTED, anchor="w", justify="left")
        self.points.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 10))
        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.grid(row=0, column=1, rowspan=2, padx=12)
        ctk.CTkButton(buttons, text="Restaurer le système…", width=190,
                      command=lambda: launch("rstrui.exe")).pack(pady=3)
        ctk.CTkButton(buttons, text="Créer un point maintenant", width=190, fg_color="transparent", border_width=1,
                      text_color=TEXT, command=self._create_point).pack(pady=3)

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=3, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._load_points()
        for child in self.list.winfo_children():
            child.destroy()
        items = journal.entries(limit=100)
        if not items:
            ctk.CTkLabel(self.list, text="Aucune action pour l'instant.", text_color=MUTED).pack(anchor="w")
        for entry in items:
            self._row(entry)

    def _row(self, entry: dict) -> None:
        row = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=8)
        row.pack(fill="x", pady=3, padx=(0, 6))
        row.grid_columnconfigure(1, weight=1)
        color = COLORS[Severity.OK] if entry["ok"] else COLORS[Severity.CRITICAL]
        ctk.CTkLabel(row, text="✔" if entry["ok"] else "✘", text_color=color, font=_font(16, True), width=30).grid(
            row=0, column=0, rowspan=2, padx=(10, 4))
        ctk.CTkLabel(row, text=f"{entry['label']}  →  {entry['action']}", font=_font(13, True), anchor="w",
                     justify="left", wraplength=620).grid(row=0, column=1, sticky="w", pady=(6, 0))
        ctk.CTkLabel(row, text=f"{entry['time']} · {entry['category']} · {entry['message'][:300]}", text_color=MUTED,
                     anchor="w", justify="left", wraplength=620).grid(row=1, column=1, sticky="w", pady=(0, 6))
        if entry.get("undone"):
            ctk.CTkLabel(row, text=f"Annulée le {entry['undone']}", text_color=MUTED).grid(
                row=0, column=2, rowspan=2, padx=12)
        elif entry.get("undo"):
            ctk.CTkButton(row, text="Annuler", width=110, fg_color="transparent", border_width=1, text_color=TEXT,
                          command=lambda e=entry: self._undo(e)).grid(row=0, column=2, rowspan=2, padx=12)

    def _undo(self, entry: dict) -> None:
        if not messagebox.askyesno("Annuler", f"Annuler « {entry['action']} » sur « {entry['label']} » ?"):
            return

        def done(message, error) -> None:
            if error:
                messagebox.showerror("Annulation impossible", str(error) + (
                    "" if self.app.admin else "\n\nCertaines annulations nécessitent les droits administrateur."))
            else:
                messagebox.showinfo("Annulé", message)
                module = self.app.module_named(entry["category"])
                if module:
                    self.app.scan(module)
            self.refresh()

        self.app.run_bg("Annulation…", lambda: journal.undo(entry), done)

    def _load_points(self) -> None:
        if not self.app.admin:
            self.points.configure(text="Autowash crée un point de restauration avant chaque modification importante "
                                       "et le supprime après 7 jours.\nVisible et actif en mode administrateur.")
            return

        def done(points, error) -> None:
            if error:
                self.points.configure(text=f"Lecture impossible : {error}")
                return
            ours = [p for p in points if p["Ours"]]
            lines = [f"{p['Created']:%d/%m/%Y %H:%M} — {p['Description']}" for p in ours[:4]]
            self.points.configure(text=(f"{len(ours)} point(s) Autowash (supprimés après {restore.KEEP_DAYS} jours), "
                                        f"{len(points) - len(ours)} de Windows.\n" + "\n".join(lines)).strip())

        self.app.run_bg("Lecture des points de restauration…", restore.list_points, done)

    def _create_point(self) -> None:
        if not self.app._check_admin("La création d'un point de restauration"):
            return

        def done(message, error) -> None:
            messagebox.showinfo("Point de restauration", str(error) if error else message)
            self._load_points()

        self.app.run_bg("Création d'un point de restauration…", lambda: restore.create("point manuel"), done)


def _score_color(value: int) -> str:
    return COLORS[Severity.OK] if value >= 85 else COLORS[Severity.WARNING] if value >= 60 else COLORS[Severity.CRITICAL]
