"""Analytique Pennylane : les chemins et les corps exacts que l'API attend.

Référence API v2 : les axes sous `/category_groups`, les catégories d'un objet
sous `/{objet}/{id}/categories`, et la catégorisation est un PUT dont le corps
est une LISTE `[{id, weight}]` qui remplace tout, le poids en chaîne.
"""
import pytest

from oto.tools.pennylane.client import PennylaneClient


def _client():
    """Un client dont le transport est remplacé par un mouchard."""
    c = PennylaneClient(api_key="k", rate_limit_delay=0)
    c.appels = []

    def _appel(methode, endpoint, *, params=None, data=None, retries=3):
        c.appels.append((methode, endpoint, data))
        return [{"id": 5, "weight": "1"}]
    c._appel = _appel

    def _pages(endpoint, params=None, max_pages=None, per_page=100):
        c.appels.append(("PAGES", endpoint, max_pages))
        return [{"id": 5}]
    c.fetch_all_pages = _pages
    return c


def test_axes_et_categories_suivent_toutes_les_pages():
    c = _client()
    c.list_category_groups()
    c.get_categories()
    c.get_categories(category_group_id=3)
    assert c.appels == [("PAGES", "category_groups", None),
                        ("PAGES", "categories", None),
                        ("PAGES", "category_groups/3/categories", None)]


@pytest.mark.parametrize("cible, chemin", [
    ("supplier_invoice", "supplier_invoices/42/categories"),
    ("customer_invoice", "customer_invoices/42/categories"),
    ("ledger_entry_line", "ledger_entry_lines/42/categories"),
])
def test_lire_puis_poser_les_categories_d_un_objet(cible, chemin):
    c = _client()
    c.get_categories_of(cible, 42)
    c.categorize(cible, 42, [{"id": 59, "weight": "0.5"}, {"id": 33, "weight": 0.5},
                             {"id": 65, "weight": 1}])
    assert c.appels[0] == ("PAGES", chemin, None)
    assert c.appels[1] == ("PUT", chemin, [{"id": 59, "weight": "0.5"},
                                          {"id": 33, "weight": "0.5"},
                                          {"id": 65, "weight": "1"}])


def test_une_liste_vide_retire_toutes_les_categories():
    c = _client()
    c.categorize("supplier_invoice", 42, [])
    assert c.appels == [("PUT", "supplier_invoices/42/categories", [])]


@pytest.mark.parametrize("cible, categories, motif", [
    ("bank_account", [], "carries no categories"),
    ("supplier_invoice", {"id": 1, "weight": "1"}, "list"),
    ("supplier_invoice", [{"id": 1}], "`id` and `weight`"),
    ("supplier_invoice", [{"weight": "1"}], "`id` and `weight`"),
    ("supplier_invoice", [{"id": 1, "weight": "1", "label": "x"}], "unknown"),
    ("supplier_invoice", [{"id": 1, "weight": "1.5"}], "outside"),
    ("supplier_invoice", [{"id": 1, "weight": True}], "number"),
    ("supplier_invoice", [{"id": 1, "weight": "moitié"}], "number"),
])
def test_un_corps_mal_forme_est_refuse_avant_l_appel(cible, categories, motif):
    c = _client()
    with pytest.raises(ValueError, match=motif):
        c.categorize(cible, 42, categories)
    assert c.appels == []
