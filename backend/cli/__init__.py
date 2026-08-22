"""Host-side command-line entry points for NEX Studio Visual.

These run on the HOST (``python -m backend.cli.<name>``), against the same database the backend uses.
They exist for operations that have no cockpit screen because the cockpit's user is the Manažér, not the
technical team — :mod:`backend.cli.dedo_message` (answer a build's AI Agent) and
:mod:`backend.cli.dedo_unblock` (release a build stuck on a NEX Studio bug, once it is really fixed).
"""
