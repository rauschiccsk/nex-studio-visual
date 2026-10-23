"""What the project page shows of the code nobody uses that earlier versions left behind (DEV-156)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DeadCodeFindingRead(BaseModel):
    kind: str
    path: str
    name: str
    line: int
    #: The finding as the Manažér reads it („funkciu X (súbor:riadok) nikto nevolá“).
    sentence: str


class DeadCodeRead(BaseModel):
    findings: list[DeadCodeFindingRead]
    #: Why a part of the check did not run, each as a sentence.
    not_run: list[str]
    #: The fast fix's text that cleans them up — ``None`` when there is nothing to clean.
    brief: Optional[str]
