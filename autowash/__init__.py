"""Autowash : analyse et nettoyage de sécurité pour Windows."""
__version__ = "1.0.7"  # avant les imports : selfupdate le lit pendant l'initialisation du paquet

from . import browsers, cleanup, drivers, health, malware, optimize, programs, startup, updates, vulnerabilities

MODULES = [malware, vulnerabilities, updates, drivers, health, cleanup, optimize, startup, programs, browsers]
