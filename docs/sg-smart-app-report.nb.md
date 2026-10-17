# Dekompilering — SG Smart (`com.sgas.leddimapp`) v5.0.958

Analyse av XAPK-en `com_sgas_leddimapp_arm64-v8a.xapk`. Appen er **SG Smart** fra
**SG Armaturen** (sg-as.com), utviklet av **ideaslab** (Hong Kong). Formålet med
denne analysen er å forstå hvordan appen styrer SG sine LED-dimmere/armaturer, med
tanke på **Home Assistant-integrasjon**.

---

## 1. Kort oppsummering (TL;DR)

* Appen er en **Flutter-app**. Forretningslogikken er skrevet i **Dart** og kompilert
  til **native ARM-maskinkode** i `libapp.so` (20 MB AOT-snapshot) — *ikke* til
  Java/DEX-bytecode. jadx gir deg derfor bare Android-skallet + pluginer, ikke
  Dart-logikken direkte. Men mye er likevel lesbart (se under).
* **Lokal styring skjer over Bluetooth-mesh**, i to generasjoner:
  * **CSRmesh** (Qualcomm/CSR) — eldre SG Smart-produkter
  * **Bluetooth SIG Mesh på Telink-chip** — nyere produkter
* Det finnes en **WiFi-tilkoblet Hub/Gateway** som bygger bro mellom mesh og sky via **MQTT**.
* De nyeste enhetene støtter **Matter** — dette er den klart enkleste veien inn i HA.
* Sky-backend: `leddimapp.sg-as.com` (REST), Firebase (push/RTDB/config), og
  **Tibber-integrasjon** for strømpris-styring.

---

## 2. Hvorfor dette er en Flutter-app — og hva det betyr for dekompilering

| Lag | Språk | Hva du får ut |
|-----|-------|---------------|
| Android-skall + pluginer | Java/Kotlin → DEX | **Fullt dekompilerbart** med jadx (ligger i `java_sources/`) |
| CSRmesh-SDK (`com.csr.*`) | Java | **Fullt lesbart** — 411 filer |
| Telink SIG Mesh-SDK (`com.telink.*`) | Java | **Fullt lesbart** — 366 filer |
| SGAS-egen mesh-bro (`hk.ideaslab.flutter_sg_sig_mesh`) | Java | **Lesbart** (tynt lag) |
| **App-logikk (UI, protokoll-orkestrering, API-kall)** | **Dart → native ARM** | **Ikke** i Java. Ligger i `libapp.so` som maskinkode |

Det viktige poenget: den delen som "limer alt sammen" (hvilke mesh-kommandoer som
sendes når du skrur en dimmer, hvordan REST-kallene settes sammen) er Dart og dermed
native maskinkode. Du får den **ikke** som pen kildekode fra jadx.

**Men** — to ting redder mye av analysen:
1. **Selve mesh-protokollene (CSR + Telink) er Java-SDK-er** og fullt lesbare. Det er
   her den faktiske on-air-protokollen er implementert (opcodes, pakking, kryptering).
2. **Dart-snapshotet beholder tekststrenger**: klassenavn, hele SQL-skjemaet, logg-
   meldinger, URL-er og nøkkel-feltnavn. Alt dette ligger i `dart_strings.txt` og
   forteller svært mye om hvordan appen er bygd.

**Neste steg hvis du vil ha selve Dart-koden:** verktøyet **`blutter`**
(github.com/worawit/blutter) rekonstruerer Dart-klasser/metoder fra `libapp.so`. Det
krever at man bygger mot nøyaktig samme Dart/Flutter-versjon som appen. Alternativt
**`reFlutter`** som patcher appen for å dumpe snapshot ved kjøring. Si fra hvis du vil
at jeg prøver blutter-ruta.

---

## 3. Arkitektur — tre styringsveier

```
                 ┌─────────────────────────────────────────────┐
                 │                SG Smart (app)                │
                 └───────┬───────────────┬───────────────┬──────┘
         direkte BLE     │               │ WiFi/sky      │ Matter
        (når du er nær)  │               │ (remote)      │
                         ▼               ▼               ▼
                 ┌───────────────┐  ┌──────────┐  ┌──────────────┐
                 │ Mesh-node     │  │ SG Hub/   │  │ Matter-enhet │
                 │ (GATT proxy   │  │ Gateway   │  │ / hub        │
                 │  0x1828)      │  │ (WiFi)    │  │              │
                 └──────┬────────┘  └────┬─────┘  └──────┬───────┘
                        │ BT-mesh        │ MQTT          │ Matter
                        ▼                ▼ (til sky)      ▼
                 ┌──────────────────────────────┐  (native i HA)
                 │  Dimmere / armaturer / drivere │
                 │  (CSRmesh  eller  SIG Mesh)    │
                 └──────────────────────────────┘
```

* **Lokal BLE:** appen kobler seg til én mesh-node via GATT proxy (SIG Mesh service
  `0x1828`) eller CSR sin tjeneste, og sender mesh-meldinger inn i nettet.
* **Hub/Gateway:** en WiFi-tilkoblet SG Hub registreres (`RegisterGatewayJob`), henter
  MQTT-server dynamisk fra `/route-api/mqtt-server`, og relér mesh↔sky. Dette er det
  som gir fjernstyring når du ikke er hjemme (`requestHubInfoFromMQTT`,
  `mqttGatewayConnected`).
* **Matter:** lokal DB har `matterFabricId`, `matterIPK`, `matterCaseTag` — appen kan
  dele enheter inn i en Matter-fabric.

---

## 4. Det viktigste for Home Assistant — tre ruter rangert

### Rute A — Matter (klart enklest, native i HA) ✅
Hvis dimmerne/hub-en din er av nyere type med Matter-støtte, kan du legge dem rett inn
i Home Assistant over Matter uten noe reverse engineering. HA har innebygd Matter-
støtte. Sjekk om enhetene dine viser Matter i SG Smart-appen (det finnes egne skjermer:
`matterIntegration`, `matterMember`, `matterWarning`).
**Dette bør du teste først.**

### Rute B — Lokal Bluetooth-mesh (mulig, men krevende)
For å snakke direkte med mesh-nettet trenger du nettverksnøklene. Appen lagrer dem
**lokalt** i sin SQLite-database, per "Sector" (deres ord for et mesh-nett/anlegg):

```sql
CREATE TABLE `Sector` ( ... `networkPassphrase` TEXT, `sigMeshNetKey` TEXT,
                         `sigMeshAppKey` TEXT, `matterIPK` TEXT, `matterFabricId` TEXT, ... )
```

* **SIG Mesh (Telink):** med `sigMeshNetKey` + `sigMeshAppKey` kan du i prinsippet
  styre nettet selv. Men: HA har **ingen god innebygd SIG-mesh-støtte**. Realistisk vei
  er en egen mesh-proxy (f.eks. en ESP32 / dedikert Telink-node) som eksponerer
  enhetene mot HA. Telink-SDK-en i `java_sources/com/telink/` viser hele
  on-air-protokollen (opcodes, kryptering, provisjonering).
* **CSRmesh:** enda mer lukket; `java_sources/com/csr/` har SDK-en, men dette er
  tyngre å gjenbruke.
* **Nøkkel-ekstraksjon:** nøklene ligger på *din egen* telefon/konto. På en rootet
  Android (eller via `adb backup` på eldre versjoner) kan du hente SQLite-fila til
  appen og lese ut nøklene for *ditt eget* mesh. Det er helt legitimt for dine egne
  enheter — det er bare dine egne nøkler.

### Rute C — Sky-API / MQTT (uoffisielt)
Du kan reverse-engineere REST-API-et (`leddimapp.sg-as.com/sg/m/`) og MQTT-broen og
styre via skyen. Fungerer, men er uoffisielt og kan brytes når SG oppdaterer backend.
Krever at du fanger din egen Bearer-token fra appen. Minst robust av de tre.

**Anbefalt rekkefølge:** A (Matter) → B (mesh-nøkler) → C (sky).

---

## 5. Endepunkter og identifikatorer funnet

| Hva | Verdi |
|-----|-------|
| REST-API (hoved) | `https://leddimapp.sg-as.com/sg/` og `/sg/m/` |
| MQTT-server (hentes dynamisk) | `…/route-api/mqtt-server` |
| ideaslab backend | `https://sgapps2.ideaslab.hk` |
| Firebase Realtime DB | `https://leddimapp-211203.firebaseio.com` |
| Firebase project id | `leddimapp-211203` |
| FCM sender id | `877728264673` |
| Firebase app id | `1:877728264673:android:b4cdb51e9c75ab5f` |
| OAuth web client | `877728264673-…apps.googleusercontent.com` |
| Tibber (strømpris) | `https://api.tibber.com/v1-beta/gql` |
| Support / recall | `sg-as.com/.../sg-smart-support`, `.../tilbakekalling-av-sg-smart-plug` |

> Merk om Firebase-verdiene: dette er **innebygd klientkonfig** som ligger i hver kopi
> av appen og kan hentes ut av hvem som helst. `google_api_key` (AIza…) er en
> Android-API-nøkkel begrenset av pakkenavn + signatur, ikke en konto-hemmelighet.
> Firebase RTDB er beskyttet av server-side security rules. De er altså greie å kjenne
> til, men gir ikke i seg selv tilgang til noe.

---

## 6. BLE-detaljer

| UUID | Betydning |
|------|-----------|
| `00001827-0000-1000-8000-00805F9B34FB` | SIG Mesh **Provisioning Service** (0x1827) |
| `00001828-0000-1000-8000-00805F9B34FB` | SIG Mesh **Proxy Service** (0x1828) |
| `00010203-0405-0607-0809-0A0B0C0D1910` | Telink **OTA** characteristic |
| `00010203-0405-0607-0809-0A0B0C0D2B10/2B11` | Telink mesh (pair / online status) |
| `258EAFA5-E914-47DA-95CA-C5AB0DC85B11` | Egendefinert (gateway/bridge) |

Appen bruker en **"gateway vendor model"**: den sender vendor-model-meldinger
(`sendVendorModelMessage`, `requestGatewayInfoFromVendorModel`) til en bro-node som så
relér inn i meshet. Logikken ligger i `GatewayVendorModelUtil` (i Dart-strengene) og
Telink-SDK-en.

Rettigheter appen ber om (fra `manifest.json`): BLE scan/connect, posisjon (kreves for
BLE-skann), WiFi-state, INTERNET, kamera (QR-paring), FCM, foreground-service for
tilkoblet enhet.

---

## 7. Produktspekter (fra asset-navn og DeviceType)

Appen dekker bl.a.: `din1000`, `din3600`, `din`, `driver350ma`, `driverpill`,
`driver100w`, `ziptrack`, `ceiling`, `floor_box`, `wallmount`, `dk_dimmer`, `dimmer`,
samt `gateway`/hub. Det finnes også referanser til **"Heatreg Smart"** (SG sin
varmeregulator) og **"SG Smart Plug"** (med egen tilbakekalling/recall). `DeviceType`
forekommer 107 ganger i Dart-koden — det er et stort enum over produktvarianter.

---

## 8. Hva ligger i zip-en

```
SG_Smart_decompiled/
├── RAPPORT.md                 ← dette dokumentet
├── dart_strings.txt           ← alle strenger fra libapp.so (Dart) — søk her
├── AndroidManifest.xml        ← dekodet manifest (komponenter, tjenester)
├── res_values/                ← strings.xml, arrays.xml, public.xml (Firebase-config m.m.)
└── java_sources/              ← all dekompilert Java fra jadx
    ├── com/telink/…           ← Telink SIG Mesh SDK (366 filer) — lokalprotokoll
    ├── com/csr/…              ← CSRmesh SDK (411 filer) — eldre lokalprotokoll
    ├── hk/ideaslab/…          ← SGAS sin mesh-bro
    ├── com/signify/hue/…      ← flutter_reactive_ble
    └── com/polidea/rxandroidble2/…
```

## 9. Forslag til neste steg

1. **Sjekk Matter først** i appen på dine egne enheter — det kan gjøre hele
   reverse-engineeringen unødvendig.
2. **Hvis ikke Matter:** identifiser om *dine* enheter er CSR eller Telink (eldre vs
   nyere innkjøp). Bruk **nRF Connect** på telefonen til å scanne og se om de
   annonserer SIG Mesh proxy (`0x1828`).
3. **For full Dart-kode:** si fra, så kan jeg forsøke `blutter` mot `libapp.so`.
4. Jeg kan dykke dypere i Telink-SDK-en og dokumentere de konkrete mesh-opcodene for
   på/av/dim-nivå hvis du vil gå for den lokale ruta.
