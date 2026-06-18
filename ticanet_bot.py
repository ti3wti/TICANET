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
import threading
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

log = logging.getLogger("TICANET")
log.setLevel(logging.INFO)
log.propagate = False  # evita que los mensajes lleguen también al root (duplicados)

# Solo agregar handlers si no existen ya (evita duplicación al re-importar)
if not log.handlers:
    _formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    _file_handler = logging.FileHandler(log_file)
    _file_handler.setFormatter(_formatter)
    log.addHandler(_file_handler)

    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(_formatter)
    log.addHandler(_stream_handler)

# ============================================================================
# GESTIÓN DE EVENTOS
# ============================================================================

def load_events(filepath):
    if not os.path.exists(filepath):
        log.warning(f"Archivo de eventos no encontrado: {filepath}")
        return []
    with open(filepath, "r") as f:
        return json.load(f)


def latlon_to_grid(lat, lon):
    """Convierte lat/lon a grid locator Maidenhead (6 caracteres)."""
    lon += 180.0
    lat += 90.0
    A = ord("A")
    field_lon = int(lon // 20)
    field_lat = int(lat // 10)
    square_lon = int((lon % 20) // 2)
    square_lat = int((lat % 10) // 1)
    sub_lon = int(((lon % 2) / 2) * 24)
    sub_lat = int(((lat % 1) / 1) * 24)
    return (chr(A + field_lon) + chr(A + field_lat) +
            str(square_lon) + str(square_lat) +
            chr(A + sub_lon).lower() + chr(A + sub_lat).lower())


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

    # ------------------------------------------------------------------
    # Acumulativos: eventos cuya numeración NO se reinicia por día.
    # El número de participante es continuo sobre todos los CSV del evento.
    # El offset (number_offset en events.json) se suma al conteo para
    # arrancar en un número específico (ej. ya se reportaron 37 -> offset 37,
    # el siguiente será #38).
    # ------------------------------------------------------------------
    @staticmethod
    def _is_cumulative(event):
        return bool(event.get("cumulative", False))

    def _event_dir(self, event_id):
        event_dir = os.path.join(self.data_dir, event_id)
        os.makedirs(event_dir, exist_ok=True)
        return event_dir

    def _iter_event_rows(self, event):
        """Itera todas las filas de todos los CSV del evento (acumulativo)."""
        event_dir = self._event_dir(event["id"])
        for fname in sorted(os.listdir(event_dir)):
            if not fname.endswith(".csv"):
                continue
            path = os.path.join(event_dir, fname)
            with open(path, "r") as f:
                for row in csv.DictReader(f):
                    yield row

    def get_code(self, callsign, event):
        """
        Devuelve el código SOLO si el indicativo ya se reportó HOY.
        En días distintos siempre se permite un nuevo check-in (código nuevo),
        tanto en eventos acumulativos como por-día.
        """
        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return None
        with open(filepath, "r") as f:
            for row in csv.DictReader(f):
                if row["callsign"].upper() == callsign.upper():
                    return row["code"]
        return None

    def checkin(self, callsign, event):
        tz = event.get("timezone_offset", -6)
        date_str = self._get_today_str(tz)
        filepath = self._get_filepath(event["id"], date_str)

        # Solo se bloquea el doble check-in DENTRO del mismo día.
        # En otra fecha siempre puede volver a reportarse.
        existing = self.get_code(callsign, event)
        if existing:
            return existing, self.get_count(event), True

        code = self._generate_code(event, filepath)
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
        """
        Acumulativo: cuenta TODOS los check-ins de todos los CSV del evento
        + el offset configurado (number_offset).
        Por-día: cuenta solo el CSV de hoy.
        """
        if self._is_cumulative(event):
            offset = int(event.get("number_offset", 0))
            total = sum(1 for _ in self._iter_event_rows(event))
            return total + offset

        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return 0
        with open(filepath, "r") as f:
            return sum(1 for _ in csv.DictReader(f))

    def get_list(self, event):
        """Lista de indicativos del CSV de HOY (para LIST)."""
        tz = event.get("timezone_offset", -6)
        filepath = self._get_filepath(event["id"], self._get_today_str(tz))
        if not os.path.exists(filepath):
            return []
        with open(filepath, "r") as f:
            return [row["callsign"] for row in csv.DictReader(f)]

    def _generate_code(self, event, filepath, length=4):
        """
        Genera un código numérico de 4 dígitos (nunca inicia en 0).
        Acumulativo: único contra TODOS los CSV del evento.
        Por-día: único dentro del CSV del día.
        """
        existing_codes = set()
        if self._is_cumulative(event):
            existing_codes = {row["code"] for row in self._iter_event_rows(event)}
        elif os.path.exists(filepath):
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
        self.start_time = time.time()

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
        """Envía el beacon respetando el intervalo (usado al arrancar)."""
        if not self.config.get("beacon_enabled", False):
            return
        now = time.time()
        if now - self.last_beacon < self.config.get("beacon_interval", 1800):
            return
        self._send_beacon_now()

    def _send_beacon_now(self):
        """Construye y envía el beacon inmediatamente, sin chequear intervalo."""
        if not self.config.get("beacon_enabled", False):
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
            self.last_beacon = time.time()
            log.info(f"BEACON enviado: {lat_str}/{lon_str}")
        except Exception as e:
            log.error(f"Error enviando beacon: {e}")

    def _beacon_loop(self):
        """Hilo en segundo plano: emite el beacon cada beacon_interval segundos."""
        interval = self.config.get("beacon_interval", 1800)
        while True:
            time.sleep(interval)
            # Solo emite si hay conexión activa
            if self.ais is not None:
                try:
                    self._send_beacon_now()
                except Exception as e:
                    log.error(f"Error en hilo de beacon: {e}")

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
        if command in ("UPTIME", "UP"):
            self._cmd_uptime(from_call)
            return
        if command in ("HORA", "UTC", "TIME"):
            self._cmd_hora(from_call)
            return
        if command in ("CLIMA", "WX", "TIEMPO"):
            self._cmd_clima(from_call)
            return
        if command in ("GRID", "LOCATOR", "QTH"):
            self._cmd_grid(from_call)
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
                          "Otros cmds: LIST, STATUS, EVENTOS, "
                          "UPTIME, HORA, CLIMA, GRID, INFO, SALIR")

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

    def _cmd_uptime(self, from_call):
        secs = int(time.time() - self.start_time)
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        parts = []
        if d:
            parts.append(f"{d}d")
        if h or d:
            parts.append(f"{h}h")
        parts.append(f"{m}m")
        self.send_message(from_call,
                          f"TICANET activo: {' '.join(parts)}. 73!")

    def _cmd_hora(self, from_call):
        now = datetime.utcnow()
        offset = self.config.get("timezone_offset", -6)
        local = now + timedelta(hours=offset)
        self.send_message(
            from_call,
            f"UTC {now.strftime('%H:%M')} | Local {local.strftime('%H:%M')} "
            f"({now.strftime('%d/%m/%Y')})")

    def _cmd_clima(self, from_call):
        lat = self.config.get("latitude", 0.0)
        lon = self.config.get("longitude", 0.0)
        try:
            url = (f"https://api.open-meteo.com/v1/forecast?"
                   f"latitude={lat}&longitude={lon}"
                   f"&current=temperature_2m,relative_humidity_2m,"
                   f"wind_speed_10m&timezone=auto")
            r = requests.get(url, timeout=10)
            cur = r.json().get("current", {})
            temp = cur.get("temperature_2m", "?")
            hum = cur.get("relative_humidity_2m", "?")
            wind = cur.get("wind_speed_10m", "?")
            self.send_message(
                from_call,
                f"WX Cartago: {temp}C, HR {hum}%, viento {wind}km/h. 73!")
        except Exception as e:
            log.error(f"Error clima: {e}")
            self.send_message(from_call,
                              "No pude obtener el clima ahora. 73!")

    def _cmd_grid(self, from_call):
        lat = self.config.get("latitude", 0.0)
        lon = self.config.get("longitude", 0.0)
        grid = latlon_to_grid(lat, lon)
        self.send_message(from_call,
                          f"TICANET QTH: {grid} ({lat:.4f}, {lon:.4f})")

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

        # Hilo de beacon periódico (se inicia una sola vez)
        if self.config.get("beacon_enabled", False):
            beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True)
            beacon_thread.start()
            log.info(f"Hilo de beacon iniciado (cada "
                     f"{self.config.get('beacon_interval', 1800)}s)")

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
