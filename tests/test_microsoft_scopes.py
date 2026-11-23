"""Scopes Microsoft — la lecture du `scope` rendu par Entra, pour qu'un consommateur
teste `short(FILES) <= normalize(grant.scope)` quelle que soit la forme rendue."""
from oto.tools.microsoft import scopes


def test_formes_courtes_uri_et_casse_confondues():
    granted = scopes.normalize(
        "openid profile email offline_access https://graph.microsoft.com/files.readwrite.all "
        "SITES.READWRITE.ALL User.Read  HTTPS://GRAPH.MICROSOFT.COM/Mail.Send")
    assert granted == {"openid", "profile", "email", "offline_access", "Files.ReadWrite.All",
                       "Sites.ReadWrite.All", "User.Read", "Mail.Send"}
    assert scopes.short(scopes.FILES) <= granted
    assert scopes.short(scopes.IDENTITY) <= granted
    assert not scopes.short(scopes.MAIL) <= granted


def test_short_de_nos_constantes():
    assert scopes.short(scopes.TEAMS) == {"Team.ReadBasic.All", "Channel.ReadBasic.All",
                                          "ChannelMessage.Send", "Chat.ReadWrite"}
    assert scopes.short(scopes.REFRESH) == {".default", "offline_access"}
    assert scopes.short(scopes.NOTES) == {"Notes.ReadWrite.All"}
    assert scopes.short(scopes.NOTES) <= scopes.normalize("notes.readwrite.all User.Read")


def test_scope_inconnu_garde_tel_que_rendu_sans_prefixe():
    assert scopes.normalize("https://graph.microsoft.com/Tasks.ReadWrite") == {"Tasks.ReadWrite"}


def test_scope_vide():
    assert scopes.normalize("") == frozenset() and scopes.normalize(None) == frozenset()


def test_admin_a_part():
    every_user = (scopes.IDENTITY + scopes.FILES + scopes.MAIL + scopes.CALENDAR
                  + scopes.TEAMS + scopes.NOTES)
    assert not set(scopes.TEAMS_ADMIN) & set(every_user)
    assert not set(scopes.DIRECTORY) & set(every_user)
    # L'écriture de l'annuaire est bornée : licences, compte activé, membres — jamais
    # créer ou supprimer un compte, ni un mot de passe.
    ecritures = {s.removeprefix(scopes.GRAPH) for s in scopes.DIRECTORY if "Write" in s
                 or "EnableDisable" in s}
    assert ecritures == {"LicenseAssignment.ReadWrite.All", "User.EnableDisableAccount.All",
                         "GroupMember.ReadWrite.All"}


def test_boites_partagees_a_part_du_courrier():
    assert scopes.short(scopes.MAIL_SHARED) == {"Mail.Read.Shared"}
    assert not set(scopes.MAIL_SHARED) & set(scopes.MAIL)
    assert scopes.short(scopes.MAIL_SHARED) <= scopes.normalize("mail.read.shared User.Read")
