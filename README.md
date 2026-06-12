# TICANET Bot - QSL Digital vía APRS

Bot APRS-IS standalone para gestión de nets y emisión de QSL digitales automatizadas.

Operado por **TI3WTI** — RadioLab TEC / TI0ARC, Cartago, Costa Rica.

Inspirado en [MYANET APRS Bot](http://9w2key.blogspot.com/) de 9W2KEY (Malasia).

## ¿Qué hace?

Un operador de radioaficionado envía un mensaje APRS al bot (por ejemplo `CQ TICANET`). El bot responde con un código de verificación. El operador ingresa ese código en un formulario web y recibe automáticamente una QSL digital personalizada en formato PDF por correo electrónico.

```
┌──────────┐                ┌──────────────┐              ┌──────────────┐
│ OPERADOR │                │  TICANET Bot │              │    Google    │
│ (APRS)   │                │  (RPi 3B+)   │              │    Cloud     │
└────┬─────┘                └──────┬───────┘              └──────┬───────┘
     │                             │                             │
     │  1. CQ TICANET              │                             │
     │ ──────────────────────────> │                             │
     │                             │  2. POST código + datos     │
     │                             │ ──────────────────────────> │
     │  3. Bienvenido! Code: 2179  │                             │ Sheets
     │ <────────────────────────── │                             │
     │                             │                             │
     │  4. Llena formulario con código                           │
     │ ─────────────────────────────────────────────────────────>│ Form
     │                             │                             │
     │                             │  5. Valida código           │
     │                             │     Genera PDF desde Slides │
     │  6. QSL PDF por email       │     Envía por Gmail         │
     │ <─────────────────────────────────────────────────────────│ Apps Script
     │                             │                             │
```

## Eventos soportados

El bot soporta múltiples eventos simultáneos. Cada evento tiene su propio comando CQ, horario y plantilla QSL independiente. Los eventos se definen en `events.json` (ver `events.example.json`):

- **APRS Day** — check-in semanal (estilo APRS Thursday)
- **Revista Matutina** — check-in durante la emisión mensual de la Revista Matutina TI0ARC (2.º domingo de cada mes)
- **Especial** — actividades especiales con fechas de inicio y fin definidas

Tipos de evento (`type`): `weekly`, `monthly` (con `week_of_month` y `day_of_week`) y `special` (con `start_date` / `end_date`).

## Estructura del repositorio

```
TICANET/
├── README.md               ← Este archivo
├── ticanet_bot.py          ← Bot APRS-IS (lee config.json, sin datos hardcoded)
├── config.example.json     ← Ejemplo de configuración (sin credenciales)
├── events.example.json     ← Ejemplo de eventos (sin IDs reales)
├── ticanet.service         ← Unit file de systemd
├── .gitignore              ← Excluye config.json, events.json, data/, *.log
├── apps_script/
│   └── Codigo.gs           ← Google Apps Script (recibe códigos y emite QSL)
├── templates/              ← Plantillas QSL (Google Slides)
└── LICENSE                 ← MIT
```

## Requisitos

- Raspberry Pi 3B+ (o cualquier Linux) con Python 3
- Biblioteca `aprslib`
- Passcode de APRS-IS válido (asociado a tu indicativo)
- Cuenta de Google con una plantilla QSL en Google Slides, un Google Form y un Apps Script desplegado como Web App

## Instalación rápida

```bash
git clone https://github.com/ti3wti/TICANET.git
cd TICANET
pip3 install aprslib

# Configuración (a partir de los ejemplos)
cp config.example.json config.json
cp events.example.json events.json
# Editá config.json con tu indicativo, passcode, coordenadas y URL del webhook
# Editá events.json con tus eventos y template_id de Slides
```

### Ejecutar como servicio (systemd)

```bash
sudo cp ticanet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ticanet
sudo systemctl start ticanet
sudo systemctl status ticanet
```

Ver logs en vivo:

```bash
journalctl -u ticanet -f
```

## Configuración

`config.json` (no se versiona; ver `config.example.json`):

```json
{
    "callsign": "TICANET",
    "login": "YOUR_CALLSIGN",
    "passcode": "YOUR_PASSCODE",
    "host": "rotate.aprs2.net",
    "port": 14580,
    "beacon_enabled": true,
    "beacon_interval": 1800,
    "latitude": 0.0000,
    "longitude": 0.0000,
    "beacon_comment": "TICANET Bot - QSL Digital",
    "beacon_symbol_table": "/",
    "beacon_symbol": "#",
    "data_dir": "/home/tecnico/ticanet-bot/data",
    "events_file": "/home/tecnico/ticanet-bot/events.json",
    "log_file": "/home/tecnico/ticanet-bot/ticanet.log",
    "msg_cooldown": 8,
    "ack_retries": 3,
    "sheets_webhook_url": "https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec"
}
```

## Google Apps Script

El archivo `apps_script/Codigo.gs` se instala en el editor de Apps Script vinculado a la hoja de cálculo:

- `doPost()` — recibe los códigos del bot vía HTTP POST y los escribe en la hoja **Codes**.
- `onFormSubmit()` — valida el código + indicativo, genera el PDF desde la plantilla de Slides y lo envía por Gmail.

Marcadores soportados en la plantilla de Google Slides:
`{{CALLSIGN}}`, `{{EVENT}}`, `{{DATE}}`, `{{TIME_UTC}}`, `{{CODE}}`, `{{NUMBER}}`

## Licencia

MIT © 2026 Ing. William Marín Moreno (TI3WTI) — RadioLab TEC / TI0ARC, Cartago, Costa Rica.

## Créditos

- **TI3WTI** — [qrz.com/db/TI3WTI](https://www.qrz.com/db/TI3WTI)
- **RadioLab-TEC** — Escuela de Ingeniería Electrónica, ITCR
- **TI0ARC** — Asociación de Radioaficionados Cartago
- Inspirado en MYANET APRS Bot de 9W2KEY
