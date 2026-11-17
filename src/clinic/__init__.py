"""Clinic mode: server-side policy layer of the clinic fork.

Everything here is inert unless the environment variable CLINIC_MODE is on, so
upstream behaviour (and every upstream test) is unchanged by default.

This file stays free of imports on purpose: src.models.system imports
src.clinic.config at module load, and a package __init__ that pulled in Flask
or the models would create an import cycle.
"""
