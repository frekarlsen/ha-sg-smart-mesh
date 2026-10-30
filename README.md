# SG Smart 3.0 for Home Assistant – Bluetooth Mesh uten gateway

[![Release](https://img.shields.io/github/v/release/frekarlsen/ha-sg-smart-mesh)](https://github.com/frekarlsen/ha-sg-smart-mesh/releases)
[![Tests](https://github.com/frekarlsen/ha-sg-smart-mesh/actions/workflows/tests.yml/badge.svg)](https://github.com/frekarlsen/ha-sg-smart-mesh/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/frekarlsen/ha-sg-smart-mesh)](LICENSE)

Styr **SG Armaturen «SG Smart 3.0»**-belysning, som LEDDim Smart Pill 3.0-dimmere og SG Smart Wireless
3.0-dimmehjul, direkte fra Home Assistant over Bluetooth du allerede har. Du trenger verken SG-gateway,
SG-appen eller nRF Mesh.

Dette er en fork av [dasimon135/ha-bluetooth-mesh](https://github.com/dasimon135/ha-bluetooth-mesh), en
Bluetooth SIG Mesh-stakk i ren Python med en Home Assistant-integrasjon, opprinnelig laget for Häfele
Connect Mesh. Alt den gjør, virker fortsatt. Forken legger til SG Smart 3.0 sin egen protokoll og alt
som trengs for å sette opp og styre enhetene fra Home Assistant alene.

> **Uoffisiell.** Ikke tilknyttet eller godkjent av SG Armaturen AS. SG-protokollen er kartlagt for å
> få enhetene til å virke sammen med Home Assistant. Ingen SG-kode er med.

Har du førstegenerasjons SG Smart (CSRmesh)? Den bruker en annen radioprotokoll. Se søsterprosjektet
[ha-sg-csrmesh](https://github.com/frekarlsen/ha-sg-csrmesh).

## SG Smart 3.0: dette får du

| | |
| --- | --- |
| **Dimmere** (Smart Pill 3.0) | En `light` med av/på og lysstyrke. Tilstanden leses tilbake fra dimmeren, så en endring fra veggbryteren eller et dimmehjul vises i HA. |
| **Dimmerinnstillinger** | Under *Konfigurasjon* på enhetssiden. De lagres i selve dimmeren, så de gjelder også for dimmehjul og veggbryter: minimumsnivå, oppstartsnivå (et kort spark for en kald lampe) og maksnivå, lest fra dimmeren, samt trykkbryterinngang (vanlig trykkbryter eller sensor) og innfadingstid. Hvilke innstillinger en enhet får, følger produktet, som i SG-appen. |
| **Minimumsnivå (Home Assistant)** | Et ekstra minimum som HA legger på, for laster som forblir mørke over de 40 % dimmerens eget minimum tillater. HAs 1–100 % fordeles da over området der lampen faktisk lyser. |
| **Dimmehjul og brytere** | En `event`-entitet (`press`, `hold`, `rotate_up`, `rotate_down` med `steps`) til automasjoner, og en sensor for **batterispenning**. |
| **Direkte paring** | Et dimmehjul paret med en dimmer styrer den **direkte** over meshen, uten gateway og uten Home Assistant i mellom. Det virker derfor også når HA er nede. HA ser fortsatt begge sider. |
| **Enhetsadministrasjon** | Legg til (provisjonering), par / fjern paring, fjern (fabrikkreset over meshen) og ta sikkerhetskopi, alt fra integrasjonens **Konfigurer**-meny. |

### Dette trenger du

- Home Assistant 2025.8 eller nyere.
- En **ESPHome Bluetooth-proxy** (en ESP32 holder) eller en Bluetooth-adapter Home Assistant kan bruke,
  innen rekkevidde av minst én SG-enhet med fast strøm.
- SG Smart **3.0**-enheter. Den eldre CSRmesh-generasjonen støttes av
  [ha-sg-csrmesh](https://github.com/frekarlsen/ha-sg-csrmesh).

### Installasjon

1. HACS → Integrasjoner → ⋮ → **Egendefinerte repositorier** → legg til
   `https://github.com/frekarlsen/ha-sg-smart-mesh`, kategori *Integrasjon*. Installer
   **SG Smart 3.0 (Bluetooth Mesh)** og start Home Assistant på nytt.
2. Legg til integrasjonen (Innstillinger → Enheter og tjenester → Legg til integrasjon →
   *Bluetooth Mesh*) og lim inn en eksport av mesh-nettverket (Mesh Configuration Database JSON,
   for eksempel eksportert fra nRF Mesh). Den inneholder nettverksnøklene dine, så hold den privat.
3. Deretter gjøres alt under **Innstillinger → Enheter og tjenester → Bluetooth Mesh → Konfigurer**.

> Integrasjonsdomenet er fortsatt `bluetooth_mesh`. Forken erstatter derfor den opprinnelige
> integrasjonen i stedet for å kjøre ved siden av den. Installer den ene eller den andre.

### Konfigurer-menyen

- **Legg til en enhet:** viser fabrikkresatte enheter Home Assistant hører, de sist hørte først. Velg én,
  gi den et navn, og fortsett å trykke på enheten mens den settes opp (dimmehjulet, eller dimmerens
  strøm- / R-knapp). HA provisjonerer den, gir den applikasjonsnøkkelen, binder modellene og setter opp
  hvor den skal publisere: en dimmer rapporterer til HA, et dimmehjul publiserer til alle noder så parede
  dimmere kan reagere. En batterienhet melder seg bare et øyeblikk etter at den er vekket. Vekk den, og
  velg så *Søk igjen*.
- **Par et dimmehjul / en bryter med en dimmer:** velg begge ved navn, og hva et kort trykk, en vridning
  og et hold skal gjøre. Dimmeren lagrer paringen. Ett dimmehjul kan styre flere dimmere: par det med hver
  av dem. Dialogen viser hva som allerede er paret, og enhetssidene viser det også: *Styres av* på
  dimmeren, *Styrer* på dimmehjulet. Dette er Home Assistants oversikt over paringene den har sendt.
  Dimmerne svarer ikke når man spør om tabellen deres, så en paring gjort i SG-appen vises ikke.
- **Fjern en enhet:** fabrikkresetter enheten over meshen (Config Node Reset, som når du fjerner den i
  SG-appen), fjerner først paringen fra alle dimmere hvis det er et dimmehjul, og fjerner den fra HA.
  Adressen brukes aldri om igjen, fordi de andre nodene husker sekvensnumrene og ellers ville avvist en
  ny enhet som en gjentakelse.
- **Ta sikkerhetskopi av nettverket:** skriver nettverket (nøkler, noder, enhetsnøkler) til
  `/config/bluetooth_mesh_backup_<navn>.json`. **Ta vare på en kopi et annet sted:** uten den må alle
  enheter fabrikkresettes hvis denne Home Assistant-installasjonen går tapt. Den kan importeres igjen med
  *Rekonfigurer*.
- **Tilkoblingsinnstillinger:** de opprinnelige innstillingene for keep-alive og avsenderadresse.

### Handlinger (for automasjoner og skript)

`bluetooth_mesh.pair_switch`, `unpair_switch`, `set_min_level`, samt `configure_node`, `get_composition`
og `send_raw` for å fullføre en halvveis lagt til enhet eller for videre kartlegging.

### Kjente begrensninger

- Dimmerens eget minimum går opp til 40 % (som i SG-appen). En last som trenger mer, beholder Home
  Assistant-minimumet i tillegg. Det ser ikke dimmehjulet, og under det viser HA 1 %.
- Trykkbryterinngang og innfading kan ikke leses tilbake fra dimmeren, så HA viser verdien den sist
  skrev (ukjent fram til da).
- Testet på LEDDim Smart Pill Micro 3.0 (produkt `0x00A2`) og SG Smart Wireless 3.0-dimmehjul. Andre
  SG Smart 3.0-enheter (tunable white, RGBW, DIN, drivere) bruker samme protokoll, men er ikke prøvd.
  Tilbakemeldinger er velkomne.
- Scener lagret i SG-enhetene vises ikke. Bruk scener i Home Assistant.

### Slik virker det

SG Smart 3.0-enheter er vanlige Bluetooth SIG Mesh-noder (Telink-stakk, firma-ID `0x0EE8`) som gjør nesten
alt gjennom én egen leverandørmodell. Protokollen (av/på og nivå, status, bryterparing, knappehendelser,
enhetsinnstillinger, produkt-ID-er) er dokumentert i
[docs/sg-smart-3-protocol.md](docs/sg-smart-3-protocol.md). Hvordan den ble kartlagt (SG Smart-appen
dekompilert med blutter, hvor hver kommando ligger i den, og hver maskinvaretest, slik at arbeidet kan
gjentas på en nyere app eller fastvare) står i
[docs/sg-smart-3-reverse-engineering.md](docs/sg-smart-3-reverse-engineering.md), med den første analysen
av appen i [docs/sg-smart-app-report.nb.md](docs/sg-smart-app-report.nb.md). Ingen av dem inneholder
SG-kode eller binærfiler.

En viktig rettelse underveis, som også gjelder det opprinnelige prosjektet: konfigurasjonsmeldinger til
proxyen (proxyfilteret) må krypteres med **proxy-nonce** (Mesh Profile §3.8.5.4), ikke nettverks-nonce.
Häfele-lamper tåler feil nonce. SG-noder avviser den uten å si fra, og sender da ingenting tilbake.

---

*Resten av denne README-en er dokumentasjonen fra det opprinnelige prosjektet for den generelle
Bluetooth Mesh-integrasjonen, som forken beholder.*

## Opprinnelig prosjekt: virker dette for meg?

**Lampene dine.** Bluetooth Mesh-armaturer: Häfele Connect Mesh og Loox, ThingOS-baserte lamper solgt
under andre navn, og vanlige SIG Mesh-noder. Hvis lampen ble satt opp fra en mobilapp over Bluetooth og
det ikke fulgte med noen hub, er den trolig en av disse.

**Dette trenger du.** En ESPHome Bluetooth-proxy innen rekkevidde av lampene, eller en Bluetooth-adapter
på Home Assistant-maskinen, det du allerede har. Ingenting annet å kjøpe.

**Farger støttes ikke ennå.** Av/på, lysstyrke og varm til kald hvit virker. Fullfarge RGB-lamper gjør
det ikke: forfatterens egne lamper er tunable white, og i stedet for å levere farger uten testing er de
utelatt. Har du fargelamper og vil hjelpe, er det akkurat det som mangler.

**Du kan fortsette å bruke leverandørens app.** Integrasjonen blir med i det eksisterende meshet ditt i
stedet for å erstatte det, så appen og Home Assistant kan brukes side om side. Se
[Sameksistens med leverandørens app](#sameksistens-med-leverandørens-app-delte-nøkler).

**Det Home Assistant viser, er det lampen sier.** Lysstyrke og fargetemperatur leses tilbake fra selve
lampen, ikke antatt ut fra siste kommando. En endring fra appen vises derfor her.

## Hva den styrer

Hver lysutgang blir én `light`-entitet i Home Assistant, med egenskaper lest fra mesh-sammensetningen:

| Nodemodell | Egenskap i HA |
| --- | --- |
| Generic OnOff (`0x1000`) | av / på |
| Light Lightness (`0x1300`) | + lysstyrke |
| Light CTL (`0x1303` / `0x1306`) | + fargetemperatur (tunable white) |

En lampe er én utgang, og dermed én entitet. En **flerkanals kontroller** er én mesh-node der kanalene er
elementene, hver med sin egen dimmer. Hver av dem blir en egen entitet, navngitt etter utgangen slik
leverandørappen navngir den, samlet under én enhet. En lampe som bare sprer modellene sine over flere
elementer, forblir én entitet.

**Ikke støttet ennå:** RGB- og fullfargelamper (Light HSL / xyL). Forfatterens maskinvare er bare tunable
white, så farger er utelatt i stedet for levert uten testing. Bidrag fra noen med fargelamper å teste
mot er velkomne.

### Tilstanden leses, den antas ikke

En Bluetooth Mesh-proxy videresender *ingenting* inn på en ny tilkobling før klienten har satt opp
adressefilteret sitt. Derfor så de første versjonene aldri et eneste svar og kunne bare vise det de sist
hadde sendt. Integrasjonen setter opp filteret, så:

* av/på og lysstyrke **leses fra lampen** når meshet blir tilgjengelig, og igjen etter hver ny
  tilkobling, også endringer gjort fra leverandørappen mens Home Assistant var borte;
* en lampe som ikke er lest ennå, viser `unknown` i stedet for å gjette `off`. En oppdiktet tilstand kan
  en annen integrasjon handle på og gjøre sann;
* fargetemperatur leses også tilbake, og lampen spørres én gang om Kelvin-området den faktisk følger. Da
  viser glidebryteren lampens egne ytterpunkter i stedet for en antatt 2700–6500;
* en kommando vises med en gang, og ender så på det lampen **svarte**. En lampe som ikke svarer, lar
  entiteten stå som den var, og loggen sier fra én gang per tilfelle. En node som svarer på lesing men
  dropper skriving, er ellers usynlig fra dashbordet, som ville fortsatt å vise tilstanden det ba om.

Noen lamper har fargetemperaturen snudd: de lyser varmt hvitt når Home Assistant sier kaldt. Huk av for
dem under **Lamper med invertert fargetemperatur** (Innstillinger → Enheter og tjenester →
*Bluetooth Mesh* → **Konfigurer**), så speiles ønsket temperatur før den sendes. La de andre være:
speiler du en lampe som allerede er riktig, byttes varmt og kaldt helt om. Det er en innstilling per
lampe og ikke per produsent, fordi feilen varierer mellom modeller og fastvare fra samme merke.

Speilingen gjelder når en kommando sendes, så en endring virker fra neste kommando: å huke av eller på
fargelegger ikke om en lampe som allerede lyser. Den speiler rundt området lampen oppgir, så en lampe med
andre grenser enn 2700–6500 speiles rundt sitt eget midtpunkt.

Å legge til en node, eller fornye en nøkkel, gjøres med **Rekonfigurer** på den eksisterende oppføringen:
lim inn den nye eksporten, og alle entitets-ID-er og historikk beholdes. Virker noe ikke, rapporterer
**Last ned diagnostikk** på integrasjonen hvilken Network ID den ser etter, alle mesh-proxyer Home
Assistant ser nå, IV Index og sekvensteller i bruk, unicast-adressen kommandoer sendes fra, hvilken
applikasjonsnøkkel de krypteres med ved siden av dem hver modell er bundet til, og hver nodes
sammensetning. Den inneholder ingen nøkler, så den er trygg å lime inn i en sak. Den siste gruppen er
viktig fordi en feil der ikke synes i lufta: en node forkaster en melding den ikke kan autentisere, uten å
svare, så integrasjonen sender helt riktig og lampen gjør ingenting.

Diagnostikken **tester også hver node med enhetsnøkkelen** (en `Config Relay Get`, der både spørsmål og
svar får plass i én usegmentert melding). Feltet `answered` er det ærlige signalet på om noden nås: det
krever ingen applikasjonsnøkkel, ingen modellbinding og ingen leverandørstøtte, så det skiller *meldingen
kom aldri fram* fra *noden fikk den og gjorde ingenting*, som er det aller vanskeligste å skille i et mesh.
Blokken `relay` ved siden av viser om noden videresender noe fra proxytilkoblingen ut i resten av
nettverket.

## To leveranser

Repoet inneholder to ting, etter samme mønster som `pymadoka-ng` / `daikin_madoka`:

1. **`btmesh`** (`src/btmesh/`): et Python-bibliotek uavhengig av Home Assistant som implementerer
   mesh-stakken: krypto (k1–k4-avledninger, AES-CMAC, AES-CCM, nettverksobfuskering),
   nettverks-, transport- og aksesslag, segmentering og sammensetting av proxy-PDU-er, en provisjonerer,
   og en GATT-bærer over `bleak` / `habluetooth` (så den virker gjennom ESPHome Bluetooth-proxyer).
2. **`bluetooth_mesh`** (`custom_components/bluetooth_mesh/`): en HACS-integrasjon med oppsettsdialog,
   lagring, en tilkoblingskoordinator og Home Assistant-entiteter (først `light`).

## Slik henger det sammen

```
Home Assistant (mesh-stakk i ren Python: btmesh)
  │  bleak / habluetooth
  ▼
ESPHome BLE-proxy (de du allerede har)      ← eller en lokal Bluetooth-adapter
  │  GATT: Mesh Proxy Service (proxyprotokoll-tunnel)
  ▼
En mesh-node med GATT Proxy aktivert (en hvilken som helst lampe med strøm)
  │  annonseringsbærer (mesh-videresending)
  ▼
Resten av mesh-nettverket
```

Én GATT-tilkobling betjener hele nettverket: stakken holder én tunnel til én proxynode, og meshet
videresender meldingene til alle de andre nodene. Ingen BlueZ meshd og ingen egen ESP32-fastvare. Det
virker på Home Assistant OS i en VM uten egen radio.

## Sameksistens med leverandørens app (delte nøkler)

I stedet for å etterligne leverandørens egen aktiveringsprosess **bruker prosjektet mesh-nettverket
leverandørappen allerede har laget**. Appen eksporterer nettverket som en `.connect`-JSON (ThingOS-format)
med NetKey, AppKey og hver nodes unicast-adresse. Integrasjonen importerer filen, kobler proxyen sin til
samme nettverk og sender **standard** mesh-meldinger med applikasjonsnøkkelen (Generic OnOff Set, Light
Lightness Set, …) til hver nodes unicast-adresse.

Fordi begge bruker de samme nøklene, kan leverandørappen og Home Assistant styre **de samme lampene**.

En mesh-node har **én** plass for GATT-proxytilkobling, så Home Assistant og appen kan ikke ha den
samtidig. Integrasjonen holder proxytilkoblingen åpen som standard (så kommandoer går umiddelbart), og
da kan ikke leverandørappen koble til mens HA kjører. Vil du fortsatt bruke appen, kan du sette en
**keep-alive-tidsavbrudd** (Innstillinger → Enheter og tjenester → *Bluetooth Mesh* → **Konfigurer**):
etter så mange sekunder uten aktivitet slipper HA lampen så appen kan ta over, mot at HAs neste kommando
må koble til på nytt i noen sekunder. `0` = alltid tilkoblet.

## Installasjon (opprinnelig prosjekt)

1. **Legg til repoet i HACS** som egendefinert repositorium (HACS → Integrasjoner → ⋯ → Egendefinerte
   repositorier), kategori **Integrasjon**, installer *Bluetooth Mesh* og start Home Assistant på nytt.
2. **Eksporter nettverket** fra leverandørappen som en `.connect`-fil.
3. **Legg til integrasjonen** (Innstillinger → Enheter og tjenester → Legg til integrasjon →
   *Bluetooth Mesh*) og lim inn innholdet i `.connect`-filen når du blir spurt. Hold filen privat: den
   inneholder nøklene til mesh-nettverket og skal aldri legges i et repo.
4. *(Valgfritt)* **Konfigurer** keep-alive-tidsavbruddet hvis du vil fortsette å bruke leverandørappen.
   Se [Sameksistens](#sameksistens-med-leverandørens-app-delte-nøkler).

Du trenger en Bluetooth-transport: en ESPHome Bluetooth-proxy i nettverket, eller en lokal
Bluetooth-adapter Home Assistant kan bruke. Minst én mesh-lampe må ha strøm og være innen rekkevidde av
proxyen for at Home Assistant skal nå nettverket.

## Utvikling

```bash
# Bare biblioteket (raskt, uten Home Assistant):
uv sync
uv run pytest                  # btmesh-biblioteket + phase0-testene

# Hele testsuiten (bibliotek + HA-integrasjon) i et miljø med Home Assistant:
pip install -e .
pip install -r requirements-test.txt
pytest                         # alt: bibliotek, phase0, tests/ha

# Lint (CI låser akkurat denne versjonen):
pip install ruff==0.16.0
ruff check .
```

Kjør `pytest` uten sti: `testpaths` i `pyproject.toml` dekker bibliotektestene, `tests/ha/` og
`phase0/`-testene. Å kjøre én fil fra `tests/ha/` er ingen snarvei: `test_init.py` og
`test_diagnostics.py` importerer `custom_components` og henter Bluetooth-manageren først inne i testene,
og forutsetter at en tidligere fil i samme kjøring har gjort det på modulnivå. Alene feiler de med
`ModuleNotFoundError` eller `RuntimeError: BluetoothManager has not been set`, som ser ut som en ødelagt
utsjekk. Kjør hele suiten før du tror på at én fil feiler. `tests/ha/` er beskyttet med
`pytest.importorskip`, så de hoppes over når Home Assistant ikke er installert og kjøres når den er det.
CI installerer Home Assistant og kjører hele suiten på Linux. Se `.github/workflows/tests.yml`
(ruff + pytest) og `.github/workflows/validate.yml` (hassfest + HACS).

> På Windows slår `tests/ha/conftest.py` av `pytest-socket`, fordi hendelsesløkka trenger en socket der.
> En test som når nettverket, for eksempel en som utløser en ekte omlasting av en oppføring, vil derfor
> passere lokalt og feile i CI på Linux. Det er CI som gjør jobben sin, ikke en tilfeldig feil.

### Utgivelser

`docs/release-flow.md` beskriver hvordan en endring blir en publisert utgivelse: gren, PR, begge
CI-arbeidsflytene, en release candidate testet på maskinvare, og så taggen. Den har også reglene som kom
av å gjøre det feil: les remote på nytt rett før tagging, og hold `Closes #N` unna merge-teksten når saken
venter på bekreftelse fra den som meldte den.

### Innebygd bibliotek

`src/btmesh/` er det kanoniske biblioteket (sannhetskilden, publisert på PyPI). Fordi HACS installerer
`custom_components/bluetooth_mesh/` som den er, har integrasjonen en **innebygd kopi** av biblioteket i
`custom_components/bluetooth_mesh/btmesh/`, så den installeres uten ekstern `btmesh`-avhengighet (bare
`cryptography`, som Home Assistant allerede har). Etter en endring under `src/btmesh/` må den innebygde
kopien synkroniseres:

```bash
python scripts/sync_vendored_btmesh.py
```

CI kjører `python scripts/sync_vendored_btmesh.py --check` og feiler ved avvik. Det må den: hvert tre
importeres av hver sin halvdel av testsuiten, så en glemt synkronisering knekker ingen test og ville gitt
HACS-brukere en utdatert stakk på et grønt bygg.

## Dokumentasjon

Design og mulighetsstudier ligger i [`docs/plans/`](docs/plans/):

- [`2026-07-18-btmesh-design.md`](docs/plans/2026-07-18-btmesh-design.md): designdokument (problem,
  arkitektur, viktige valg).
- [`2026-07-18-phase0-feasibility.md`](docs/plans/2026-07-18-phase0-feasibility.md): plan for fase 0
  (mulighetsstudie).
- [`2026-07-19-phase0-report.md`](docs/plans/2026-07-19-phase0-report.md): rapport fra fase 0
  (gjennombruddet, verifisert på maskinvare).
- [`2026-07-19-phase1-library-and-ha-integration.md`](docs/plans/2026-07-19-phase1-library-and-ha-integration.md):
  plan for fase 1 (bibliotek + HA-integrasjon).

## Kreditering og ærlighet

Prosjektet vokste ut av kartleggingen av et Häfele Connect Mesh-anlegg etter at leverandørens gateway ble
lagt ned. Krypto- og kodeklagene i meshet er implementert fra den offentlige Bluetooth SIG
Mesh-spesifikasjonen og kontrollert mot spesifikasjonens offisielle testvektorer. Sameksistensløsningen
ble funnet ved forsøk på ekte maskinvare. Det er et uavhengig, uoffisielt prosjekt og ikke tilknyttet
eller godkjent av Häfele, ThingOS eller Bluetooth SIG.

## Støtte

Opprett en sak (issue) her for alt som gjelder integrasjonen: feil, spørsmål eller ønsker. Forumtråder er
for generell diskusjon og hjelp mellom brukere. Det som tas opp der, følges ikke opp og kan bli borte. En
sak blir ikke det.

Les [Kreditering og ærlighet](#kreditering-og-ærlighet) før du oppretter en: dette er en kartlagt stakk,
og der står det tydelig hva som er kontrollert mot spesifikasjonen og hva som er funnet ved forsøk på
ekte maskinvare.

For å få et nyttig svar med en gang, ta med:

- Home Assistant-versjonen og versjonen av denne integrasjonen;
- nodene det gjelder (merke, modell, og om de noen gang er provisjonert av en leverandørapp) og
  ESPHome-proxyene som brukes, med kort og ESPHome-versjon;
- diagnostikkfilen (Innstillinger → Enheter og tjenester → Bluetooth Mesh → **⋮** → *Last ned
  diagnostikk*);
- en debug-logg, og hva du gjorde, hva du forventet og hva som skjedde i stedet.

### Hold deg oppdatert

Nye versjoner kunngjøres her først. En kort melding kan komme i forumtråden etterpå, men denne siden er
den eneste fullstendige oversikten. Slik får du beskjed:

- **HACS tilbyr deg oppdateringen allerede**, med utgivelsesnotater. Du trenger ikke gjøre noe;
- abonner på `https://github.com/frekarlsen/ha-sg-smart-mesh/releases.atom` i en RSS-leser, eller i Home
  Assistant med `feedreader`-integrasjonen;
- eller bruk **Watch → Custom → Releases** på dette repoet.

## Lisens

[MIT](LICENSE) © 2026 David Simon (opprinnelig prosjekt), © 2026 Fredrik Karlsen (tillegg for SG Smart 3.0).
