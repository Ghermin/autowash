"""Utilitaires Windows : droits admin, registre, lancement de programmes."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

HKLM = winreg.HKEY_LOCAL_MACHINE
HKCU = winreg.HKEY_CURRENT_USER
CREATE_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def app_command(*args: str) -> tuple[str, list[str]]:
    """Exécutable et arguments pour relancer Autowash (sans fenêtre console)."""
    if getattr(sys, "frozen", False):
        return sys.executable, list(args)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    main = Path(__file__).resolve().parent.parent / "main.py"
    return str(pythonw if pythonw.exists() else exe), [str(main), *args]


def relaunch_as_admin(*args: str) -> bool:
    exe, argv = app_command(*args)
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, subprocess.list2cmdline(argv), None, 1) > 32


def open_target(target: str) -> str:
    os.startfile(target)
    return "Ouvert."


def launch(*cmd: str) -> str:
    subprocess.Popen(cmd)
    return "Ouvert."


class _SHFileOp(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]


def recycle(path: str) -> None:
    """Déplace un fichier ou dossier dans la corbeille (Windows avertit s'il doit le supprimer définitivement)."""
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = (
        3, 0x4, 0x10, 0x40, 0x400, 0x4000)
    op = _SHFileOp(wFunc=FO_DELETE, pFrom=os.path.abspath(path) + "\0",
                   fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted:
        raise OSError(f"Impossible de déplacer {path} dans la corbeille (code {code}) : fichier en cours "
                      "d'utilisation ou droits insuffisants.")


def _recycle_info(info_file: str) -> tuple[str, int] | None:
    """Chemin d'origine et date de suppression lus dans un fichier $I de la corbeille."""
    try:
        with open(info_file, "rb") as f:
            data = f.read()
    except OSError:
        return None
    version, deleted = int.from_bytes(data[0:8], "little"), int.from_bytes(data[16:24], "little")
    if version == 2:  # Windows 10 et 11
        length = int.from_bytes(data[24:28], "little")
        raw = data[28:28 + 2 * length]
    else:
        raw = data[24:24 + 520]
    return raw.decode("utf-16-le", errors="ignore").split("\0")[0], deleted


def restore_recycled(original: str) -> str:
    """Remet à sa place un fichier ou dossier envoyé dans la corbeille par recycle()."""
    original = os.path.abspath(original)
    if os.path.exists(original):
        raise FileExistsError(f"{original} existe déjà : rien à restaurer.")
    bin_root = os.path.join(os.path.splitdrive(original)[0] + "\\", "$Recycle.Bin")
    matches = []
    for sid in os.listdir(bin_root) if os.path.isdir(bin_root) else []:
        folder = os.path.join(bin_root, sid)
        try:
            names = os.listdir(folder)
        except OSError:
            continue  # corbeille d'un autre utilisateur
        for name in names:
            if name.startswith("$I"):
                info = _recycle_info(os.path.join(folder, name))
                if info and info[0].lower() == original.lower():
                    matches.append((info[1], folder, name))
    if not matches:
        raise FileNotFoundError(f"{original} n'est plus dans la corbeille (vidée entre-temps ?).")
    _, folder, name = max(matches)  # la suppression la plus récente
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(os.path.join(folder, "$R" + name[2:]), original)
    os.remove(os.path.join(folder, name))
    return f"{original} restauré depuis la corbeille."


NOTIFY_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$text.Item(0).AppendChild($xml.CreateTextNode(__TITLE__)) | Out-Null
$text.Item(1).AppendChild($xml.CreateTextNode(__MESSAGE__)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def notify(title: str, message: str) -> None:
    """Notification Windows (centre de notifications), sans dépendance externe."""
    from . import ps  # import local : ps n'a pas besoin de system, mais évite tout cycle futur
    ps.run(NOTIFY_PS.replace("__TITLE__", ps.quote(title)).replace("__MESSAGE__", ps.quote(message)))


def reg_get(root, path: str, name: str, default=None):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def reg_exists(root, path: str) -> bool:
    try:
        winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
        return True
    except OSError:
        return False


def reg_set_dword(root, path: str, name: str, value: int) -> None:
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)


def reg_set(root, path: str, name: str, value: int | str) -> None:
    """Écrit une valeur DWORD (int) ou chaîne (str), en créant la clé si besoin."""
    kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
        winreg.SetValueEx(key, name, 0, kind, value)


def reg_delete(root, path: str, name: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def data_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Autowash"
    path.mkdir(parents=True, exist_ok=True)
    return path


def human_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"
