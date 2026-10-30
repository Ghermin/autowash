"""Autowash : analyse et nettoyage de sécurité pour Windows."""
from . import browsers, cleanup, drivers, health, malware, optimize, programs, startup, updates, vulnerabilities

MODULES = [malware, vulnerabilities, updates, drivers, health, cleanup, optimize, startup, programs, browsers]
