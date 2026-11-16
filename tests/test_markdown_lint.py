"""A list glued to the preceding paragraph: detected, and fixed for the Gmail body."""

import warnings

from oto.tools.markdown_lint import find_glued_lists, fix_glued_lists, warn_markdown
from oto.tools.google.gmail.lib.gmail_client import _markdown_to_html_fragment

GLUED = "Le montage est le suivant :\n- point 1\n- point 2"


def test_liste_collee_reperee_a_sa_premiere_ligne():
    assert find_glued_lists(GLUED) == [2]


def test_la_correction_insere_une_seule_ligne_vide():
    assert fix_glued_lists(GLUED) == "Le montage est le suivant :\n\n- point 1\n- point 2"


def test_un_texte_sans_liste_collee_revient_identique():
    for text in ("Bonjour,\n\n- a\n- b\n", "Une phrase.\nUne autre.", "", "- a\n- b"):
        assert fix_glued_lists(text) == text


def test_liste_numerotee_collee():
    assert fix_glued_lists("Étapes :\n1. ouvrir\n2. fermer") == "Étapes :\n\n1. ouvrir\n2. fermer"


def test_bloc_de_code_inchange():
    text = "Exemple :\n\n```\nvaleur :\n- pas une liste\n```\nFin."
    assert find_glued_lists(text) == []
    assert fix_glued_lists(text) == text


def test_liste_apres_un_bloc_de_code_reperee_hors_du_bloc():
    text = "```\ncode\n```\nPuis :\n- a"
    assert find_glued_lists(text) == [5]


def test_le_corps_gmail_rend_une_vraie_liste():
    html = _markdown_to_html_fragment(GLUED)
    assert "<ul>" in html and "<li>point 1</li>" in html
    assert "suivant : - point" not in html


def test_le_corps_gmail_ne_previent_plus_puisqu_il_corrige():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _markdown_to_html_fragment(GLUED)


def test_l_avertissement_reste_pour_les_autres_rendus():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert warn_markdown(GLUED, source="doc")
    assert w and "line(s) 2" in str(w[0].message)
