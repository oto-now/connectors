"""Génère la sortie TypeScript, la sortie Python de données, les descriptions servies et la carte du parc, les écrit, ou vérifie que la sortie commitée est à jour."""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

from . import (auth_cases, background_cases, body_cases, carte, cases, error_cases, file_cases, jwt_bearer_cases,
               list_body_cases, nested_body_cases, python, served, strip_cases)
from .descriptions import CONNECTORS, REPO, load_all
from .typescript import GENERATED_MARK, Module, connector_module, index_source

OUTPUT = REPO / "ts" / "src"


@dataclass
class Result:
    modules: list[Module]
    files: dict[str, str]
    python: str
    served: dict[str, str]
    carte: str


def generate(root: pathlib.Path = CONNECTORS) -> Result:
    """Toutes les descriptions et les cas communs (coût, fichier rendu, retrait de la sortie, authentification, compte de
    service, table d'erreurs, corps entier, fonction longue, corps emboîté, corps en liste), contrôlés d'abord : un seul fautif, et rien
    n'est généré."""
    descriptions = load_all(root)
    modules = [connector_module(description) for description in descriptions]
    files = {f"{m.connector}.ts": m.source for m in modules}
    files["index.ts"] = index_source(modules)
    files[cases.OUTPUT_NAME] = cases.typescript_source(cases.load(root / cases.CASES.relative_to(CONNECTORS)))
    files[file_cases.OUTPUT_NAME] = file_cases.typescript_source(
        file_cases.load(root / file_cases.CASES.relative_to(CONNECTORS)))
    files[strip_cases.OUTPUT_NAME] = strip_cases.typescript_source(
        strip_cases.load(root / strip_cases.CASES.relative_to(CONNECTORS)))
    files[auth_cases.OUTPUT_NAME] = auth_cases.typescript_source(auth_cases.load(root / auth_cases.CASES.relative_to(CONNECTORS)))
    files[jwt_bearer_cases.OUTPUT_NAME] = jwt_bearer_cases.typescript_source(
        jwt_bearer_cases.load(root / jwt_bearer_cases.CASES.relative_to(CONNECTORS)))
    files[error_cases.OUTPUT_NAME] = error_cases.typescript_source(
        error_cases.load(root / error_cases.CASES.relative_to(CONNECTORS)))
    files[body_cases.OUTPUT_NAME] = body_cases.typescript_source(
        body_cases.load(root / body_cases.CASES.relative_to(CONNECTORS)))
    files[background_cases.OUTPUT_NAME] = background_cases.typescript_source(
        background_cases.load(root / background_cases.CASES.relative_to(CONNECTORS)))
    files[nested_body_cases.OUTPUT_NAME] = nested_body_cases.typescript_source(
        nested_body_cases.load(root / nested_body_cases.CASES.relative_to(CONNECTORS)))
    files[list_body_cases.OUTPUT_NAME] = list_body_cases.typescript_source(
        list_body_cases.load(root / list_body_cases.CASES.relative_to(CONNECTORS)))
    return Result(modules=modules, files=files, python=python.python_source(descriptions),
                  served=served.files(descriptions), carte=carte.markdown_source(descriptions, modules))


def _generated_on_disk(output: pathlib.Path, pattern: str = "*.ts", mark: str = GENERATED_MARK) -> set[str]:
    if not output.is_dir():
        return set()
    return {
        path.name for path in output.glob(pattern)
        if path.read_text(encoding="utf-8").startswith(mark)
    }


def _write_dir(files: dict[str, str], output: pathlib.Path, pattern: str, mark: str) -> None:
    """Les fichiers générés d'un dossier, écrits ; ceux qui n'ont plus de source, retirés."""
    output.mkdir(parents=True, exist_ok=True)
    for gone in _generated_on_disk(output, pattern, mark) - set(files):
        (output / gone).unlink()
    for name, source in files.items():
        (output / name).write_text(source, encoding="utf-8")


def _stale_dir(files: dict[str, str], output: pathlib.Path, pattern: str, mark: str) -> list[str]:
    """Les fichiers générés d'un dossier qui diffèrent de leur source, ou qui n'en ont plus, par leur nom."""
    differ = [name for name, source in sorted(files.items())
              if not (output / name).exists() or (output / name).read_text(encoding="utf-8") != source]
    return differ + sorted(_generated_on_disk(output, pattern, mark) - set(files))


def write(result: Result, output: pathlib.Path = OUTPUT, python_output: pathlib.Path = python.OUTPUT,
          carte_output: pathlib.Path = carte.OUTPUT, served_output: pathlib.Path = served.OUTPUT) -> None:
    _write_dir(result.files, output, "*.ts", GENERATED_MARK)
    python_output.parent.mkdir(parents=True, exist_ok=True)
    python_output.write_text(result.python, encoding="utf-8")
    _write_dir(result.served, served_output, "*.py", served.GENERATED_MARK)
    carte_output.write_text(result.carte, encoding="utf-8")


def stale(result: Result, output: pathlib.Path = OUTPUT, python_output: pathlib.Path = python.OUTPUT,
          carte_output: pathlib.Path = carte.OUTPUT, served_output: pathlib.Path = served.OUTPUT) -> list[str]:
    """Les fichiers dont la version commitée n'est pas celle que la fabrique produit : ceux de `ts/src/` par leur nom,
    le module Python de données, les descriptions servies et la carte du parc par leur chemin dans le dépôt."""
    differ = _stale_dir(result.files, output, "*.ts", GENERATED_MARK)
    if not python_output.exists() or python_output.read_text(encoding="utf-8") != result.python:
        differ.append(python.RELATIVE)
    differ += [f"{served.RELATIVE}/{name}" for name in _stale_dir(result.served, served_output, "*.py",
                                                                  served.GENERATED_MARK)]
    if not carte_output.exists() or carte_output.read_text(encoding="utf-8") != result.carte:
        differ.append(carte.RELATIVE)
    return differ
