from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def clean_cce_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isola o teste do ambiente real: sem variáveis CCE_* e com um diretório de trabalho vazio.

    Devolve o diretório de trabalho, onde um `.env` de teste pode ser criado.
    """
    for name in list(os.environ):
        if name.upper().startswith("CCE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return tmp_path
