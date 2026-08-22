"""Host-side command-line entry points for NEX Studio Visual.

These run on the HOST (``python -m backend.cli.<name>``), against the same database the backend uses.
They exist for operations that have no cockpit screen because the cockpit's user is the Manažér, not the
technical team — see :mod:`backend.cli.dedo_message`.
"""
