"""Répondre à un fil à PLUSIEURS destinataires.

Vécu le 19/08/2026 : relancer un fil qu'on avait soi-même envoyé à deux
personnes échouait sur « Cannot determine reply recipient ». La cause n'était
pas l'absence d'adresse mais `parseaddr`, qui n'en lit qu'UNE : sur
`To: a@x, b@y` il rend `('', '')`. Le message partait donc… nulle part.

Aucun réseau : on inspecte la résolution des destinataires.
"""
from __future__ import annotations

from oto.tools.google.gmail.lib._reply import recipients as _recipients


def test_une_seule_adresse():
    assert _recipients("Julien <j@ex.test>") == "j@ex.test"


def test_plusieurs_adresses_sont_TOUTES_gardees():
    """Le défaut : `parseaddr` rendait '' ici, et la réponse ne partait pas."""
    assert _recipients("j@ex.test, Alessandro <a@ex.test>") == "j@ex.test, a@ex.test"


def test_on_ne_se_repond_pas_a_soi_meme():
    """Répondre au message d'un tiers adressé à plusieurs : on sort de la liste."""
    entete = "moi@otomata.tech, j@ex.test, a@ex.test"
    assert _recipients(entete, exclude="MOI@otomata.tech") == "j@ex.test, a@ex.test"


def test_un_entete_vide_ne_rend_rien():
    """Le garde-fou d'appel (`if not reply_to: raise`) doit rester atteignable."""
    assert _recipients("") == ""
    assert _recipients("Nom Sans Adresse") == ""
