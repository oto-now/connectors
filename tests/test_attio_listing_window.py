"""Ce que les listings Attio mettent VRAIMENT sur le fil (notes / tâches / réunions).

Quatre signaux d'usage, tous d'une procédure quotidienne qui n'arrive pas à lire
les comptes rendus d'appels écrits « aujourd'hui » :
- **#586** — `attio_note op=list` n'expose ni `limit`, ni `offset`, ni filtre de
  date, et ne rend que les DIX notes les plus ANCIENNES du workspace : les notes
  du jour sont littéralement inatteignables. Même défaut sur `attio_task`.
  `attio_meeting` accepte un `offset` mais l'API l'IGNORE — `offset=2000` rend
  les deux mêmes réunions de janvier 2023.
- **#597** — resignalé onze jours plus tard.

Contrat amont relevé le 27/08/2026 par DIFFÉRENTIEL 400/200 contre l'API réelle
(un paramètre RECONNU refuse une valeur absurde ; un paramètre IGNORÉ l'avale) :

| endpoint       | pagination            | tri                                   | fenêtre de date          |
|----------------|-----------------------|---------------------------------------|--------------------------|
| `GET /notes`   | `limit` (déf. 10, max 50) + `offset` | AUCUN — `sort=` avalé en 200 | AUCUNE — avalée en 200   |
| `GET /tasks`   | `limit` (déf. 500, max 1000) + `offset` | `sort=created_at:asc|desc`, `completed_at:asc|desc` | AUCUNE |
| `GET /meetings`| `limit` (déf. 50, max 200) + `cursor` — **PAS d'`offset`** | `sort=start_asc|start_desc` | `ends_from` / `starts_before` |

D'où ce test : on ne juge pas un filtre à la lecture du code, on regarde ce qui
part sur le fil. Deux paramètres qui MENTAIENT y sont verrouillés :
`tasks.list(completed=)` partait en `completed=` — nom inconnu d'Attio, avalé
en silence (`is_completed=PASBOOL` → 400, `completed=PASBOOL` → 200) : le filtre
de complétion ne filtrait RIEN ; et `meetings.list(offset=)` partait en `offset=`,
que l'API ignore.
"""
from __future__ import annotations

import pytest

from oto.tools.attio import client as ac


@pytest.fixture()
def wire(monkeypatch):
    """Capture (method, endpoint, kwargs) du dernier appel HTTP."""
    seen = {}

    def fake_request(self, method, endpoint, **kwargs):
        seen.update(method=method, endpoint=endpoint, **kwargs)
        return {"data": []}

    monkeypatch.setattr(ac.AttioClient, "_request", fake_request)
    return seen


def _client():
    return ac.AttioClient(api_key="attio-test-key")


# --- notes : la seule fenêtre possible est limit + offset ----------------------

def test_notes_list_met_limit_et_offset_sur_le_fil(wire):
    """Sans ces deux paramètres, la page par défaut d'Attio (10, les plus
    anciennes) est un plafond dont l'appelant ne peut pas sortir — signal #586."""
    _client().notes.list(limit=50, offset=120)
    assert wire["params"] == {"limit": 50, "offset": 120}


def test_notes_list_sans_borne_ne_contraint_rien(wire):
    """Aucune borne fournie ⟹ aucun paramètre inventé : c'est le défaut d'Attio
    qui s'applique, et la docstring le dit. On ne pose pas de défaut maison."""
    _client().notes.list()
    assert wire["params"] == {}


def test_notes_list_refuse_un_limit_hors_plafond():
    """51 est refusé par Attio avec un « Query params validation error » opaque
    (vérifié 27/08). Le refuser ici NOMME la borne au lieu de laisser passer."""
    with pytest.raises(ValueError, match="limit"):
        _client().notes.list(limit=51)


# --- tâches : le filtre de complétion qui ne filtrait rien --------------------

def test_tasks_list_envoie_is_completed_pas_completed(wire):
    """Le nom Attio est `is_completed`. `completed` est un paramètre INCONNU,
    avalé en 200 : le filtre annoncé au tool ne filtrait rien (signal #586)."""
    _client().tasks.list(completed=False)
    assert wire["params"] == {"is_completed": False}


def test_tasks_list_met_limit_offset_et_sort_sur_le_fil(wire):
    _client().tasks.list(limit=200, offset=200, sort="created_at:desc")
    assert wire["params"] == {"limit": 200, "offset": 200, "sort": "created_at:desc"}


def test_tasks_list_refuse_un_sort_inconnu():
    """`sort=-created_at` → HTTP 400 opaque côté Attio (vérifié 27/08)."""
    with pytest.raises(ValueError, match="sort"):
        _client().tasks.list(sort="-created_at")


# --- réunions : cursor, pas offset -------------------------------------------

def test_meetings_list_pagine_au_cursor_et_borne_les_dates(wire):
    """`ends_from`/`starts_before` sont la SEULE vraie fenêtre de date de tout
    ce connecteur — les notes et les tâches n'en ont aucune."""
    _client().meetings.list(limit=200, cursor="cur-2", sort="start_desc",
                            ends_from="2026-08-26T00:00:00Z",
                            starts_before="2026-08-28T00:00:00Z")
    assert wire["params"] == {
        "limit": 200, "cursor": "cur-2", "sort": "start_desc",
        "ends_from": "2026-08-26T00:00:00Z", "starts_before": "2026-08-28T00:00:00Z",
    }


def test_meetings_list_ne_met_plus_offset_sur_le_fil(wire):
    """L'API n'a pas d'`offset` : elle l'avale (offset=2000 rend la même
    première page). Un paramètre que l'amont n'honore pas ne s'envoie pas."""
    _client().meetings.list(limit=10)
    assert "offset" not in wire["params"]
    with pytest.raises(TypeError):
        _client().meetings.list(offset=2000)


# --- transcription d'un appel : cursor (signal d'usage 1226) -------------------

_TRANSCRIPT = "meetings/m-1/call_recordings/cr-1/transcript"


def test_transcript_premiere_page_sans_curseur(wire):
    """Sans curseur, l'appel reste celui d'avant : aucun paramètre sur le fil."""
    _client().call_recordings.transcript("m-1", "cr-1")
    assert wire["method"] == "GET" and wire["endpoint"] == _TRANSCRIPT
    assert "params" not in wire


def test_transcript_pagine_au_cursor(wire):
    """La référence d'Attio pagine les segments d'une transcription au curseur
    (`cursor` en query, `pagination.next_cursor` en réponse) : sans lui, une
    longue réunion ne se lisait qu'à sa première page."""
    _client().call_recordings.transcript("m-1", "cr-1", cursor="cur-2")
    assert wire["endpoint"] == _TRANSCRIPT
    assert wire["params"] == {"cursor": "cur-2"}
