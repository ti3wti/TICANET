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
## Ejemplo de QSL

![QSL de ejemplo TICANET](https://raw.githubusercontent.com/ti3wti/TICANET/main/qsl_ej1.png)

## Eventos soportados

El bot soporta múltiples eventos simultáneos. Cada evento tiene su propio comando CQ, horario y plantilla QSL independiente. No es necesario modificar el código del bot ni del Apps Script para agregar nuevos eventos: solo se edita `events.json`.

| Comando | Evento | Disponibilidad | Plantilla |
|---------|--------|----------------|-----------|
| `CQ TICANET` | Net general TICANET | 24/7, todos los días | QSL general |
| `CQ APRSDAY` | APRS Thursday CR | Jueves, todo el día | QSL Thursday |
| `CQ MATUTINA` | Revista Matutina TI0ARC | 2do domingo, 07:00-10:00 | QSL Revista |
| `CQ [CUSTOM]` | Actividades especiales | Fechas configurables | QSL por evento |

## Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `CQ TICANET` | Check-in a la net general (24/7) |
| `CQ APRSDAY` | Check-in al APRS Thursday |
| `CQ MATUTINA` | Check-in a la Revista Matutina |
| `LIST` | Lista de check-ins del evento activo |
| `STATUS` | Cantidad de check-ins |
| `INFO` | Comandos disponibles y eventos activos |
| `EVENTOS` | Lista de todos los eventos programados |
| `SALIR` | Despedida (el registro se mantiene) |

## Arquitectura

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  APRSDroid /    │     │  TICANET Bot │     │  Google Cloud       │
│  Radio + iGate  │────>│  (RPi 3B+)   │────>│  Sheets + Forms +   │
│                 │<────│  Python 3    │     │  Slides + Gmail     │
└─────────────────┘     └──────────────┘     └─────────────────────┘
        APRS-IS              aprslib              Apps Script
```

- **Bot**: Python 3 + aprslib, corre en Raspberry Pi 3B+ (o cualquier sistema con Internet)
- **Datos locales**: CSV por evento/fecha en la RPi, numeración independiente por evento
- **Datos en la nube**: Google Sheets recibe los códigos vía HTTP POST
- **Certificados**: Google Slides (plantilla por evento) → PDF → Gmail automático
- **Formulario**: Google Forms para reclamar la QSL con código de verificación

## Requisitos

### Hardware

- Raspberry Pi 3B+ (o cualquier dispositivo con Python 3 e Internet)
- MicroSD 8GB+ con Raspberry Pi OS Lite
- Fuente de alimentación estable
- Conexión WiFi (2.4 GHz para RPi 3 Model B) o Ethernet

### Software

- Python 3.10+
- `aprslib` y `requests` (se instalan con pip)

### Cuentas

- Indicativo de radioaficionado válido con passcode APRS-IS
- Cuenta de Google (para Forms, Sheets, Slides, Apps Script)

## Instalación rápida

```bash
# 1. Clonar repositorio
git clone https://github.com/ti3wti/TICANET.git
cd TICANET

# 2. Instalar dependencias
pip3 install aprslib requests --break-system-packages

# 3. Crear archivos de configuración
cp config.example.json config.json
cp events.example.json events.json

# 4. Editar config.json con tus credenciales
nano config.json

# 5. Editar events.json con tus eventos y template_id
nano events.json

# 6. Probar manualmente
python3 ticanet_bot.py

# 7. Instalar como servicio
sudo cp ticanet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ticanet
sudo systemctl start ticanet
```

Ver logs en vivo:

```bash
journalctl -u ticanet -f
```

Para instrucciones detalladas paso a paso, ver [INSTALL.md](INSTALL.md).

## Configuración

### config.json (no se sube a Git)

```json
{
    "callsign": "TICANET",
    "login": "TU_INDICATIVO",
    "passcode": "TU_PASSCODE",
    "host": "rotate.aprs2.net",
    "port": 14580,
    "beacon_enabled": true,
    "beacon_interval": 1800,
    "latitude": 9.8394,
    "longitude": -83.9022,
    "beacon_comment": "TICANET Bot - QSL Digital",
    "beacon_symbol_table": "/",
    "beacon_symbol": "#",
    "data_dir": "/home/tecnico/ticanet-bot/data",
    "events_file": "/home/tecnico/ticanet-bot/events.json",
    "log_file": "/home/tecnico/ticanet-bot/ticanet.log",
    "msg_cooldown": 8,
    "ack_retries": 3,
    "sheets_webhook_url": "https://script.google.com/macros/s/.../exec"
}
```

### events.json

Cada evento define su comando, horario y plantilla:

```json
[
    {
        "id": "aprs_day",
        "name": "APRS Thursday Costa Rica",
        "command": "CQ TICANET",
        "aliases": ["CQ", "CHECKIN"],
        "type": "weekly",
        "day_of_week": 3,
        "start_time": "00:00",
        "end_time": "23:59",
        "timezone_offset": -6,
        "template_id": "ID_DE_GOOGLE_SLIDES",
        "form_url": "https://forms.gle/xxx",
        "active": true
    }
]
```

#### Tipos de evento

| Tipo | Campos requeridos | Ejemplo |
|------|-------------------|---------|
| `weekly` | `day_of_week` (0=Lun, 6=Dom) | APRS Thursday |
| `monthly` | `week_of_month`, `day_of_week` | Revista Matutina (2do domingo) |
| `special` | `start_date`, `end_date` | Actividad especial |

## Google Apps Script

El archivo `apps_script/Codigo.gs` contiene el código para Google Apps Script que:

1. **doPost()**: Recibe códigos del bot vía HTTP POST y los escribe en la hoja "Codes".
2. **onFormSubmit()**: Valida el código ingresado en el formulario, genera el PDF desde la plantilla de Google Slides y lo envía por correo.

### Configuración del Apps Script

1. Crear Google Form con campos: Indicativo, Código, Email
2. Vincular respuestas a Google Sheet
3. Crear hoja "Codes" en el mismo Sheet
4. Extensiones → Apps Script → pegar código de `apps_script/Codigo.gs`
5. Configurar `TEMPLATE_ID` con el ID de la plantilla de Google Slides
6. Implementar como App Web (acceso: cualquier persona)
7. Crear trigger: `onFormSubmit` → Al enviar formulario

### Plantilla QSL (Google Slides)

Crear una presentación con estos marcadores en el texto:

| Marcador | Se reemplaza por |
|----------|-----------------|
| `{{CALLSIGN}}` | Indicativo del operador |
| `{{EVENT}}` | Nombre del evento |
| `{{DATE}}` | Fecha del check-in |
| `{{TIME_UTC}}` | Hora UTC del check-in |
| `{{CODE}}` | Código de verificación |
| `{{NUMBER}}` | Número de participante |

## Estructura del proyecto

```
TICANET/
├── ticanet_bot.py          # Bot principal
├── config.json             # Configuración (NO en Git)
├── config.example.json     # Ejemplo de configuración
├── events.json             # Eventos activos (NO en Git)
├── events.example.json     # Ejemplo de eventos
├── ticanet.service         # Archivo systemd
├── apps_script/
│   └── Codigo.gs           # Google Apps Script
├── templates/
│   └── QSL_APRS_Thursday.pptx  # Plantilla QSL base
├── data/                   # CSV de check-ins (NO en Git)
├── INSTALL.md              # Guía de instalación detallada
├── LICENSE
└── README.md
```

## Créditos

- **Inspiración**: [MYANET APRS Bot](http://9w2key.blogspot.com/) por 9W2KEY (Malasia)
- **Biblioteca APRS**: [aprslib](https://github.com/rossengeorgiev/aprs-python)
- **Referencia**: [APRSD](https://github.com/craigerl/aprsd) por KM6LYW

## Licencia

MIT License — ver [LICENSE](LICENSE).

## Contacto

- **Operador**: Ing. William Marín Moreno ([TI3WTI](https://www.qrz.com/db/TI3WTI))
- **Club**: TI0ARC — Asociación de Radioaficionados de Cartago
- **Laboratorio**: RadioLab TEC — ITCR, Cartago, Costa Rica
