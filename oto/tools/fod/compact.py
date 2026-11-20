"""Compactage des réponses entreprise et ICPE, re-exported from the shared france-opendata lib (single source).

Recherche compactée, profil d'un SIREN et lot de profils, fiche ICPE réduite : la lecture qu'en fait un agent. Les
fonctions qui interrogent prennent les clients en paramètre (clients france-opendata ou proxy HTTP vers FOD, de même
surface) ; la lib n'en ouvre aucun.
"""
from france_opendata.compact import (
    BILAN_OUTLINE_KEYS,
    COMPLEMENT_KEEP,
    DIRIGEANT_KEEP,
    DIRIGEANTS_MAX,
    ETABLISSEMENT_KEEP,
    ETABLISSEMENTS_MAX,
    EVENEMENT_KEEP,
    FILTRE_CA_AVERTISSEMENT,
    ICPE_INSPECTIONS_MAX,
    ICPE_KEEP,
    ICPE_MAX_RUBRIQUES,
    IDENTITY_KEEP,
    LATEST_BILAN_KEYS,
    LEGAL_FORM_CODES,
    PROFILS_LOT_MAX,
    RUBRIQUES_ENERGIE,
    cloture_liquidation,
    compact_icpe,
    compact_identity,
    compact_rubriques,
    pick,
    profil_entreprise,
    profils_entreprises,
    rechercher,
    split_legal_form,
)
from france_opendata.erreurs import ParametreInvalide

__all__ = [
    "BILAN_OUTLINE_KEYS", "COMPLEMENT_KEEP", "DIRIGEANT_KEEP", "DIRIGEANTS_MAX", "ETABLISSEMENT_KEEP",
    "ETABLISSEMENTS_MAX", "EVENEMENT_KEEP", "FILTRE_CA_AVERTISSEMENT", "ICPE_INSPECTIONS_MAX", "ICPE_KEEP",
    "ICPE_MAX_RUBRIQUES", "IDENTITY_KEEP", "LATEST_BILAN_KEYS", "LEGAL_FORM_CODES", "PROFILS_LOT_MAX",
    "ParametreInvalide", "RUBRIQUES_ENERGIE", "cloture_liquidation", "compact_icpe", "compact_identity",
    "compact_rubriques", "pick", "profil_entreprise", "profils_entreprises", "rechercher", "split_legal_form",
]
