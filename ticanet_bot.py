#!/usr/bin/env python3
"""
TICANET Bot — Bot APRS-IS para gestión de nets y QSL digitales.

Arquitectura standalone usando aprslib.
Cada evento define su propio comando CQ y plantilla QSL.

Configuración en config.json (no incluido en el repositorio).
Eventos en events.json.

Autor: Ing. William Marín Moreno (TI3WTI)
Licencia: MIT
"""

import aprslib
import json
import csv
import os
import random
import string
import time
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# CARGAR CONFIGURACIÓN
# ============================================================================

def load_config():
    config_path = os.path.join(SCRIPT_DIR, "config.json")
    if not os.path.exists(config_path):
        print(f"ERROR: No se encontró {config_path}")
        print("Copie config.example.json a config.json y edite sus datos.")
        exit(1)
    with open(config_path, "r") as f:
        return json.load(f)

CONFIG = load_config()

# ============================================================================
# LOGGING
# ============================================================================

log_file = CONFIG.get("log_file", os.path.join(SCRIPT_DIR, "ticanet.log"))
os.makedirs(os.path.dirname(log_file), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("TICANET")

# ============================================================================
# GESTIÓN DE EVENTOS
# ============================================================================

def load_events(filepath):
    if not os.path.exists(filepath):
        log.warning(f"Archivo de eventos no encontrado: {filepath}")
        return []
    with open(filepath, "r") as f:
        return json.load(f)


def is_event_in_window(event):
    now = datetime.utcnow()
    offset = timedelta(hours=event.get("timezone_offset", -6))
    local_now = now + offset

    start_h, start_m = map(int, event["start_time"].split(":"))
    end_h, end_m = map(int, event["end_time"].split(":"))
    time_start = local_now.replace(hour=start_h, minute=start_m, second=0)
    time_end = local_now.replace(hour=end_h, minute=end_m, second=59)

    if event["type"] == "weekly":
        if local_now.weekday() != event["day_of_week"]:
            return False
        return time_start <= local_now <= time_end

    elif event["type"] == "monthly":
        if local_now.weekday() != event["day_of_week"]:
            return False
        week_num = (local_now.day - 1) // 7 + 1
        if week_num != event["week_of_month"]:
            return False
        return time_start <= local_now <= time_end

    elif event["type"] == "special":
        start_date = datetime.strptime(event["start_date"], "%Y-%m-%d")
        end_date = datetime.strptime(event["end_date"], "%Y-%m-%d")
        if not (start_date.date() <= local_now.date() <= end_date.date()):
            return False
        return time_start <= local_now <= time_end

    return False


def find_event_by_command(events, command):
    for event in events:
        if not event.get("active", False):
            continue
        event_cmd = event.get("command", "").upper()
        aliases = [a.upper() for a in event.get("aliases", [])]
        if command == event_cmd or command in aliases:
            return event, is_event_in_window(event)
    return None, False


# ============================================================================
# GESTIÓN DE MIEMBROS / CHECK-INS
# ============================================================================

class MemberManager:

    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _get_filepath(self, event_id, date_str=None):
        if date_str is None:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
        event_dir = os.path.join(self.data_dir, event_id)
        os.makedirs(event_dir, exist_ok=True)
        return os.path.join(event_dir, f"{date_str}.csv")

    def _get_today_str(self, tz_offset=-6):
        local = datetime.utcnow() + timedelta(hours=tz_offset)
        return local.strftime("%Y-%m-%d")

    def get_code(self, callsign, event):
        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["callsign"].upper() == callsign.upper():
                    return row["code"]
        return None

    def checkin(self, callsign, event):
        tz = event.get("timezone_offset", -6)
        date_str = self._get_today_str(tz)
        filepath = self._get_filepath(event["id"], date_str)

        existing = self.get_code(callsign, event)
        if existing:
            return existing, self.get_count(event), True

        code = self._generate_code(filepath)
        count = self.get_count(event) + 1

        file_exists = os.path.exists(filepath)
        with open(filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "number", "callsign", "code", "timestamp_utc",
                "event_id", "event_name"
            ])
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "number": count,
                "callsign": callsign.upper(),
                "code": code,
                "timestamp_utc": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "event_id": event["id"],
                "event_name": event["name"]
            })

        log.info(f"CHECK-IN: {callsign} -> evento={event['id']} "
                 f"codigo={code} orden={count}")
        return code, count, False

    def get_count(self, event):
        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return 0
        with open(filepath, "r") as f:
            return sum(1 for _ in csv.DictReader(f))

    def get_list(self, event):
        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return []
        with open(filepath, "r") as f:
            return [row["callsign"] for row in csv.DictReader(f)]

    def _generate_code(self, filepath, length=4):
        existing_codes = set()
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                existing_codes = {row["code"] for row in csv.DictReader(f)}
        while True:
            code = random.choice("123456789") + "".join(random.choices(string.digits, k=length-1))
            if code not in existing_codes:
                return code


# ============================================================================
# BOT APRS
# ============================================================================

class TICANETBot:

    def __init__(self, config):
        self.config = config
        events_file = config.get("events_file",
                                 os.path.join(SCRIPT_DIR, "events.json"))
        self.events = load_events(events_file)
        self.members = MemberManager(
            config.get("data_dir", os.path.join(SCRIPT_DIR, "data")))
        self.last_msg_time = {}
        self.last_beacon = 0
        self.ais = None
        self._msg_counter = 0

    def connect(self):
        self.ais = aprslib.IS(
            callsign=self.config["login"],
            passwd=self.config["passcode"],
            host=self.config["host"],
            port=self.config["port"]
        )
        self.ais.set_filter(f"b/{self.config['callsign']}* "
                            f"g/{self.config['callsign']}")
        self.ais.connect()
        log.info(f"Conectado a APRS-IS como {self.config['login']} "
                 f"(tactical: {self.config['callsign']})")

    def send_message(self, to_call, message):
        now = time.time()
        cooldown = self.config.get("msg_cooldown", 8)
        if to_call in self.last_msg_time:
            elapsed = now - self.last_msg_time[to_call]
            if elapsed < cooldown:
                time.sleep(cooldown - elapsed)

        self._msg_counter += 1
        msg_id = str(self._msg_counter).zfill(3)
        to_padded = to_call.ljust(9)
        packet = (f"{self.config['callsign']}>APRS,TCPIP*::"
                  f"{to_padded}:{message}{{{msg_id}")
        try:
            self.ais.sendall(packet)
            self.last_msg_time[to_call] = time.time()
            log.info(f"TX -> {to_call}: {message}")
        except Exception as e:
            log.error(f"Error enviando a {to_call}: {e}")

    def send_ack(self, to_call, msg_id):
        to_padded = to_call.ljust(9)
        packet = (f"{self.config['callsign']}>APRS,TCPIP*::"
                  f"{to_padded}:ack{msg_id}")
        try:
            self.ais.sendall(packet)
        except Exception as e:
            log.error(f"Error enviando ACK a {to_call}: {e}")

    def send_beacon(self):
        if not self.config.get("beacon_enabled", False):
            return
        now = time.time()
        if now - self.last_beacon < self.config.get("beacon_interval", 1800):
            return

        lat = self.config["latitude"]
        lon = self.config["longitude"]
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"
        lat, lon = abs(lat), abs(lon)
        lat_str = f"{int(lat):02d}{(lat - int(lat)) * 60:05.2f}{lat_dir}"
        lon_str = f"{int(lon):03d}{(lon - int(lon)) * 60:05.2f}{lon_dir}"
        sym_t = self.config.get("beacon_symbol_table", "/")
        sym_c = self.config.get("beacon_symbol", "#")
        comment = self.config.get("beacon_comment", "TICANET Bot")
        packet = (f"{self.config['callsign']}>APRS,TCPIP*:"
                  f"={lat_str}{sym_t}{lon_str}{sym_c}{comment}")
        try:
            self.ais.sendall(packet)
            self.last_beacon = now
            log.info(f"BEACON enviado: {lat_str}/{lon_str}")
        except Exception as e:
            log.error(f"Error enviando beacon: {e}")

    def handle_packet(self, packet):
        try:
            parsed = aprslib.parse(packet)
        except (aprslib.ParseError, aprslib.UnknownFormat):
            return

        if parsed.get("format") != "message":
            return
        addresse = parsed.get("addresse", "").strip()
        if addresse.upper() != self.config["callsign"].upper():
            return

        from_call = parsed.get("from", "").strip()
        message_text = parsed.get("message_text", "").strip()
        msg_id = parsed.get("msgNo", "")

        log.info(f"RX <- {from_call}: {message_text} (id={msg_id})")

        if msg_id:
            self.send_ack(from_call, msg_id)
        if not message_text or message_text.startswith("ack"):
            return

        self.process_command(from_call, message_text.upper())

    def process_command(self, from_call, command):
        events_file = self.config.get("events_file",
                                      os.path.join(SCRIPT_DIR, "events.json"))
        self.events = load_events(events_file)

        if command in ("INFO", "HELP", "AYUDA", "?"):
            self._cmd_info(from_call)
            return
        if command in ("EVENTOS", "EVENTS"):
            self._cmd_eventos(from_call)
            return
        if command in ("SALIR", "QUIT", "EXIT", "KELUAR"):
            self._cmd_salir(from_call)
            return

        event, in_window = find_event_by_command(self.events, command)
        if event:
            if in_window:
                self._cmd_checkin(from_call, event)
            else:
                self.send_message(from_call,
                                  f"{event['name']} no esta activo ahora. "
                                  f"{event['description']}. 73!")
            return

        if command in ("LIST", "LISTA"):
            self._cmd_list(from_call)
            return
        if command in ("STATUS", "ESTADO"):
            self._cmd_status(from_call)
            return

        self.send_message(from_call,
                          "Cmd no reconocido. Envia INFO para ayuda.")

    # -- Comandos --

    def _cmd_checkin(self, from_call, event):
        code, count, already = self.members.checkin(from_call, event)
        form_url = event.get("form_url", "")
        cooldown = self.config.get("msg_cooldown", 8)

        if already:
            self.send_message(from_call,
                              f"Ya registrado en {event['name']}! "
                              f"Codigo: {code} QSL: {form_url}")
        else:
            # Enviar código Y número de participante al Sheet
            self._post_code_to_sheets(from_call, code, count, event)

            self.send_message(from_call,
                              f"BIENVENIDO a {event['name']}! "
                              f"Participante #{count}.")
            time.sleep(cooldown)
            self.send_message(from_call,
                              f"Tu codigo: {code} "
                              f"Reclama tu QSL: {form_url}")
            time.sleep(cooldown)
            self.send_message(from_call,
                              f"Espera {cooldown}s "
                              f"antes de enviar otro mensaje. 73!")

    def _cmd_list(self, from_call):
        event = self._get_any_active_in_window()
        if not event:
            self.send_message(from_call, "No hay evento activo ahora.")
            return
        members = self.members.get_list(event)
        if not members:
            self.send_message(from_call, "Aun no hay check-ins.")
            return
        self.send_message(from_call,
                          f"{event['name']}: {len(members)} check-ins")
        for i in range(0, len(members), 5):
            time.sleep(self.config.get("msg_cooldown", 8))
            self.send_message(from_call, " | ".join(members[i:i + 5]))

    def _cmd_status(self, from_call):
        event = self._get_any_active_in_window()
        if not event:
            self.send_message(from_call, "No hay evento activo ahora.")
            return
        self.send_message(from_call,
                          f"{event['name']} | "
                          f"Check-ins: {self.members.get_count(event)}")

    def _cmd_info(self, from_call):
        active = [e for e in self.events if e.get("active")]
        cq_cmds = [e.get("command", "?") for e in active]
        if cq_cmds:
            self.send_message(from_call,
                              f"TICANET Bot - Check-in: {', '.join(cq_cmds)}")
            time.sleep(self.config.get("msg_cooldown", 8))
        self.send_message(from_call,
                          "Otros cmds: LIST, STATUS, EVENTOS, INFO, SALIR")

    def _cmd_eventos(self, from_call):
        active = [e for e in self.events if e.get("active")]
        if not active:
            self.send_message(from_call, "No hay eventos programados.")
            return
        for event in active[:3]:
            msg = f"{event.get('command', '?')} -> {event.get('description', event['name'])}"[:67]
            self.send_message(from_call, msg)
            time.sleep(self.config.get("msg_cooldown", 8))

    def _cmd_salir(self, from_call):
        self.send_message(from_call,
                          "Gracias por participar! Tu registro "
                          "se mantiene. 73 de TICANET!")

    def _get_any_active_in_window(self):
        for event in self.events:
            if event.get("active") and is_event_in_window(event):
                return event
        return None

    # -- Google Sheets --

    def _post_code_to_sheets(self, callsign, code, number, event):
        """Envía código y número de participante a Google Sheets."""
        url = self.config.get("sheets_webhook_url", "")
        if not url:
            return
        try:
            data = {
                "callsign": callsign.upper(),
                "code": code,
                "event_id": event["id"],
                "event_name": event["name"],
                "template_id": event.get("template_id", ""),
                "number": number,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            }
            resp = requests.post(url, json=data, timeout=10)
            log.info(f"Sheets POST: {resp.status_code} para {callsign} #{number}")
        except Exception as e:
            log.error(f"Error POST Sheets: {e}")

    # -- Loop principal --

    def run(self):
        log.info("=" * 60)
        log.info("TICANET Bot iniciando...")
        log.info(f"Callsign: {self.config['callsign']}")
        log.info(f"Login: {self.config['login']}")
        log.info(f"Eventos cargados: {len(self.events)}")
        for e in self.events:
            status = "ACTIVO" if e.get("active") else "inactivo"
            log.info(f"  [{status}] {e.get('command', '?')} -> {e['name']}")
        log.info("=" * 60)

        while True:
            try:
                self.connect()
                self.send_beacon()
                self.ais.consumer(self.handle_packet, immortal=True, raw=True)
            except aprslib.ConnectionDrop:
                log.warning("Conexion perdida. Reconectando en 30s...")
                time.sleep(30)
            except aprslib.ConnectionError as e:
                log.error(f"Error de conexion: {e}. Reintentando en 60s...")
                time.sleep(60)
            except KeyboardInterrupt:
                log.info("Bot detenido por el usuario.")
                break
            except Exception as e:
                log.error(f"Error inesperado: {e}. Reintentando en 30s...")
                time.sleep(30)


if __name__ == "__main__":
    os.makedirs(CONFIG.get("data_dir", os.path.join(SCRIPT_DIR, "data")),
                exist_ok=True)
    bot = TICANETBot(CONFIG)
    bot.run()