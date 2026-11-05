Généré par la fabrique depuis `connectors/*/connector.yaml`. Ne pas modifier : lancer `python -m fabrique`.

# La carte du parc

Une ligne par connecteur décrit, par ordre alphabétique. Le type se déduit de la description, première règle qui s'applique :

1. `auth.kind: oauth2_user` : connexion d'une personne (OAuth) ;
2. `auth.kind: oauth2_client_credentials` : application du client (OAuth entre machines) ;
3. un champ du `credential` en `holder: host` : clé de l'hôte ;
4. `auth.kind: none` : sans clé ;
5. sinon (`api_key`, `bearer`, `basic`) : clé.

« Clé de la plateforme » : `modes` contient `platform`. Fonctions : par `class` (lecture, écriture, sensible). Sonde : `probe`, et son solde `probe.balance`. Avec coût : les fonctions qui portent `cost`. Niveaux d'accès : `auth.access_levels`. Non générées : les fonctions que la fabrique saute, nommées par `python -m fabrique`.

| connecteur | libellé | version | type | clé de la plateforme | fonctions | lecture | écriture | sensible | sonde | avec coût | niveaux d'accès | non générées |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: |
| `affinity` | Affinity | 1.0.0 | clé | non | 33 | 24 | 7 | 2 | non | 0 | non | 0 |
| `aircall` | Aircall | 1.1.0 | clé | non | 20 | 20 | 0 | 0 | non | 20 | non | 0 |
| `allo` | Allo | 1.2.0 | clé | non | 95 | 40 | 41 | 14 | oui | 1 | non | 0 |
| `amplemarket` | Amplemarket | 1.0.0 | clé | non | 48 | 28 | 15 | 5 | oui | 48 | non | 0 |
| `amplitude` | Amplitude | 1.2.0 | clé | non | 16 | 16 | 0 | 0 | non | 0 | non | 0 |
| `apollo` | Apollo.io | 1.1.1 | clé | non | 30 | 16 | 10 | 4 | non | 30 | non | 0 |
| `attio` | Attio | 1.2.0 | clé | non | 47 | 28 | 11 | 8 | oui | 47 | non | 0 |
| `brevo` | Brevo | 1.0.0 | clé | non | 60 | 36 | 16 | 8 | oui | 60 | non | 0 |
| `claap` | Claap | 1.0.1 | clé | non | 6 | 5 | 0 | 1 | non | 6 | non | 0 |
| `crisp` | Crisp | 1.0.0 | clé | non | 20 | 20 | 0 | 0 | oui | 20 | non | 0 |
| `dropcontact` | Dropcontact | 1.0.0 | clé | oui | 3 | 2 | 1 | 0 | oui, avec solde | 1 | non | 0 |
| `elevenlabs` | ElevenLabs | 1.0.1 | clé | non | 4 | 4 | 0 | 0 | oui | 4 | non | 0 |
| `fod` | France Open Data | 1.5.0 | clé | oui | 31 | 31 | 0 | 0 | oui | 31 | non | 0 |
| `folk` | folk | 1.0.1 | clé | non | 62 | 23 | 21 | 18 | oui | 62 | non | 0 |
| `fullenrich` | FullEnrich | 1.0.0 | clé | oui | 2 | 1 | 1 | 0 | non | 1 | non | 0 |
| `gmail` | Gmail | 1.0.0 | connexion d'une personne (OAuth) | non | 13 | 8 | 2 | 3 | non | 13 | oui | 0 |
| `google_calendar` | Google Calendar | 1.0.0 | connexion d'une personne (OAuth) | non | 7 | 4 | 0 | 3 | non | 7 | oui | 0 |
| `google_drive` | Google Drive | 1.0.0 | connexion d'une personne (OAuth) | non | 12 | 5 | 4 | 3 | non | 12 | oui | 0 |
| `google_sheets` | Google Sheets | 1.0.0 | connexion d'une personne (OAuth) | non | 5 | 2 | 2 | 1 | non | 5 | oui | 0 |
| `granola` | Granola | 1.0.0 | clé | non | 8 | 5 | 2 | 1 | oui | 8 | non | 0 |
| `higgsfield` | Higgsfield | 1.0.0 | clé | non | 5 | 2 | 2 | 1 | oui | 5 | non | 0 |
| `hubspot` | HubSpot | 1.0.2 | clé | non | 25 | 13 | 7 | 5 | non | 25 | non | 0 |
| `hunter` | Hunter.io | 1.0.0 | clé | oui | 4 | 1 | 3 | 0 | oui, avec solde | 3 | non | 0 |
| `jefacture` | jefacture.com | 1.0.0 | connexion d'une personne (OAuth) | non | 11 | 11 | 0 | 0 | non | 11 | non | 0 |
| `klaviyo` | Klaviyo | 1.0.0 | clé | non | 25 | 19 | 2 | 4 | oui | 25 | non | 0 |
| `lemlist` | lemlist | 1.2.2 | clé | non | 125 | 57 | 32 | 36 | oui | 125 | non | 0 |
| `linear` | Linear | 1.0.0 | clé | non | 27 | 12 | 9 | 6 | oui | 0 | non | 0 |
| `linkedin_unipile` | LinkedIn (Unipile) | 1.3.0 | clé de l'hôte | oui | 28 | 24 | 1 | 3 | oui | 0 | non | 0 |
| `mailpool` | Mailpool | 1.0.1 | clé | non | 21 | 16 | 5 | 0 | non | 21 | non | 0 |
| `malibou` | Malibou | 1.0.0 | clé | non | 4 | 4 | 0 | 0 | oui | 4 | non | 0 |
| `meta_ads` | Meta Ads | 1.1.0 | clé | non | 8 | 8 | 0 | 0 | non | 0 | non | 0 |
| `microsoft` | Microsoft 365 (SharePoint, OneDrive) | 3.2.0 | connexion d'une personne (OAuth) | non | 13 | 13 | 0 | 0 | non | 13 | oui | 0 |
| `microsoft_app` | Microsoft 365 mail (application access) | 1.0.0 | application du client (OAuth entre machines) | non | 6 | 4 | 1 | 1 | non | 0 | non | 0 |
| `mistral` | Mistral AI | 1.0.1 | clé de l'hôte | oui | 1 | 1 | 0 | 0 | non | 1 | non | 0 |
| `modjo` | Modjo | 1.0.0 | clé | non | 29 | 26 | 2 | 1 | oui | 29 | non | 0 |
| `mux` | Mux | 1.0.0 | clé | non | 5 | 3 | 2 | 0 | oui | 5 | non | 0 |
| `nextmotion` | Nextmotion | 1.1.0 | clé | non | 140 | 69 | 44 | 27 | non | 0 | non | 0 |
| `notion` | Notion | 1.1.0 | clé | non | 22 | 9 | 10 | 3 | non | 22 | non | 0 |
| `origami` | Origami | 1.0.0 | clé | non | 19 | 12 | 2 | 5 | oui | 19 | non | 0 |
| `pappers` | Pappers | 1.0.0 | clé | non | 4 | 4 | 0 | 0 | oui | 0 | non | 0 |
| `pennylane` | Pennylane | 2.1.0 | clé | non | 52 | 30 | 13 | 9 | oui | 52 | non | 0 |
| `pennylane_firm` | Pennylane (firm) | 1.1.1 | clé | non | 8 | 6 | 1 | 1 | oui | 8 | non | 0 |
| `pipecorn` | Pipecorn | 1.0.0 | clé | oui | 8 | 3 | 5 | 0 | oui, avec solde | 5 | non | 0 |
| `qonto` | Qonto | 1.0.0 | clé | non | 20 | 20 | 0 | 0 | oui | 20 | non | 0 |
| `salesforce` | Salesforce | 1.0.0 | connexion d'une personne (OAuth) | non | 6 | 5 | 0 | 1 | non | 6 | non | 0 |
| `sellsy` | Sellsy | 1.0.1 | application du client (OAuth entre machines) | non | 2 | 2 | 0 | 0 | non | 2 | non | 0 |
| `serper` | Serper | 1.1.0 | clé | oui | 12 | 12 | 0 | 0 | non | 12 | non | 0 |
| `sivan` | Sivan | 1.1.0 | clé | non | 7 | 7 | 0 | 0 | oui | 7 | non | 0 |
| `slack` | Slack | 1.1.0 | clé | non | 16 | 9 | 3 | 4 | oui | 16 | non | 0 |
| `slack_user` | Slack (personal token) | 1.1.0 | clé | non | 10 | 5 | 2 | 3 | non | 10 | non | 0 |
| `threecx` | 3CX | 1.1.0 | application du client (OAuth entre machines) | non | 2 | 2 | 0 | 0 | non | 2 | non | 0 |
| `typeform` | Typeform | 1.2.0 | clé | non | 12 | 6 | 1 | 5 | non | 12 | non | 0 |
| `wttj_ats` | Welcome to the Jungle ATS | 1.0.0 | clé | non | 10 | 7 | 3 | 0 | non | 0 | non | 0 |
| `yousign` | Yousign | 1.1.1 | clé | non | 13 | 5 | 7 | 1 | oui | 0 | non | 0 |
| **total : 54** |  |  |  |  | 1222 | 745 | 290 | 187 |  | 836 |  | 0 |

## Par type

| type | connecteurs | fonctions |
| --- | ---: | ---: |
| connexion d'une personne (OAuth) | 7 | 67 |
| application du client (OAuth entre machines) | 3 | 10 |
| clé de l'hôte | 2 | 29 |
| sans clé | 0 | 0 |
| clé | 42 | 1116 |
