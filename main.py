"""Autowash : point d'entrée."""
import argparse
import sys
import time
import traceback

from autowash.system import data_dir, is_admin, relaunch_as_admin


def _log_crash() -> str:
    """Écrit l'erreur dans %LOCALAPPDATA%\\Autowash\\autowash.log (la version .exe n'a pas de console)."""
    path = data_dir() / "autowash.log"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {sys.argv}\n{traceback.format_exc()}\n")
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Autowash : analyse de sécurité et de santé du PC.")
    parser.add_argument("--report", action="store_true", help="analyse complète en mode texte, sans interface")
    parser.add_argument("--auto", action="store_true", help="installe les mises à jour urgentes (tâche planifiée)")
    parser.add_argument("--no-admin", action="store_true", help="ne pas demander les droits administrateur")
    args, _ = parser.parse_known_args()  # un argument inconnu (raccourci mal réglé…) ne doit pas empêcher le lancement

    if args.report:
        from autowash.cli import report
        return report()
    if args.auto:
        from autowash.updates import run_scheduled
        run_scheduled()
        return 0
    if not args.no_admin and not is_admin() and relaunch_as_admin("--no-admin"):
        return 0  # l'instance administrateur prend le relais

    from autowash.gui import App
    App().mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log = _log_crash()
        from tkinter import messagebox
        messagebox.showerror("Autowash", f"Autowash n'a pas pu démarrer.\n\n{traceback.format_exc(limit=3)}\n"
                                         f"Détails : {log}")
        sys.exit(1)
