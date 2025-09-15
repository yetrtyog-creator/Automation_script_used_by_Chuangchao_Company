#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path

def to_posix(p: str | Path) -> str:
    return str(Path(p)).replace("\\", "/")

def parent_name(p: str | Path) -> str:
    return Path(p).parent.name

def grandparent_name(p: str | Path) -> str:
    return Path(p).parent.parent.name
