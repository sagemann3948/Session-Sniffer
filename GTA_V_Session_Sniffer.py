# ------------------------------------------------------
# 🐍 Standard Python Libraries (Included by Default) 🐍
# ------------------------------------------------------
import os
import re
import sys
import ast
import json
import time
import enum
import errno
import signal
import logging
import textwrap
import winsound
import threading
import subprocess
import webbrowser
from pathlib import Path
from operator import attrgetter
from datetime import datetime, timedelta
from traceback import TracebackException
from json.decoder import JSONDecodeError
from types import FrameType, TracebackType
from typing import TypedDict, Optional, Literal, Union, Type, Any
from ipaddress import IPv4Address, AddressValueError
from dataclasses import dataclass

# --------------------------------------------
# 📦 External/Third-party Python Libraries 📦
# --------------------------------------------
import wmi
import psutil
import colorama
import requests
import geoip2.errors
import geoip2.database
from rich.text import Text
from rich.console import Console
from rich.traceback import Traceback
from wmi import _wmi_namespace, _wmi_object
from prettytable import PrettyTable, TableStyle
from colorama import Fore, Back, Style

# -----------------------------------------------------
# 📚 Local Python Libraries (Included with Project) 📚
# -----------------------------------------------------
from Modules.oui_lookup.oui_lookup import MacLookup
from Modules.capture.capture import PacketCapture, Packet, TSharkCrashException
from Modules.capture.utils import TSharkNotFoundException, InvalidTSharkVersionException, get_tshark_path, is_npcap_or_winpcap_installed
from Modules.https_utils.unsafe_https import s
from Modules.msgbox import MsgBox


if sys.version_info.major <= 3 and sys.version_info.minor < 12:
    print("To use this script, your Python version must be 3.12 or higher.")
    sys.exit(0)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[
        logging.FileHandler("error.log")
        # rich.traceback does it nicer ---> logging.StreamHandler(sys.stdout)
    ]
)
logging.captureWarnings(True)


@dataclass
class ExceptionInfo:
    exc_type: Type[BaseException]
    exc_value: BaseException
    exc_traceback: Optional[TracebackType]

def terminate_script(
        terminate_method: Literal["EXIT", "SIGINT", "THREAD_RAISED"],
        msgbox_crash_text: Optional[str] = None,
        stdout_crash_text: Optional[str] = None,
        exception_info: Optional[ExceptionInfo] = None,
        terminate_gracefully = True,
        force_terminate_errorlevel: Union[int, Literal[False]] = False
    ):

    def should_terminate_gracefully():
        if terminate_gracefully is False:
            return False

        for thread_name in ["capture_core__thread", "rendering_core__thread", "iplookup_core__thread"]:
            if thread_name in globals():
                thread = globals()[thread_name]
                if isinstance(thread, threading.Thread):
                    if thread.is_alive():
                        return False

        # TODO: Gracefully exit the script even when the `cature` module is running.
        if "capture" in globals():
            if capture is not None and isinstance(capture, PacketCapture):
                return False

        return True

    ScriptControl.set_crashed(None if stdout_crash_text is None else f"\n\n{stdout_crash_text}\n")

    if exception_info:
        logging.error("Uncaught exception", exc_info=(exception_info.exc_type, exception_info.exc_value, exception_info.exc_traceback))

        console = Console()

        traceback_message = Traceback.from_exception(exception_info.exc_type, exception_info.exc_value, exception_info.exc_traceback)
        console.print(traceback_message)

        error_message = Text.from_markup(
            (
                "\n\n\nAn unexpected (uncaught) error occurred. [bold]Please kindly report it to:[/bold]\n"
                "[link=https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/issues]"
                "https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/issues[/link].\n\n"
                "DEBUG:\n"
                f"VERSION={globals().get("VERSION", "Unknown Version")}" # Define a default value for VERSION if it's not defined
            ),
            style="white"
        )
        console.print(error_message)

    if stdout_crash_text is not None:
        print(ScriptControl.get_message())

    if msgbox_crash_text is not None:
        msgbox_title = TITLE
        msgbox_message = msgbox_crash_text
        msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Critical | MsgBox.Style.SystemModal | MsgBox.Style.MsgBoxSetForeground

        MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
        time.sleep(1)

    time.sleep(3)

    if should_terminate_gracefully():
        if force_terminate_errorlevel is False:
            errorlevel = 1 if terminate_method == "THREAD_RAISED" else 0
        else:
            errorlevel = force_terminate_errorlevel
        sys.exit(errorlevel)

    terminate_process_tree()

def handle_exception(exc_type: Type[BaseException], exc_value: BaseException, exc_traceback: Optional[TracebackException]):
    """Handles exceptions for the main script. (not threads)"""
    if issubclass(exc_type, KeyboardInterrupt):
        return

    exception_info = ExceptionInfo(exc_type, exc_value, exc_traceback)
    terminate_script("EXIT", "An unexpected (uncaught) error occurred.\n\nPlease kindly report it to:\nhttps://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/issues", exception_info = exception_info)

def signal_handler(sig: int, frame: FrameType):
    if sig == 2: # means CTRL+C pressed
        if not ScriptControl.has_crashed(): # Block CTRL+C if script is already crashing under control
            print(f"\n{Fore.YELLOW}Ctrl+C pressed. Exiting script ...{Fore.RESET}")
            terminate_script("SIGINT")

sys.excepthook = handle_exception
signal.signal(signal.SIGINT, signal_handler)


class InvalidBooleanValueError(Exception):
    pass

class InvalidNoneTypeValueError(Exception):
    pass

class InvalidFileError(Exception):
    def __init__(self, path: str):
        super().__init__(f'The path does not point to a regular file: "{path}"')

class PacketCaptureOverflow(Exception):
    pass

class ScriptControl:
    _lock = threading.Lock()
    _crashed = False
    _message = None

    @classmethod
    def set_crashed(cls, message: Optional[str] = None):
        with cls._lock:
            cls._crashed = True
            cls._message = message

    @classmethod
    def reset_crashed(cls):
        with cls._lock:
            cls._crashed = False
            cls._message = None

    @classmethod
    def has_crashed(cls):
        with cls._lock:
            return cls._crashed

    @classmethod
    def get_message(cls):
        with cls._lock:
            return cls._message

class Version:
    def __init__(self, version: str):
        self.major, self.minor, self.patch = map(int, version[1:6:2])
        self.date = datetime.strptime(version[9:19], "%d/%m/%Y").date().strftime("%d/%m/%Y")

        # Check if the version string contains the time component
        if (
            len(version) == 27
            and re.search(r" \((\d{2}:\d{2})\)$", version)
        ):
            self.time = datetime.strptime(version[21:26], "%H:%M").time().strftime("%H:%M")
            self._date_time = datetime.strptime(version[9:27], "%d/%m/%Y (%H:%M)")
        else:
            self.time = None
            self._date_time = datetime.strptime(version[9:19], "%d/%m/%Y")

    def __str__(self):
        return f"v{self.major}.{self.minor}.{self.patch} - {self.date}{f' ({self.time})' if self.time else ''}"

class Updater:
    def __init__(self, current_version: Version):
        self.current_version = current_version

    def check_for_update(self, latest_version: Version):
        # Check if the latest version is newer than the current version
        if (latest_version.major, latest_version.minor, latest_version.patch) > (self.current_version.major, self.current_version.minor, self.current_version.patch):
            return True
        elif (latest_version.major, latest_version.minor, latest_version.patch) == (self.current_version.major, self.current_version.minor, self.current_version.patch):
            # Compare date and time if versioning is equal
            if latest_version._date_time > self.current_version._date_time:
                return True
        return False

class Threads_ExceptionHandler:
    """In Python, threads cannot be raised within the main source code. When raised, they operate independently,
    and the main process continues execution without halting for the thread's completion. To overcome this limitation,
    this class is designed to enhance thread management and provide additional functionality.

    Attributes:
        raising_function (str): The name of the function where the exception was raised.
        raising_exc_type (type): The type of the exception raised.
        raising_exc_value (Exception): The value of the exception raised.
        raising_exc_traceback (TracebackType): The traceback information of the exception raised.
    """
    raising_function = None
    raising_exc_type = None
    raising_exc_value = None
    raising_exc_traceback = None

    def __init__(self):
        pass

    def __enter__(self):
        pass

    def __exit__(self, exc_type: type, exc_value: Exception, exc_traceback: TracebackType):
        """
        Exit method called upon exiting the 'with' block.

        Args:
            exc_type: The type of the exception raised.
            exc_value: The value of the exception raised.
            exc_traceback: The traceback information of the exception raised.

        Returns:
            bool: True to suppress the exception from propagating further.
        """
        if exc_type:
            Threads_ExceptionHandler.raising_exc_type = exc_type
            Threads_ExceptionHandler.raising_exc_value = exc_value
            Threads_ExceptionHandler.raising_exc_traceback = exc_traceback

            tb = exc_traceback
            while tb.tb_next:
                tb = tb.tb_next
            # Set the failed function name
            Threads_ExceptionHandler.raising_function = tb.tb_frame.f_code.co_name

            exception_info = ExceptionInfo(exc_type, exc_value, exc_traceback)
            terminate_script("THREAD_RAISED", "An unexpected (uncaught) error occurred.\n\nPlease kindly report it to:\nhttps://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/issues", exception_info = exception_info)

            return True  # Prevent exceptions from propagating

class DefaultSettings:
    """Class containing default setting values."""
    CAPTURE_TSHARK_PATH = None
    CAPTURE_NETWORK_INTERFACE_CONNECTION_PROMPT = True
    CAPTURE_INTERFACE_NAME = None
    CAPTURE_IP_ADDRESS = None
    CAPTURE_MAC_ADDRESS = None
    CAPTURE_ARP = True
    CAPTURE_BLOCK_THIRD_PARTY_SERVERS = True
    CAPTURE_PROGRAM_PRESET = None
    CAPTURE_VPN_MODE = False
    CAPTURE_OVERFLOW_TIMER = 3.0
    CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER = None
    CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER = None
    STDOUT_SHOW_ADVERTISING_HEADER = True
    STDOUT_SESSIONS_LOGGING = True
    STDOUT_RESET_PORTS_ON_REJOINS = True
    STDOUT_FIELDS_TO_HIDE = ["Intermediate Ports", "First Port", "Continent", "R. Code", "City", "District", "ZIP Code", "Lat", "Lon", "Time Zone", "Offset", "Currency", "Organization", "ISP", "AS", "ASN"]
    STDOUT_DATE_FIELDS_SHOW_DATE = False
    STDOUT_DATE_FIELDS_SHOW_TIME = False
    STDOUT_DATE_FIELDS_SHOW_ELAPSED = True
    STDOUT_FIELD_SHOW_COUNTRY_CODE = True
    STDOUT_FIELD_SHOW_CONTINENT_CODE = True
    STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY = "Last Rejoin"
    STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY = "Last Seen"
    STDOUT_FIELD_COUNTRY_MAX_LEN = 20
    STDOUT_FIELD_CITY_MAX_LEN = 20
    STDOUT_FIELD_CONTINENT_MAX_LEN = 20
    STDOUT_FIELD_REGION_MAX_LEN = 20
    STDOUT_FIELD_ORGANIZATION_MAX_LEN = 20
    STDOUT_FIELD_ISP_MAX_LEN = 20
    STDOUT_FIELD_ASN_ISP_MAX_LEN = 20
    STDOUT_FIELD_AS_MAX_LEN = 20
    STDOUT_FIELD_AS_NAME_MAX_LEN = 20
    STDOUT_DISCONNECTED_PLAYERS_TIMER = 10.0
    STDOUT_DISCONNECTED_PLAYERS_COUNTER = 6
    USERIP_ENABLED = True
    DISCORD_PRESENCE = True

class Settings(DefaultSettings):
    stdout_fields_mapping = {
        "First Seen": "datetime.first_seen",
        "Last Rejoin": "datetime.last_rejoin",
        "Last Seen": "datetime.last_seen",
        "Usernames": "userip.usernames",
        "Rejoins": "rejoins",
        "T. Packets": "total_packets",
        "Packets": "packets",
        "PPS": "pps.rate",
        "IP Address": "ip",
        "Last Port": "ports.last",
        "Intermediate Ports": "ports.intermediate",
        "First Port": "ports.first",
        "Continent": "iplookup.ipapi.compiled.continent",
        "Country": "iplookup.maxmind.compiled.country",
        "Region": "iplookup.ipapi.compiled.region",
        "R. Code": "iplookup.ipapi.compiled.region_code",
        "City": "iplookup.maxmind.compiled.city",
        "District": "iplookup.ipapi.compiled.district",
        "ZIP Code": "iplookup.ipapi.compiled.zip_code",
        "Lat": "iplookup.ipapi.compiled.lat",
        "Lon": "iplookup.ipapi.compiled.lon",
        "Time Zone": "iplookup.ipapi.compiled.time_zone",
        "Offset": "iplookup.ipapi.compiled.offset",
        "Currency": "iplookup.ipapi.compiled.currency",
        "Organization": "iplookup.ipapi.compiled.org",
        "ISP": "iplookup.ipapi.compiled.isp",
        "ASN / ISP": "iplookup.maxmind.compiled.asn",
        "AS": "iplookup.ipapi.compiled._as",
        "ASN": "iplookup.ipapi.compiled.as_name",
        "Mobile": "iplookup.ipapi.compiled.mobile",
        "VPN": "iplookup.ipapi.compiled.proxy",
        "Hosting": "iplookup.ipapi.compiled.hosting"
    }
    stdout_forced_fields = ["First Seen", "Last Rejoin", "Last Seen", "Usernames", "Rejoins", "T. Packets", "Packets", "PPS", "IP Address"]
    stdout_hideable_fields = ["Last Port", "Intermediate Ports", "First Port", "Continent", "Country", "Region", "R. Code", "City", "District", "ZIP Code", "Lat", "Lon", "Time Zone", "Offset", "Currency", "Organization", "ISP", "ASN / ISP", "AS", "ASN", "Mobile", "VPN", "Hosting"]
    stdout_all_connected_fields = [field for field in stdout_forced_fields if field != "Last Seen"] + stdout_hideable_fields
    stdout_all_disconnected_fields = [field for field in stdout_forced_fields if field != "PPS"] + stdout_hideable_fields

    @classmethod
    def iterate_over_settings(cls):
        _allowed_settings_types = (type(None), Path, bool, list, str, float, int)

        for attr_name, attr_value in vars(DefaultSettings).items():
            if (
                callable(attr_value)
                or attr_name.startswith("_")
                or attr_name in ["stdout_fields_mapping", "stdout_forced_fields", "stdout_hideable_fields", "stdout_all_connected_fields", "stdout_all_disconnected_fields"]
                or not attr_name.isupper()
                or not isinstance(attr_value, _allowed_settings_types)
            ):
                continue

            # Get the value from Settings if it exists, otherwise from DefaultSettings
            current_value = getattr(cls, attr_name, attr_value)
            yield attr_name, current_value

    @classmethod
    def get_settings_length(cls):
        return sum(1 for _ in cls.iterate_over_settings())

    @classmethod
    def has_setting(cls, setting_name):
        return hasattr(cls, setting_name)

    @staticmethod
    def reconstruct_settings():
        print("\nCorrect reconstruction of \"Settings.ini\" ...")
        text = textwrap.dedent(f"""
            ;;-----------------------------------------------------------------------------
            ;; Session Sniffer Configuration Settings
            ;;-----------------------------------------------------------------------------
            ;; Lines starting with \";\" or \"#\" symbols are commented lines.
            ;;
            ;; For detailed explanations of each setting, please refer to the following documentation:
            ;; https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/?tab=readme-ov-file#editing-settings
            ;;-----------------------------------------------------------------------------
        """.removeprefix("\n"))
        for setting_name, setting_value in Settings.iterate_over_settings():
            text += f"{setting_name}={setting_value}\n"
        SETTINGS_PATH.write_text(text, encoding="utf-8")

    @staticmethod
    def load_from_settings_file(settings_path: Path):
        matched_settings_count = 0

        try:
            settings, need_rewrite_settings = parse_settings_ini_file(settings_path, values_handling="first")
            settings: dict[str, str]
        except FileNotFoundError:
            need_rewrite_settings = True
        else:
            for setting_name, setting_value in settings.items():
                if not Settings.has_setting(setting_name):
                    need_rewrite_settings = True
                    continue

                matched_settings_count += 1
                need_rewrite_current_setting = False

                if setting_name == "CAPTURE_TSHARK_PATH":
                    try:
                        Settings.CAPTURE_TSHARK_PATH, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        stripped__setting_value = setting_value.strip("\"'")
                        if not setting_value == stripped__setting_value:
                            need_rewrite_settings = True
                        Settings.CAPTURE_TSHARK_PATH = Path(stripped__setting_value.replace("\\", "/"))
                elif setting_name == "CAPTURE_NETWORK_INTERFACE_CONNECTION_PROMPT":
                    try:
                        Settings.CAPTURE_NETWORK_INTERFACE_CONNECTION_PROMPT, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "CAPTURE_INTERFACE_NAME":
                    try:
                        Settings.CAPTURE_INTERFACE_NAME, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        Settings.CAPTURE_INTERFACE_NAME = setting_value
                elif setting_name == "CAPTURE_IP_ADDRESS":
                    try:
                        Settings.CAPTURE_IP_ADDRESS, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        if is_ipv4_address(setting_value):
                            Settings.CAPTURE_IP_ADDRESS = setting_value
                        else:
                            need_rewrite_settings = True
                elif setting_name == "CAPTURE_MAC_ADDRESS":
                    try:
                        Settings.CAPTURE_MAC_ADDRESS, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        if is_mac_address(setting_value):
                            formatted_mac_address = format_mac_address(setting_value)
                            if not formatted_mac_address == setting_value:
                                need_rewrite_settings = True
                            Settings.CAPTURE_MAC_ADDRESS = formatted_mac_address
                        else:
                            need_rewrite_settings = True
                elif setting_name == "CAPTURE_ARP":
                    try:
                        Settings.CAPTURE_ARP, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "CAPTURE_BLOCK_THIRD_PARTY_SERVERS":
                    try:
                        Settings.CAPTURE_BLOCK_THIRD_PARTY_SERVERS, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "CAPTURE_PROGRAM_PRESET":
                    try:
                        Settings.CAPTURE_PROGRAM_PRESET, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(setting_value, ["GTA5", "Minecraft"])
                        if case_insensitive_match:
                            Settings.CAPTURE_PROGRAM_PRESET = normalized_match
                            if not case_sensitive_match:
                                need_rewrite_current_setting = True
                        else:
                            need_rewrite_settings = True
                elif setting_name == "CAPTURE_VPN_MODE":
                    try:
                        Settings.CAPTURE_VPN_MODE, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "CAPTURE_OVERFLOW_TIMER":
                    try:
                        CAPTURE_OVERFLOW_TIMER = float(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if CAPTURE_OVERFLOW_TIMER >= 1:
                            Settings.CAPTURE_OVERFLOW_TIMER = CAPTURE_OVERFLOW_TIMER
                        else:
                            need_rewrite_settings = True
                elif setting_name == "CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER":
                    try:
                        Settings.CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        if updated_setting_value := setting_value.strip().strip("\"'").strip("()"):
                            Settings.CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER = f"({updated_setting_value})"
                        else:
                            need_rewrite_settings = True
                elif setting_name == "CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER":
                    try:
                        Settings.CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER, need_rewrite_current_setting = custom_str_to_nonetype(setting_value)
                    except InvalidNoneTypeValueError:
                        if updated_setting_value := setting_value.strip().strip("\"'").strip("()"):
                            Settings.CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER = f"({updated_setting_value})"
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_SHOW_ADVERTISING_HEADER":
                    try:
                        Settings.STDOUT_SHOW_ADVERTISING_HEADER, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_SESSIONS_LOGGING":
                    try:
                        Settings.STDOUT_SESSIONS_LOGGING, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_RESET_PORTS_ON_REJOINS":
                    try:
                        Settings.STDOUT_RESET_PORTS_ON_REJOINS, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELDS_TO_HIDE":
                    try:
                        stdout_fields_to_hide: list[str] = ast.literal_eval(setting_value)
                    except (ValueError, SyntaxError):
                        need_rewrite_settings = True
                    else:
                        if isinstance(stdout_fields_to_hide, list) and all(isinstance(item, str) for item in stdout_fields_to_hide):
                            filtered_stdout_fields_to_hide: list[str] = []

                            for value in stdout_fields_to_hide:
                                case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(value, Settings.stdout_hideable_fields)
                                if case_insensitive_match:
                                    filtered_stdout_fields_to_hide.append(normalized_match)
                                    if not case_sensitive_match:
                                        need_rewrite_current_setting = True
                                else:
                                    need_rewrite_settings = True

                            Settings.STDOUT_FIELDS_TO_HIDE = filtered_stdout_fields_to_hide
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_DATE_FIELDS_SHOW_DATE":
                    try:
                        Settings.STDOUT_DATE_FIELDS_SHOW_DATE, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_DATE_FIELDS_SHOW_TIME":
                    try:
                        Settings.STDOUT_DATE_FIELDS_SHOW_TIME, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_DATE_FIELDS_SHOW_ELAPSED":
                    try:
                        Settings.STDOUT_DATE_FIELDS_SHOW_ELAPSED, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_SHOW_CONTINENT_CODE":
                    try:
                        Settings.STDOUT_FIELD_SHOW_CONTINENT_CODE, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_SHOW_COUNTRY_CODE":
                    try:
                        Settings.STDOUT_FIELD_SHOW_COUNTRY_CODE, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY":
                    case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(setting_value, Settings.stdout_all_connected_fields)
                    if case_insensitive_match:
                        Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY = normalized_match
                        if not case_sensitive_match:
                            need_rewrite_current_setting = True
                    else:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY":
                    case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(setting_value, Settings.stdout_all_disconnected_fields)
                    if case_insensitive_match:
                        Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY = normalized_match
                        if not case_sensitive_match:
                            need_rewrite_current_setting = True
                    else:
                        need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_COUNTRY_MAX_LEN":
                    try:
                        stdout_field_country_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_country_max_len >= 1:
                            Settings.STDOUT_FIELD_COUNTRY_MAX_LEN = stdout_field_country_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_CONTINENT_MAX_LEN":
                    try:
                        stdout_field_continent_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_continent_max_len >= 1:
                            Settings.STDOUT_FIELD_CONTINENT_MAX_LEN = stdout_field_continent_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_REGION_MAX_LEN":
                    try:
                        stdout_field_region_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_region_max_len >= 1:
                            Settings.STDOUT_FIELD_REGION_MAX_LEN = stdout_field_region_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_CITY_MAX_LEN":
                    try:
                        stdout_field_city_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_city_max_len >= 1:
                            Settings.STDOUT_FIELD_CITY_MAX_LEN = stdout_field_city_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_ORGANIZATION_MAX_LEN":
                    try:
                        stdout_field_organization_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_organization_max_len >= 1:
                            Settings.STDOUT_FIELD_ORGANIZATION_MAX_LEN = stdout_field_organization_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_ISP_MAX_LEN":
                    try:
                        stdout_field_isp_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_isp_max_len >= 1:
                            Settings.STDOUT_FIELD_ISP_MAX_LEN = stdout_field_isp_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_ASN_ISP_MAX_LEN":
                    try:
                        stdout_field_asn_isp_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_asn_isp_max_len >= 1:
                            Settings.STDOUT_FIELD_ASN_ISP_MAX_LEN = stdout_field_asn_isp_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_AS_MAX_LEN":
                    try:
                        stdout_field_as_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_as_max_len >= 1:
                            Settings.STDOUT_FIELD_AS_MAX_LEN = stdout_field_as_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_FIELD_AS_NAME_MAX_LEN":
                    try:
                        stdout_field_as_name_max_len = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_field_as_name_max_len >= 1:
                            Settings.STDOUT_FIELD_AS_NAME_MAX_LEN = stdout_field_as_name_max_len
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_DISCONNECTED_PLAYERS_TIMER":
                    try:
                        player_disconnected_timer = float(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if player_disconnected_timer >= 3.0:
                            Settings.STDOUT_DISCONNECTED_PLAYERS_TIMER = player_disconnected_timer
                        else:
                            need_rewrite_settings = True
                elif setting_name == "STDOUT_DISCONNECTED_PLAYERS_COUNTER":
                    try:
                        stdout_counter_session_disconnected_players = int(setting_value)
                    except (ValueError, TypeError):
                        need_rewrite_settings = True
                    else:
                        if stdout_counter_session_disconnected_players >= 0:
                            Settings.STDOUT_DISCONNECTED_PLAYERS_COUNTER = stdout_counter_session_disconnected_players
                        else:
                            need_rewrite_settings = True
                elif setting_name == "USERIP_ENABLED":
                    try:
                        Settings.USERIP_ENABLED, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True
                elif setting_name == "DISCORD_PRESENCE":
                    try:
                        Settings.DISCORD_PRESENCE, need_rewrite_current_setting = custom_str_to_bool(setting_value)
                    except InvalidBooleanValueError:
                        need_rewrite_settings = True

                if need_rewrite_current_setting:
                    need_rewrite_settings = True

            if not matched_settings_count == Settings.get_settings_length():
                need_rewrite_settings = True

        if need_rewrite_settings:
            Settings.reconstruct_settings()

        for field_name in Settings.STDOUT_FIELDS_TO_HIDE:
            for sort_field_name, sort_field_value, default_sort_value in [
                ("STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY", Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY, DefaultSettings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY),
                ("STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY", Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY, DefaultSettings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY)
            ]:
                if field_name in sort_field_value:
                    need_rewrite_settings = True

                    msgbox_title = TITLE
                    msgbox_message = textwrap.dedent(f"""
                        ERROR in your custom \"Settings.ini\" file:

                        You cannot sort players in the output from a hidden stdout field (STDOUT_FIELDS_TO_HIDE).

                        Would you like to replace:
                        {sort_field_name}={sort_field_value}
                        with its default value:
                        {sort_field_name}={default_sort_value}
                    """.removeprefix("\n").removesuffix("\n"))
                    msgbox_style = MsgBox.Style.YesNo | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
                    errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)

                    if errorlevel != MsgBox.ReturnValues.IDYES:
                        terminate_script("EXIT")

                    setattr(Settings, sort_field_name, getattr(DefaultSettings, sort_field_name)) # Replace the incorrect field with its default value
                    Settings.reconstruct_settings()

        if Settings.STDOUT_DATE_FIELDS_SHOW_DATE is False and Settings.STDOUT_DATE_FIELDS_SHOW_TIME is False and Settings.STDOUT_DATE_FIELDS_SHOW_ELAPSED is False:
            msgbox_title = TITLE
            msgbox_message = textwrap.dedent(f"""
                ERROR in your custom \"Settings.ini\" file:

                At least one of these settings must be set to \"True\" value:
                <STDOUT_DATE_FIELDS_SHOW_DATE>
                <STDOUT_DATE_FIELDS_SHOW_TIME>
                <STDOUT_DATE_FIELDS_SHOW_ELAPSED>

                Would you like to apply their default values and continue?
            """.removeprefix("\n").removesuffix("\n"))
            msgbox_style = MsgBox.Style.YesNo | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
            errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)

            if errorlevel != MsgBox.ReturnValues.IDYES:
                terminate_script("EXIT")

            for setting_name in ["STDOUT_DATE_FIELDS_SHOW_DATE", "STDOUT_DATE_FIELDS_SHOW_TIME", "STDOUT_DATE_FIELDS_SHOW_ELAPSED"]:
                setattr(Settings, setting_name, getattr(DefaultSettings, setting_name))

            Settings.reconstruct_settings()

class Adapter_Properties:
    def __init__(self,
        interface_index = "N/A",
        manufacturer    = "N/A"
    ):
        self.InterfaceIndex: Union[Literal["N/A"], int] = interface_index
        self.Manufacturer  : Union[Literal["N/A"], str] = manufacturer

class Interface:
    all_interfaces: list["Interface"] = []

    def __init__(self, name: str):
        self.name = name

        self.ip_addresses     : list[str]                       = []
        self.mac_address      : Union[Literal["N/A"], str]      = "N/A"
        self.organization_name: Union[Literal["N/A"], str]      = "N/A"
        self.packets_sent     : Union[Literal["N/A"], int]      = "N/A"
        self.packets_recv     : Union[Literal["N/A"], int]      = "N/A"
        self.arp_infos        : dict[str, list[dict[str, str]]] = {}

        self.adapter_properties = Adapter_Properties()

        Interface.all_interfaces.append(self)

    def add_arp_infos(self, ip_address: str, arp_infos: list[dict[str, str]]):
        """
        Add ARP informations for the given interface IP address.
        """
        if self.arp_infos.get(ip_address) is None:
            self.arp_infos[ip_address] = []

        for arp_info in arp_infos:
            if arp_info not in self.arp_infos[ip_address]:
                self.arp_infos[ip_address].append(arp_info)

    def get_arp_infos(self, ip_address: str):
        """
        Get ARP informations for the given interface IP address.
        """
        return self.arp_infos.get(ip_address)

    @classmethod
    def get_interface_by_name(cls, interface_name: str):
        for interface in cls.all_interfaces:
            if interface.name == interface_name:
                return interface
        return None

    @classmethod
    def get_interface_by_id(cls, interface_index: int):
        for interface in cls.all_interfaces:
            if interface.adapter_properties.InterfaceIndex == interface_index:
                return interface
        return None

class InterfaceOptionData(TypedDict):
    is_arp: bool
    interface: str
    ip_address: Optional[str]
    mac_address: Optional[str]

class ThirdPartyServers(enum.Enum):
    PC_DISCORD = ["66.22.196.0/22", "66.22.200.0/21", "66.22.208.0/20", "66.22.224.0/20", "66.22.240.0/21", "66.22.248.0/24"]
    PC_VALVE = ["103.10.124.0/23", "103.28.54.0/23", "146.66.152.0/21", "155.133.224.0/19", "162.254.192.0/21", "185.25.180.0/22", "205.196.6.0/24"] # Valve = Steam
    PC_GOOGLE = ["34.0.0.0/9", "34.128.0.0/10", "35.184.0.0/13", "35.192.0.0/11", "35.224.0.0/12", "35.240.0.0/13"]
    PC_MULTICAST = ["224.0.0.0/4"]
    PC_UK_MINISTRY_OF_DEFENCE = ["25.0.0.0/8"]
    PC_SERVERS_COM = ["173.237.26.0/24"]
    PC_OTHERS = ["113.117.15.193/32"]
    GTAV_PC_AND_PS3_TAKETWO = ["104.255.104.0/23", "104.255.106.0/24", "185.56.64.0/22", "192.81.241.0/24", "192.81.244.0/23"]
    GTAV_PC_MICROSOFT = ["52.139.128.0/18"]
    GTAV_PC_DOD_NETWORK_INFORMATION_CENTER = ["26.0.0.0/8"]
    GTAV_PC_BATTLEYE = ["51.89.97.102/32", "51.89.99.255/32"]
    GTAV_XBOXONE_MICROSOFT = ["52.159.128.0/17", "52.160.0.0/16", "40.74.0.0/18"]
    PS5_AMAZON = ["52.40.62.0/25"]
    MINECRAFTBEDROCKEDITION_PC_AND_PS3_MICROSOFT = ["20.202.0.0/24", "20.224.0.0/16", "168.61.142.128/25", "168.61.143.0/24", "168.61.144.0/20", "168.61.160.0/19"]

class Player_PPS:
    def __init__(self, packet_datetime: datetime):
        self._initialize(packet_datetime)

    def _initialize(self, packet_datetime: datetime):
        self.t1 = packet_datetime
        self.counter = 0
        self.rate = 0
        self.is_first_calculation = True

    def reset(self, packet_datetime: datetime):
        self._initialize(packet_datetime)

class Player_Ports:
    def __init__(self, port: int):
        self._initialize(port)

    def _initialize(self, port: int):
        self.list = [port]
        self.first = port
        self.intermediate: list[int] = []
        self.last = port

    def reset(self, port: int):
        self._initialize(port)

class Player_DateTime:
    def __init__(self, packet_datetime: datetime):
        self._initialize(packet_datetime)

    def _initialize(self, packet_datetime: datetime):
        self.first_seen = packet_datetime
        self.last_rejoin = packet_datetime
        self.last_seen = packet_datetime
        self.left: Optional[datetime] = None

    def reset(self, packet_datetime: datetime):
        self._initialize(packet_datetime)

class MaxMind_GeoLite2_Compiled:
    def __init__(self):
        self.country       = "..."
        self.country_code  = "..."
        self.country_short = "..."
        self.city          = "..."
        self.city_short    = "..."
        self.asn           = "..."
        self.asn_short     = "..."

class MaxMind_GeoLite2:
    def __init__(self):
        self.is_initialized = False
        self.compiled = MaxMind_GeoLite2_Compiled()

        self.country:      Optional[str] = None
        self.country_code: Optional[str] = None
        self.city:         Optional[str] = None
        self.asn:          Optional[str] = None

    def compile(self):
        """
        Populate the `compiled` subclass with processed values where `None` is replaced with "..."
        and all other values are converted to their string representation.
        """
        self.compiled.country        = "..." if self.country      is None else str(self.country)
        self.compiled.country_code   = "..." if self.country_code is None else str(self.country_code)
        self.compiled.country_short  = "..." if self.country      is None else truncate_with_ellipsis(str(self.country), Settings.STDOUT_FIELD_COUNTRY_MAX_LEN)
        self.compiled.city           = "..." if self.city         is None else str(self.city)
        self.compiled.city_short     = "..." if self.city         is None else truncate_with_ellipsis(str(self.city), Settings.STDOUT_FIELD_CITY_MAX_LEN)
        self.compiled.asn            = "..." if self.asn          is None else str(self.asn)
        self.compiled.asn_short      = "..." if self.asn          is None else truncate_with_ellipsis(str(self.asn), Settings.STDOUT_FIELD_ASN_ISP_MAX_LEN)

class IPAPI_Compiled:
    def __init__(self):
        self.continent      = "..."
        self.continent_code = "..."
        self.country        = "..."
        self.country_code   = "..."
        self.region         = "..."
        self.region_short   = "..."
        self.region_code    = "..."
        self.city           = "..."
        self.district       = "..."
        self.zip_code       = "..."
        self.lat            = "..."
        self.lon            = "..."
        self.time_zone      = "..."
        self.offset         = "..."
        self.currency       = "..."
        self.org            = "..."
        self.org_short      = "..."
        self.isp            = "..."
        self.isp_short      = "..."
        self._as            = "..."
        self.as_short       = "..."
        self.as_name        = "..."
        self.as_name_short  = "..."
        self.mobile         = "..."
        self.proxy          = "..."
        self.hosting        = "..."


class IPAPI:
    def __init__(self):
        self.is_initialized = False
        self.compiled = IPAPI_Compiled()

        self.continent:      Optional[Union[Literal["N/A"], str]]               = None
        self.continent_code: Optional[Union[Literal["N/A"], str]]               = None
        self.country:        Optional[Union[Literal["N/A"], str]]               = None
        self.country_code:   Optional[Union[Literal["N/A"], str]]               = None
        self.region:         Optional[Union[Literal["N/A"], str]]               = None
        self.region_code:    Optional[Union[Literal["N/A"], str]]               = None
        self.city:           Optional[Union[Literal["N/A"], str]]               = None
        self.district:       Optional[Union[Literal["N/A"], str]]               = None
        self.zip_code:       Optional[Union[Literal["N/A"], str]]               = None
        self.lat:            Optional[Union[Literal["N/A"], Union[float, int]]] = None
        self.lon:            Optional[Union[Literal["N/A"], Union[float, int]]] = None
        self.time_zone:      Optional[Union[Literal["N/A"], str]]               = None
        self.offset:         Optional[Union[Literal["N/A"], int]]               = None
        self.currency:       Optional[Union[Literal["N/A"], str]]               = None
        self.org:            Optional[Union[Literal["N/A"], str]]               = None
        self.isp:            Optional[Union[Literal["N/A"], str]]               = None
        self._as:            Optional[Union[Literal["N/A"], str]]               = None
        self.as_name:        Optional[Union[Literal["N/A"], str]]               = None
        self.mobile:         Optional[Union[Literal["N/A"], bool]]              = None
        self.proxy:          Optional[Union[Literal["N/A"], bool]]              = None
        self.hosting:        Optional[Union[Literal["N/A"], bool]]              = None

    def compile(self):
        """
        Populate the `compiled` subclass with processed values where `None` is replaced with "..."
        and all other values are converted to their string representation.
        """
        self.compiled.continent      = "..." if self.continent      is None else str(self.continent)
        self.compiled.continent_code = "..." if self.continent_code is None else str(self.continent_code)
        self.compiled.country        = "..." if self.country        is None else str(self.country)
        self.compiled.country_code   = "..." if self.country_code   is None else str(self.country_code)
        self.compiled.region         = "..." if self.region         is None else str(self.region)
        self.compiled.region_short   = "..." if self.region         is None else truncate_with_ellipsis(str(self.region), Settings.STDOUT_FIELD_REGION_MAX_LEN)
        self.compiled.region_code    = "..." if self.region_code    is None else str(self.region_code)
        self.compiled.city           = "..." if self.city           is None else str(self.city)
        self.compiled.district       = "..." if self.district       is None else str(self.district)
        self.compiled.zip_code       = "..." if self.zip_code       is None else str(self.zip_code)
        self.compiled.lat            = "..." if self.lat            is None else str(self.lat)
        self.compiled.lon            = "..." if self.lon            is None else str(self.lon)
        self.compiled.time_zone      = "..." if self.time_zone      is None else str(self.time_zone)
        self.compiled.offset         = "..." if self.offset         is None else str(self.offset)
        self.compiled.currency       = "..." if self.currency       is None else str(self.currency)
        self.compiled.org            = "..." if self.org            is None else str(self.org)
        self.compiled.org_short      = "..." if self.org            is None else truncate_with_ellipsis(str(self.org), Settings.STDOUT_FIELD_ORGANIZATION_MAX_LEN)
        self.compiled.isp            = "..." if self.isp            is None else str(self.isp)
        self.compiled.isp_short      = "..." if self.isp            is None else truncate_with_ellipsis(str(self.isp), Settings.STDOUT_FIELD_ISP_MAX_LEN)
        self.compiled._as            = "..." if self._as            is None else str(self._as)
        self.compiled.as_short       = "..." if self._as            is None else truncate_with_ellipsis(str(self._as), Settings.STDOUT_FIELD_AS_MAX_LEN)
        self.compiled.as_name        = "..." if self.as_name        is None else str(self.as_name)
        self.compiled.as_name_short  = "..." if self.as_name        is None else truncate_with_ellipsis(str(self.as_name), Settings.STDOUT_FIELD_AS_NAME_MAX_LEN)
        self.compiled.mobile         = "..." if self.mobile         is None else str(self.mobile)
        self.compiled.proxy          = "..." if self.proxy          is None else str(self.proxy)
        self.compiled.hosting        = "..." if self.hosting        is None else str(self.hosting)

class Player_IPLookup:
    def __init__(self):
        self.maxmind = MaxMind_GeoLite2()
        self.ipapi = IPAPI()

class Player_Detection:
    def __init__(self):
        self.type: Optional[Literal["Static IP"]] = None
        self.time: Optional[str] = None
        self.date_time: Optional[str] = None
        self.as_processed_userip_task = False

class Player_UserIp:
    def __init__(self):
        self._initialize()

    def _initialize(self):
        self.database_name: Optional[str] = None
        self.settings: Optional[UserIP_Settings] = None
        self.usernames: list[str] = []
        self.detection = Player_Detection()

    def reset(self):
        self._initialize()

class Player_ModMenus:
    def __init__(self):
        self.usernames: list[str] = []

class Player:
    def __init__(self, ip: str, port: int, packet_datetime: datetime):
        self.is_player_just_registered = True
        self._initialize(ip, port, packet_datetime)

    def _initialize(self, ip: str, port: int, packet_datetime: datetime):
        self.ip = ip
        self.rejoins = 0
        self.packets = 1
        self.total_packets = 1
        self.usernames: list[str] = []

        self.pps = Player_PPS(packet_datetime)
        self.ports = Player_Ports(port)
        self.datetime = Player_DateTime(packet_datetime)
        self.iplookup = Player_IPLookup()
        self.userip = Player_UserIp()
        self.mod_menus = Player_ModMenus()

    def reset(self, port: int, packet_datetime: datetime):
        self.packets = 1
        self.pps.reset(packet_datetime)
        self.ports.reset(port)
        self.datetime.reset(packet_datetime)

class PlayersRegistry:
    players_registry: dict[str, Player] = {}

    @classmethod
    def add_player(cls, player: Player):
        if player.ip in cls.players_registry:
            raise ValueError(f"Player with IP \"{player.ip}\" already exists.")
        cls.players_registry[player.ip] = player
        return player

    @classmethod
    def get_player(cls, ip: str):
        return cls.players_registry.get(ip)

    @classmethod
    def iterate_players_from_registry(cls):
        # Using list() ensures a static snapshot of the dictionary's values is used, avoiding the 'RuntimeError: dictionary changed size during iteration'.
        for player in list(cls.players_registry.values()):
            yield player

class IPLookup:
    lock =  threading.Lock()
    _lock_pending_ips = threading.Lock()
    _lock_results_ips = threading.Lock()

    _pending_ips_for_lookup: list[str] = []
    _results_ips_for_lookup: dict[str, IPAPI] = {}

    @classmethod
    def add_pending_ip(cls, ip: str):
        with cls._lock_pending_ips:
            if ip in cls._pending_ips_for_lookup:
                raise ValueError(f"IP address '{ip}' is already in the pending IP lookup list.")
            cls._pending_ips_for_lookup.append(ip)

    @classmethod
    def remove_pending_ip(cls, ip: str):
        with cls._lock_pending_ips:
            if ip in cls._pending_ips_for_lookup:
                cls._pending_ips_for_lookup.remove(ip)

    @classmethod
    def get_pending_ips(cls):
        with cls._lock_pending_ips:
            return cls._pending_ips_for_lookup

    @classmethod
    def get_pending_ips_slice(cls, start: int, end: int):
        with cls._lock_pending_ips:
            return cls._pending_ips_for_lookup[start:end]

    @classmethod
    def update_results(cls, ip: str, result: IPAPI):
        with cls._lock_results_ips:
            cls._results_ips_for_lookup[ip] = result

    @classmethod
    def get_results(cls, ip: str):
        with cls._lock_results_ips:
            return cls._results_ips_for_lookup.get(ip)

    @classmethod
    def ip_in_pending(cls, ip: str):
        with cls._lock_pending_ips:
            return ip in cls._pending_ips_for_lookup

    @classmethod
    def ip_in_results(cls, ip: str):
        with cls._lock_results_ips:
            return ip in cls._results_ips_for_lookup

    @classmethod
    def ip_exists(cls, ip: str):
        with cls._lock_pending_ips:
            if ip in cls._pending_ips_for_lookup:
                return "pending"
        with cls._lock_results_ips:
            if  ip in cls._results_ips_for_lookup:
                return "results"
        return "not found"

class SessionHost:
    player = None
    search_player = False
    players_pending_for_disconnection = []

    @staticmethod
    def get_host_player(session_connected: list[Player]):
        connected_players: list[Player] = take(2, sorted(session_connected, key=attrgetter("datetime.last_rejoin")))

        potential_session_host_player = None

        if len(connected_players) == 1:
            potential_session_host_player = connected_players[0]
        elif len(connected_players) == 2:
            time_difference = connected_players[1].datetime.last_rejoin - connected_players[0].datetime.last_rejoin
            if time_difference >= timedelta(milliseconds=200):
                potential_session_host_player = connected_players[0]
        else:
            raise ValueError(f"Unexpected number of connected players: {len(connected_players)}")

        if potential_session_host_player and (
            # Skip players remaining to be disconnected from the previous session.
            potential_session_host_player not in SessionHost.players_pending_for_disconnection
            # Ensures that we only check for the newly joined session's players.
            # The lower this value, the riskier it becomes, as it could potentially flag a player who ultimately isn't part of the newly discovered session.
            # In such scenarios, a better approach might involve checking around 25-100 packets.
            # However, increasing this value also increases the risk, as the host may have already disconnected.
            and potential_session_host_player.packets >= 50
        ):
            SessionHost.player = potential_session_host_player
            SessionHost.search_player = False

class UserIP_Settings:
    """
    Class to represent settings with attributes for each setting key.
    """
    def __init__(self,
        ENABLED: bool,
        COLOR: Literal["BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE"],
        LOG: bool,
        NOTIFICATIONS: bool,
        VOICE_NOTIFICATIONS: Union[str | Literal[False]],
        PROTECTION: Literal["Suspend_Process", "Exit_Process", "Restart_Process", "Shutdown_PC", "Restart_PC", False],
        PROTECTION_PROCESS_PATH: Optional[Path],
        PROTECTION_RESTART_PROCESS_PATH: Optional[Path],
        PROTECTION_SUSPEND_PROCESS_MODE: Union[int, float, Literal["Auto", "Manual"]]
    ):
        self.ENABLED = ENABLED
        self.COLOR = COLOR
        self.NOTIFICATIONS = NOTIFICATIONS
        self.VOICE_NOTIFICATIONS = VOICE_NOTIFICATIONS
        self.LOG = LOG
        self.PROTECTION = PROTECTION
        self.PROTECTION_PROCESS_PATH = PROTECTION_PROCESS_PATH
        self.PROTECTION_RESTART_PROCESS_PATH = PROTECTION_RESTART_PROCESS_PATH
        self.PROTECTION_SUSPEND_PROCESS_MODE = PROTECTION_SUSPEND_PROCESS_MODE

class UserIP:
    """
    Class representing information associated with a specific IP, including settings and usernames.
    """
    def __init__(self,
        ip: str,
        database_name: str,
        settings: UserIP_Settings,
        usernames: list[str]
    ):
        self.ip = ip
        self.database_name = database_name
        self.settings = settings
        self.usernames = usernames

class UserIP_Databases:
    userip_databases: list[tuple[str, UserIP_Settings, dict[str, list[str]]]] = []
    userip_infos_by_ip: dict[str, UserIP] = {}
    ips_set: set[str] = set()
    notified_settings_corrupted: set[Path] = set()
    notified_ip_invalid: set[str] = set()
    notified_ip_conflicts: set[str] = set()

    @staticmethod
    def _notify_conflict(initial_userip_entry: UserIP, conflicting_database_name: str, conflicting_username: str, conflicting_ip: str):
        from Modules.consts import USERIP_DATABASES_PATH

        msgbox_title = TITLE
        msgbox_message = textwrap.indent(textwrap.dedent(f"""
            ERROR:
                UserIP databases IP conflict

            INFOS:
                The same IP cannot be assigned to multiple
                databases.
                Users assigned to this IP will be ignored until
                the conflict is resolved.

            DEBUG:
                \"{USERIP_DATABASES_PATH}\\{initial_userip_entry.database_name}.ini\":
                {', '.join(initial_userip_entry.usernames)}={initial_userip_entry.ip}

                \"{USERIP_DATABASES_PATH}\\{conflicting_database_name}.ini\":
                {conflicting_username}={conflicting_ip}
        """.removeprefix("\n").removesuffix("\n")), "    ")
        msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.SystemModal | MsgBox.Style.MsgBoxSetForeground
        threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()

    @classmethod
    def add(cls, database_name: str, settings: UserIP_Settings, user_ips: dict[str, list[str]]):
        """
        Add a settings dictionary and user_ips dictionary pair to the databases.

        Args:
            database_name: The name of the database.
            settings: The settings class for the database.
            user_ips: A dictionary mapping usernames to lists of IPs.
        """
        if settings.ENABLED == True:
            cls.userip_databases.append((database_name, settings, user_ips))

    @classmethod
    def reset(cls):
        """
        Reset the userip_databases by clearing all entries.
        """
        cls.userip_databases.clear()

    @classmethod
    def build(cls):
        """
        Build the userip_infos_by_ip dictionaries from the current databases.
        This method updates the dictionaries without clearing their content entirely, and avoids duplicates.
        """
        userip_infos_by_ip: dict[str, UserIP] = {}
        unresolved_conflicts: set[str] = set()
        ips_set: set[str] = set()

        for database_name, settings, user_ips in cls.userip_databases:
            for username, ips in user_ips.items():
                for ip in ips:
                    if ip not in userip_infos_by_ip:
                        userip_infos_by_ip[ip] = UserIP(
                            ip = ip,
                            database_name = database_name,
                            settings = settings,
                            usernames = [username]
                        )
                        ips_set.add(ip)

                    if not userip_infos_by_ip[ip].database_name == database_name:
                        if ip not in cls.notified_ip_conflicts:
                            cls._notify_conflict(userip_infos_by_ip[ip], database_name, username, ip)
                            cls.notified_ip_conflicts.add(ip)
                        unresolved_conflicts.add(ip)
                        continue

                    if username not in userip_infos_by_ip[ip].usernames:
                        userip_infos_by_ip[ip].usernames.append(username)

        resolved_conflicts = cls.notified_ip_conflicts - unresolved_conflicts
        for resolved_ip in resolved_conflicts:
            cls.notified_ip_conflicts.remove(resolved_ip)

        cls.userip_infos_by_ip = userip_infos_by_ip
        cls.ips_set = ips_set

    @classmethod
    def get_userip_info(cls, ip: str):
        """Returns an UserIP object for the specified IP, containing its associated name, settings and usernames."""
        return cls.userip_infos_by_ip.get(ip)

    @classmethod
    def update_player_userip_data(cls, player: Player):
        """
        Updates the player's UserIP data using the information from UserIP_Databases.

        Args:
            player: The player object with 'ip' and 'userip' attributes.
        """
        if userip_data := cls.get_userip_info(player.ip):
            player.userip.database_name = userip_data.database_name
            player.userip.settings = userip_data.settings
            player.userip.usernames = userip_data.usernames
            return True
        return False

def is_pyinstaller_compiled():
    return getattr(sys, "frozen", False) # Check if the running Python script is compiled using PyInstaller, cx_Freeze or similar

def title(title: str):
    print(f"\033]0;{title}\007", end="")

def cls():
    print("\033c", end="")

def take(n: int, input_list: list[Any]):
    """Return first n items from the given input list."""
    return input_list[:n]

def tail(n: int, input_list: list[Any]):
    """Return last n items from the given input list."""
    return input_list[-n:]

def concat_lists_no_duplicates(*lists: list[Any]):
    """
    Concatenates multiple lists while removing duplicates and preserving order.

    Args:
        *lists: One or more lists to concatenate.
    """
    unique_list: list[Any] = []
    seen = set()

    for lst in lists:
        for item in lst:
            if item not in seen:
                unique_list.append(item)
                seen.add(item)

    return unique_list

def plural(variable: int):
    return "s" if variable > 1 else ""

def hex_to_int(hex_string: str):
    return int(hex_string, 16)

def is_hex(string: str):
    try:
        int(string, 16)
        return True
    except (ValueError, TypeError):
        return False

def is_ipv4_address(ip_address: str):
    try:
        return IPv4Address(ip_address).version == 4
    except AddressValueError:
        return False

def is_mac_address(mac_address: str):
    from Modules.consts import RE_MAC_ADDRESS_PATTERN
    return bool(RE_MAC_ADDRESS_PATTERN.match(mac_address))

def is_private_device_ipv4(ip_address: str):
    return IPv4Address(ip_address).is_private

def is_valid_non_special_ipv4(ip_address: str):
    try:
        ipv4_obj = IPv4Address(ip_address)
    except AddressValueError:
        return False

    if (
        not ipv4_obj.version == 4
        or ipv4_obj.packed[-1] == 255
        or ipv4_obj.is_link_local # might wants to disable this
        or ipv4_obj.is_loopback
        or ipv4_obj.is_reserved
        or ipv4_obj.is_unspecified
        or ipv4_obj.is_global
        or ipv4_obj.is_multicast
    ):
        return False

    return True

def get_pid_by_path(filepath: Path):
    for process in psutil.process_iter(["pid", "exe"]):
        if process.info["exe"] == str(filepath.absolute()):
            return process.pid
    return None

def get_mac_address_organization_name(mac_address: Optional[str]):
    if mac_address is None:
        return None

    oui_or_mal_infos: list[dict[str, str]] = mac_lookup.lookup(mac_address)
    if oui_or_mal_infos is None:
        return None

    for oui_or_mal in oui_or_mal_infos:
        organization_name = oui_or_mal["organization_name"]
        if not organization_name == "":
            return organization_name

    return None

def get_tshark_interfaces():
    def process_stdout(stdout: str):
        return stdout.strip().split(" ", maxsplit=2)

    stdout = subprocess.check_output([
        Settings.CAPTURE_TSHARK_PATH, "-D"
    ], text=True, encoding="utf-8")

    interfaces: list[tuple[str, str, str]] = []
    for parts in map(process_stdout, stdout.splitlines()):
        if len(parts) == 3:
            index = parts[0].removesuffix(".")
            device_name = parts[1]
            name = parts[2].removeprefix("(").removesuffix(")")
            interfaces.append((index, device_name, name))

    return interfaces

def get_and_parse_arp_cache():
    stdout = subprocess.check_output([
        "arp", "-a"
    ], text=True, encoding="utf-8", errors="ignore") # NOTE: "ignore" completely removes those annoying chars, so it might be a problem at some points?

    RE_NEW_INTERFACE_PATTERN = re.compile(r"^\w+\s*:\s*(?P<IP_ADDRESS>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*---\s*(?P<INTERFACE_IDX_HEX>0x[0-9a-fA-F]+)$")
    RE_ENTRY_PATTERN = re.compile(r"^\s*(?P<IP_ADDRESS>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*(?P<MAC_ADDRESS>(?:[0-9a-fA-F]{2}[:-]){5}(?:[0-9a-fA-F]{2}))\s*\w+\s*$")

    cached_arp_dict: dict[int, dict[str, str | list[dict[str, str]]]] = {}
    current_interface: dict[str, str | list[dict[str, str]]] = {}

    for line in stdout.splitlines():
        new_interface_match = RE_NEW_INTERFACE_PATTERN.match(line)
        if new_interface_match:
            current_interface = {}

            ip_address = new_interface_match.group('IP_ADDRESS')
            if ip_address is None or not isinstance(ip_address, str) or not is_ipv4_address(ip_address):
                continue
            interface_idx_hex = new_interface_match.group('INTERFACE_IDX_HEX')
            if interface_idx_hex is None or not isinstance(interface_idx_hex, str) or not is_hex(interface_idx_hex):
                continue

            interface_index = hex_to_int(interface_idx_hex)

            current_interface = {
                "interface_ip_address": ip_address,
                "interface_arp_output": []
            }
            cached_arp_dict[interface_index] = current_interface
            continue

        entry_match = RE_ENTRY_PATTERN.match(line)
        if entry_match and current_interface:
            ip_address = entry_match.group('IP_ADDRESS')
            if ip_address is None or not isinstance(ip_address, str) or not is_ipv4_address(ip_address):
                continue
            mac_address = entry_match.group('MAC_ADDRESS')
            if mac_address is None or not isinstance(mac_address, str) or not is_mac_address(mac_address):
                continue

            current_interface["interface_arp_output"].append({
                "ip_address": ip_address,
                "mac_address": mac_address
            })

    return cached_arp_dict

def iterate_network_adapter_details(**kwargs):
    """Yields network adapter info using WMI."""
    interfaces: list[_wmi_object] = wmi_namespace.Win32_NetworkAdapter(**kwargs)
    if not isinstance(interfaces, list):
        raise TypeError(f'Expected "list", got "{type(interfaces)}"')

    for interface in interfaces:
        if not isinstance(interface, _wmi_object):
            raise TypeError(f'Expected "_wmi_object", got "{type(interface)}"')

        yield(interface)

def iterate_network_ip_details(**kwargs):
    """Yields network IP address info using WMI."""
    # Get network adapter configurations
    configurations: list[_wmi_object] = wmi_namespace.Win32_NetworkAdapterConfiguration(**kwargs)
    if not isinstance(configurations, list):
        raise TypeError(f'Expected "list", got "{type(configurations)}"')

    for configuration in configurations:
        if not isinstance(configuration, _wmi_object):
            raise TypeError(f'Expected "_wmi_object", got "{type(configuration)}"')

        yield(configuration)

def format_mac_address(mac_address: str):
    if not is_mac_address(mac_address):
        stdout_crash_text = textwrap.dedent(f"""
            ERROR:
                Developer didn't expect this scenario to be possible.

            INFOS:
                It seems like a MAC address does not follow
                \"xx:xx:xx:xx:xx:xx\" or \"xx-xx-xx-xx-xx-xx\"
                format.

            DEBUG:
                mac_address: {mac_address}
        """.removeprefix("\n").removesuffix("\n"))
        terminate_script("EXIT", stdout_crash_text, stdout_crash_text)

    return mac_address.replace("-", ":").upper()

def truncate_with_ellipsis(string: str, max_length: int):
    """
    Format a string by truncating it to a specified maximum length, and appending "..." if truncated.

    Args:
        string: The string to format.
        max_length: The maximum length of the formatted string.

    Returns:
        str: The formatted string, truncated with "..." if it exceeds the specified `max_length`.
             Otherwise, returns the original `string`.
    """
    if len(string) > max_length:
        return f"{string[:max_length]}..."
    return string

def show_error__tshark_not_detected():
    from Modules.consts import WIRESHARK_REQUIRED_DL

    webbrowser.open(WIRESHARK_REQUIRED_DL)

    msgbox_title = TITLE
    msgbox_message = textwrap.dedent(f"""
        ERROR: Could not detect \"TShark (Wireshark) v4.2.9\" installed on your system.

        Opening the \"Wireshark\" project download page for you.
        You can then download and install it from there and press \"Retry\".
    """.removeprefix("\n").removesuffix("\n"))
    msgbox_style = MsgBox.Style.RetryCancel | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground

    return MsgBox.show(msgbox_title, msgbox_message, msgbox_style)

def update_and_initialize_geolite2_readers():
    def update_geolite2_databases():
        from Modules.consts import GEOLITE2_DATABASES_FOLDER_PATH, GITHUB_RELEASE_API__GEOLITE2 # TODO: Implement adding: `, GITHUB_RELEASE_API__GEOLITE2__BACKUP` in case the first one fails.

        geolite2_version_file_path = GEOLITE2_DATABASES_FOLDER_PATH / "version.json"
        geolite2_databases: dict[str, dict[str, None | str]] = {
            f"GeoLite2-{db}.mmdb": {
                "current_version": None,
                "last_version": None,
                "download_url": None
            }
            for db in ["ASN", "City", "Country"]
        }

        try:
            with geolite2_version_file_path.open("r", encoding="utf-8") as f:
                loaded_data = json.load(f)
        except (FileNotFoundError, JSONDecodeError):
            pass
        else:
            if isinstance(loaded_data, dict):
                for database_name, database_info in loaded_data.items():
                    if not isinstance(database_info, dict):
                        continue

                    if database_name in geolite2_databases:
                        geolite2_databases[database_name]["current_version"] = database_info.get("version", None)

        try:
            response = s.get(GITHUB_RELEASE_API__GEOLITE2)
        except Exception as e:
            return {
                "exception": e,
                "url": GITHUB_RELEASE_API__GEOLITE2,
                "http_code": None
            }
        if response.status_code != 200:
            return {
                "exception": None,
                "url": GITHUB_RELEASE_API__GEOLITE2,
                "http_code": response.status_code
            }

        release_data = response.json()
        for asset in release_data["assets"]:
            asset_name = asset["name"]
            if asset_name in geolite2_databases:
                geolite2_databases[asset_name].update({
                    "last_version": asset["updated_at"],
                    "download_url": asset["browser_download_url"]
                })

        failed_fetching_flag_list = []
        for database_name, database_info in geolite2_databases.items():
            if database_info["last_version"]:
                if not database_info["current_version"] == database_info["last_version"]:
                    try:
                        response = s.get(database_info["download_url"])
                    except Exception as e:
                        return {
                            "exception": e,
                            "url": database_info["download_url"],
                            "http_code": None
                        }
                    if response.status_code != 200:
                        return {
                            "exception": None,
                            "url": database_info["download_url"],
                            "http_code": response.status_code
                        }

                    GEOLITE2_DATABASES_FOLDER_PATH.mkdir(parents=True, exist_ok=True)  # Create directory if it doesn't exist
                    file_path = GEOLITE2_DATABASES_FOLDER_PATH / database_name
                    file_path.write_bytes(response.content)

                    geolite2_databases[database_name]["current_version"] = database_info["last_version"]
            else:
                failed_fetching_flag_list.append(database_name)

        if failed_fetching_flag_list:
            msgbox_title = TITLE
            msgbox_message = textwrap.indent(textwrap.dedent(f"""
                ERROR:
                    Failed fetching MaxMind \"{'\", \"'.join(failed_fetching_flag_list)}\" database{plural(len(failed_fetching_flag_list))}.

                INFOS:
                    These MaxMind GeoLite2 database{plural(len(failed_fetching_flag_list))} will not be updated.

                DEBUG:
                    GITHUB_RELEASE_API__GEOLITE2={GITHUB_RELEASE_API__GEOLITE2}
                    failed_fetching_flag_list={failed_fetching_flag_list}
            """.removeprefix("\n").removesuffix("\n")), "    ")
            msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.SystemModal | MsgBox.Style.MsgBoxSetForeground
            threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()

        with geolite2_version_file_path.open("w", encoding="utf-8") as f:
            json.dump({
                name: {"version": info["current_version"]}
                for name, info in geolite2_databases.items()
            }, f, indent=4)

        return {
            "exception": None,
            "url": None,
            "http_code": None
        }

    def initialize_geolite2_readers():
        from Modules.consts import GEOLITE2_DATABASES_FOLDER_PATH

        try:
            geolite2_asn_reader = geoip2.database.Reader(GEOLITE2_DATABASES_FOLDER_PATH / "GeoLite2-ASN.mmdb")
            geolite2_city_reader = geoip2.database.Reader(GEOLITE2_DATABASES_FOLDER_PATH / "GeoLite2-City.mmdb")
            geolite2_country_reader = geoip2.database.Reader(GEOLITE2_DATABASES_FOLDER_PATH / "GeoLite2-Country.mmdb")

            geolite2_asn_reader.asn("1.1.1.1")
            geolite2_city_reader.city("1.1.1.1")
            geolite2_country_reader.country("1.1.1.1")
        except Exception as e:
            geolite2_asn_reader = None
            geolite2_city_reader = None
            geolite2_country_reader = None

            exception = e
        else:
            exception = None

        return exception, geolite2_asn_reader, geolite2_city_reader, geolite2_country_reader

    update_geolite2_databases__dict = update_geolite2_databases()
    exception__initialize_geolite2_readers, geolite2_asn_reader, geolite2_city_reader, geolite2_country_reader = initialize_geolite2_readers()

    show_error = False
    msgbox_message = ""

    if update_geolite2_databases__dict["exception"]:
        msgbox_message += f"Exception Error: {update_geolite2_databases__dict['exception']}\n\n"
        show_error = True
    if update_geolite2_databases__dict["url"]:
        msgbox_message += f"Error: Failed fetching url: \"{update_geolite2_databases__dict['url']}\"."
        if update_geolite2_databases__dict["http_code"]:
            msgbox_message += f" (http_code: {update_geolite2_databases__dict['http_code']})"
        msgbox_message += "\nImpossible to keep Maxmind's GeoLite2 IP to Country, City and ASN resolutions feature up-to-date.\n\n"
        show_error = True

    if exception__initialize_geolite2_readers:
        msgbox_message += f"Exception Error: {exception__initialize_geolite2_readers}\n\n"
        msgbox_message += "Now disabling MaxMind's GeoLite2 IP to Country, City and ASN resolutions feature.\n"
        msgbox_message += "Countrys, Citys and ASN from players won't shows up from the players fields."
        geoip2_enabled = False
        show_error = True
    else:
        geoip2_enabled = True

    if show_error:
        msgbox_title = TITLE
        msgbox_message = msgbox_message.rstrip("\n")
        msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
        MsgBox.show(msgbox_title, msgbox_message, msgbox_style)

    return geoip2_enabled, geolite2_asn_reader, geolite2_city_reader, geolite2_country_reader

def parse_settings_ini_file(ini_path: Path, values_handling: Literal["first", "last", "all"]):
    from Modules.consts import RE_SETTINGS_INI_PARSER_PATTERN

    def process_ini_line_output(line: str):
        return line.rstrip("\n")

    if not ini_path.exists():
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),
                                str(ini_path.absolute()))
    if not ini_path.is_file():
        raise InvalidFileError(str(ini_path.absolute()))

    ini_data = ini_path.read_text("utf-8")

    need_rewrite_ini = False
    ini_db: dict[str, str | list[str]] = {}

    for line in map(process_ini_line_output, ini_data.splitlines(keepends=False)):
        corrected_line = line.strip()
        if not corrected_line == line:
            need_rewrite_ini = True

        match = RE_SETTINGS_INI_PARSER_PATTERN.search(corrected_line)
        if not match:
            continue
        setting_name = match.group("key")
        if not isinstance(setting_name, str):
            raise TypeError(f'Expected "str" object, got "{type(setting_name)}"')
        setting_value = match.group("value")
        if not isinstance(setting_value, str):
            raise TypeError(f'Expected "str" object, got "{type(setting_value)}"')

        corrected_setting_name = setting_name.strip()
        if corrected_setting_name == "":
            continue
        elif not corrected_setting_name == setting_name:
            need_rewrite_ini = True

        corrected_setting_value = setting_value.strip()
        if corrected_setting_value == "":
            continue
        elif not corrected_setting_value == setting_value:
            need_rewrite_ini = True

        if values_handling == "first":
            if corrected_setting_name not in ini_db:
                ini_db[corrected_setting_name] = corrected_setting_value
        elif values_handling == "last":
            ini_db[corrected_setting_name] = corrected_setting_value
        elif values_handling == "all":
            if corrected_setting_name in ini_db:
                ini_db[corrected_setting_name].append(corrected_setting_value)
            else:
                ini_db[corrected_setting_name] = [corrected_setting_value]

    return ini_db, need_rewrite_ini

def is_file_need_newline_ending(file):
    file = Path(file)
    if file.stat().st_size == 0:
        return False

    return not file.read_bytes().endswith(b"\n")

def terminate_process_tree(pid: int = None):
    """Terminates the process with the given PID and all its child processes.
       Defaults to the current process if no PID is specified."""
    pid = pid or psutil.Process().pid

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(children, timeout=5)
        try:
            parent.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        parent.wait(5)
    except psutil.NoSuchProcess:
        pass

def check_case_insensitive_and_exact_match(input_value: str, custom_values_list: list[str]):
    """
    Checks if the input value matches any string in the list case-insensitively, and whether it also matches exactly (case-sensitive).

    It also returns the correctly capitalized version of the matched value from the list if a case-insensitive match is found.

    Returns a tuple of three values:
    - The first boolean is True if a case-insensitive match is found.
    - The second boolean is True if the exact case-sensitive match is found.
    - The third value is the correctly capitalized version of the matched string if found, otherwise None.
    """
    case_insensitive_match = False
    case_sensitive_match = False
    normalized_match = None

    lowered_input_value = input_value.lower()
    for value in custom_values_list:
        if value.lower() == lowered_input_value:
            case_insensitive_match = True
            normalized_match = value
            if value == input_value:
                case_sensitive_match = True
                break

    return case_insensitive_match, case_sensitive_match, normalized_match

def custom_str_to_bool(string: str, only_match_against: Optional[bool] = None):
    """
    This function returns the boolean value represented by the string for lowercase or any case variation;\n
    otherwise, it raises an \"InvalidBooleanValueError\".

    Args:
        string: The boolean string to be checked.
        only_match_against (optional): If provided, the only boolean value to match against.
    """
    need_rewrite_current_setting = False
    resolved_value = None

    string_lower = string.lower()

    if string_lower == "true":
        resolved_value = True
    elif string_lower == "false":
        resolved_value = False

    if resolved_value is None:
        raise InvalidBooleanValueError("Input is not a valid boolean value")

    if (
        only_match_against is not None
        and only_match_against is not resolved_value
    ):
        raise InvalidBooleanValueError("Input does not match the specified boolean value")

    if not string == str(resolved_value):
        need_rewrite_current_setting = True

    return resolved_value, need_rewrite_current_setting

def custom_str_to_nonetype(string: str):
    """
    This function returns the NoneType value represented by the string for lowercase or any case variation; otherwise, it raises an \"InvalidNoneTypeValueError\".

    Args:
        string: The NoneType string to be checked.
    """
    if not string.lower() == "none":
        raise InvalidNoneTypeValueError("Input is not a valid NoneType value")

    is_string_literal_none = string == "None"
    return None, is_string_literal_none

colorama.init(autoreset=True)

if is_pyinstaller_compiled():
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)

TITLE = "Session Sniffer"
VERSION = "v1.3.0 - 15/12/2024 (16:26)"
SETTINGS_PATH = Path("Settings.ini")

cls()
title(f"Searching for a new update - {TITLE}")
print("\nSearching for a new update ...\n")
error_updating__flag = False
try:
    response = s.get("https://raw.githubusercontent.com/BUZZARDGTA/GTA-V-Session-Sniffer/version/version.txt")
except:
    error_updating__flag = True
else:
    if response.status_code == 200:
        current_version = Version(VERSION)
        latest_version = Version(response.text.strip().rstrip())
        if Updater(current_version).check_for_update(latest_version):
            msgbox_title = TITLE
            msgbox_message = textwrap.dedent(f"""
                New version found. Do you want to update ?

                Current version: {current_version}
                Latest version: {latest_version}
            """.removeprefix("\n").removesuffix("\n"))
            msgbox_style = MsgBox.Style.YesNo | MsgBox.Style.Question | MsgBox.Style.MsgBoxSetForeground
            errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
            if errorlevel == MsgBox.ReturnValues.IDYES:
                webbrowser.open("https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer")
                terminate_script("EXIT")
    else:
        error_updating__flag = True

if error_updating__flag:
    msgbox_title = TITLE
    msgbox_message = textwrap.dedent(f"""
        ERROR: Failed to check for updates.

        Do you want to open the \"{TITLE}\" project download page ?
        You can then download and run the latest version from there.
    """.removeprefix("\n").removesuffix("\n"))
    msgbox_style = MsgBox.Style.YesNo | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
    errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
    if errorlevel == MsgBox.ReturnValues.IDYES:
        webbrowser.open("https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer")
        terminate_script("EXIT")


if not is_pyinstaller_compiled():
    import importlib.metadata

    cls()
    title(f"Checking that your Python packages versions matches with file \"requirements.txt\" - {TITLE}")
    print(f"\nChecking that your Python packages versions matches with file \"requirements.txt\" ...\n")

    def check_packages_version(third_party_packages: dict[str, str]):
        outdated_packages = []

        for package_name, required_version in third_party_packages.items():
            installed_version = importlib.metadata.version(package_name)
            if not installed_version == required_version:
                outdated_packages.append((package_name, installed_version, required_version))

        return outdated_packages

    third_party_packages = {
        "colorama": "0.4.6",
        "geoip2": "4.8.1",
        "prettytable": "3.12.0",
        "psutil": "6.1.0",
        "pypresence": "4.3.0",
        "PyQt6": "6.8.0",
        "pywin32": "308",
        "requests": "2.32.3",
        "rich": "13.9.4",
        "urllib3": "2.2.3",
        "WMI": "1.5.1"
    }

    outdated_packages: list[tuple[str, str, str]] = check_packages_version(third_party_packages)
    if outdated_packages:
        msgbox_message = "Your following packages are not up to date:\n\n"
        msgbox_message += f"Package Name: Installed version --> Required version\n"

        # Iterate over outdated packages and add each package's information to the message box text
        for package_name, installed_version, required_version in outdated_packages:
            msgbox_message += f"{package_name}: {installed_version} --> {required_version}\n"

        # Add additional message box text
        msgbox_message += f"\nKeeping your packages synced with \"{TITLE}\" ensures smooth script execution and prevents compatibility issues."
        msgbox_message += "\n\nDo you want to ignore this warning and continue with script execution?"

        # Show message box
        msgbox_style = MsgBox.Style.YesNo | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
        msgbox_title = TITLE
        errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
        if errorlevel != MsgBox.ReturnValues.IDYES:
            terminate_script("EXIT")

cls()
title(f"Initializing the script for your Windows version - {TITLE}")
print("\nInitializing the script for your Windows version ...\n")
if sys.getwindowsversion().major >= 10:
    UNDERLINE = "\033[4m"
    UNDERLINE_RESET = "\033[24m"
else:
    UNDERLINE = ""
    UNDERLINE_RESET = ""

cls()
title(f"Checking that \"Npcap\" or \"WinpCap\" driver is installed on your system - {TITLE}")
print("\nChecking that \"Npcap\" or \"WinpCap\" driver is installed on your system ...\n")
while not is_npcap_or_winpcap_installed():
    webbrowser.open("https://nmap.org/npcap/")
    msgbox_title = TITLE
    msgbox_message = textwrap.dedent(f"""
        ERROR: Could not detect the \"Npcap\" or \"WinpCap\" driver installed on your system.

        Opening the \"Npcap\" project download page for you.
        You can then download and install it from there and press \"Retry\".
    """.removeprefix("\n").removesuffix("\n"))
    msgbox_style = MsgBox.Style.RetryCancel | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
    errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
    if errorlevel == MsgBox.ReturnValues.IDCANCEL:
        terminate_script("EXIT")

cls()
title(f"Applying your custom settings from \"Settings.ini\" - {TITLE}")
print("\nApplying your custom settings from \"Settings.ini\" ...\n")
Settings.load_from_settings_file(SETTINGS_PATH)

cls()
title(f"Checking that \"Tshark (Wireshark) v4.2.9\" is installed on your system - {TITLE}")
print("\nChecking that \"Tshark (Wireshark) v4.2.9\" is installed on your system ...\n")
from Modules.consts import WIRESHARK_REQUIRED_DL, WIRESHARK_REQUIRED_VERSION

while True:
    try:
        tshark_path = get_tshark_path(Settings.CAPTURE_TSHARK_PATH)
        if Settings.CAPTURE_TSHARK_PATH is None:
            Settings.CAPTURE_TSHARK_PATH = tshark_path
            Settings.reconstruct_settings()
        break
    except TSharkNotFoundException:
        errorlevel = show_error__tshark_not_detected()
        if errorlevel == MsgBox.ReturnValues.IDCANCEL:
            terminate_script("EXIT")
    except InvalidTSharkVersionException as invalid_tshark_version:
        webbrowser.open(WIRESHARK_REQUIRED_DL)
        msgbox_title = TITLE
        msgbox_message = textwrap.dedent(f"""
            ERROR: Detected an unsupported \"Tshark (Wireshark)\" version installed on your system.

            Installed version: {invalid_tshark_version.version}
            Required version: {WIRESHARK_REQUIRED_VERSION}

            Opening the \"Wireshark\" project download page for you.
            You can then download and install it from there and press \"Retry\".
        """.removeprefix("\n").removesuffix("\n"))
        msgbox_style = MsgBox.Style.AbortRetryIgnore | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
        errorlevel = MsgBox.show(msgbox_title, msgbox_message, msgbox_style)
        if errorlevel == MsgBox.ReturnValues.IDABORT:
            terminate_script("EXIT")
        elif errorlevel == MsgBox.ReturnValues.IDIGNORE:
            if Settings.CAPTURE_TSHARK_PATH is None:
                Settings.CAPTURE_TSHARK_PATH = invalid_tshark_version.path
                Settings.reconstruct_settings()
            break

cls()
title(f"Initializing and updating MaxMind's GeoLite2 Country, City and ASN databases - {TITLE}")
print("\nInitializing and updating MaxMind's GeoLite2 Country, City and ASN databases ...\n")
geoip2_enabled, geolite2_asn_reader, geolite2_city_reader, geolite2_country_reader = update_and_initialize_geolite2_readers()

cls()
title(f"Initializing MacLookup module - {TITLE}")
print(f"\nInitializing MacLookup module ...\n")
mac_lookup = MacLookup(bypass_fetch_error=True)

cls()
title(f"Capture network interface selection - {TITLE}")
print(f"\nCapture network interface selection ...\n")
wmi_namespace: _wmi_namespace = wmi.WMI()
if not isinstance(wmi_namespace, _wmi_namespace):
    raise TypeError(f'Expected "_wmi_namespace" object, got "{type(wmi_namespace)}"')

for _, _, name in get_tshark_interfaces():
    Interface(name)

net_io_stats = psutil.net_io_counters(pernic=True)
for interface_name, interface_stats in net_io_stats.items():
    i = Interface.get_interface_by_name(interface_name)
    if not i:
        continue

    i.packets_sent = interface_stats.packets_sent
    i.packets_recv = interface_stats.packets_recv

for config in iterate_network_adapter_details():
    i = Interface.get_interface_by_name(config.NetConnectionID)
    if not i:
        continue

    if config.MACAddress is not None:
        if not isinstance(config.MACAddress, str):
            raise TypeError(f'Expected "str" object, got "{type(config.MACAddress)}"')
        if i.mac_address != "N/A":
            stdout_crash_text = textwrap.dedent(f"""
                ERROR:
                    Developer didn't expect this scenario to be possible.

                INFOS:
                    \"WMI\" Python's module returned more then one
                    mac address for a given interface.

                DEBUG:
                    i.name: {i.name}
                    i.mac_address: {i.mac_address}
                    config.MACAddress: {config.MACAddress}
            """.removeprefix("\n").removesuffix("\n"))
            terminate_script("EXIT", stdout_crash_text, stdout_crash_text)
        i.mac_address = config.MACAddress
        i.organization_name = (
            get_mac_address_organization_name(i.mac_address)
            or "N/A"
        )

    if config.InterfaceIndex is not None:
        if not isinstance(config.InterfaceIndex, int):
            raise TypeError(f'Expected "int" object, got "{type(config.InterfaceIndex)}"')
        if i.adapter_properties.InterfaceIndex != "N/A":
            stdout_crash_text = textwrap.dedent(f"""
                ERROR:
                    Developer didn't expect this scenario to be possible.

                INFOS:
                    \"WMI\" Python's module returned more then one
                    index for a given interface.

                DEBUG:
                    i.name: {i.name}
                    i.InterfaceIndex: {i.InterfaceIndex}
                    config.InterfaceIndex: {config.InterfaceIndex}
            """.removeprefix("\n").removesuffix("\n"))
            terminate_script("EXIT", stdout_crash_text, stdout_crash_text)
        i.adapter_properties.InterfaceIndex = config.InterfaceIndex

    if config.Manufacturer is not None:
        if not isinstance(config.Manufacturer, str):
            raise TypeError(f'Expected "str" object, got "{type(config.Manufacturer)}"')
        i.adapter_properties.Manufacturer = config.Manufacturer

for config in iterate_network_ip_details():
    i = Interface.get_interface_by_id(config.InterfaceIndex)
    if not i:
        continue

    if config.IPAddress is not None:
        if not isinstance(config.IPAddress, tuple):
            raise TypeError(f'Expected "tuple" object, got "{type(config.IPAddress)}"')

        for ip in config.IPAddress:
            if not is_ipv4_address(ip):
                continue

            if ip not in i.ip_addresses:
                i.ip_addresses.append(ip)

    if config.MACAddress is not None:
        if not isinstance(config.MACAddress, str):
            raise TypeError(f'Expected "str" object, got "{type(config.MACAddress)}"')
        if i.mac_address != config.MACAddress:
            stdout_crash_text = textwrap.dedent(f"""
                ERROR:
                    Developer didn't expect this scenario to be possible.

                INFOS:
                    An interface IP address has multiple MAC addresses.

                DEBUG:
                    i.name: {i.name}
                    i.mac_address: {i.mac_address}
                    config.MACAddress: {config.MACAddress}
            """.removeprefix("\n").removesuffix("\n"))
            terminate_script("EXIT", stdout_crash_text, stdout_crash_text)
        i.mac_address = config.MACAddress

if Settings.CAPTURE_ARP:
    cached_arp_dict = get_and_parse_arp_cache()

    for arp_interface_index, arp_interface_info in cached_arp_dict.items():
        i = Interface.get_interface_by_id(arp_interface_index)
        if not i:
            continue

        if (
            not arp_interface_info["interface_ip_address"] in i.ip_addresses
            or not arp_interface_info["interface_arp_output"]
        ):
            continue

        arp_infos: list[dict[str, str]] = [
            {
                "ip_address": entry["ip_address"],
                "mac_address": format_mac_address(entry["mac_address"]),
                "organization_name": (
                    get_mac_address_organization_name(entry["mac_address"])
                    or "N/A"
                )
            }
            for entry in arp_interface_info["interface_arp_output"]
            if is_valid_non_special_ipv4(entry["ip_address"])
        ]

        if arp_infos:
            i.add_arp_infos(arp_interface_info["interface_ip_address"], arp_infos)

table = PrettyTable()
table.field_names = ["#", "Interface", "Packets Sent", "Packets Received", "IP Address", "MAC Address", "Manufacturer"]
table.align["#"] = "c"
table.align["Interface"] = "l"
table.align["Packets Sent"] = "c"
table.align["Packets Received"] = "c"
table.align["IP Address"] = "l"
table.align["MAC Address"] = "c"
table.align["Manufacturer"] = "c"

interfaces_options: dict[int, InterfaceOptionData] = {}
counter = 0

for i in Interface.all_interfaces:
    if (
        Settings.CAPTURE_INTERFACE_NAME is not None
        and Settings.CAPTURE_INTERFACE_NAME.lower() == i.name.lower()
        and not Settings.CAPTURE_INTERFACE_NAME == i.name
    ):
        Settings.CAPTURE_INTERFACE_NAME = i.name
        Settings.reconstruct_settings()

    if i.adapter_properties.Manufacturer == "N/A":
        manufacturer_or_organization_name = i.organization_name
    else:
        manufacturer_or_organization_name = i.adapter_properties.Manufacturer

    if i.ip_addresses:
        for ip_address in i.ip_addresses:
            counter += 1
            interfaces_options[counter] = {
                "is_arp": False,
                "interface": i.name,
                "ip_address": ip_address,
                "mac_address": i.mac_address
            }

            table.add_row([f"{Fore.YELLOW}{counter}{Fore.RESET}", i.name, i.packets_sent, i.packets_recv, ip_address, i.mac_address, manufacturer_or_organization_name])
    else:
        counter += 1
        interfaces_options[counter] = {
            "is_arp": False,
            "interface": i.name,
            "ip_address": "N/A",
            "mac_address": i.mac_address
        }

        table.add_row([f"{Fore.YELLOW}{counter}{Fore.RESET}", i.name, i.packets_sent, i.packets_recv, "N/A", i.mac_address, manufacturer_or_organization_name])

    if Settings.CAPTURE_ARP:
        for ip in i.ip_addresses:
            arp_infos = i.get_arp_infos(ip)
            if not arp_infos:
                continue

            for arp_info in arp_infos:
                counter += 1
                interfaces_options[counter] = {
                    "is_arp": True,
                    "interface": i.name,
                    "ip_address": arp_info["ip_address"],
                    "mac_address": arp_info["mac_address"]
                }

                table.add_row([f"{Fore.YELLOW}{counter}{Fore.RESET}", f"{i.name} (ARP)", "N/A", "N/A", arp_info["ip_address"], arp_info["mac_address"], arp_info["organization_name"]])

user_interface_selection = None

if (
    not Settings.CAPTURE_NETWORK_INTERFACE_CONNECTION_PROMPT
    and any(setting is not None for setting in [Settings.CAPTURE_INTERFACE_NAME, Settings.CAPTURE_MAC_ADDRESS, Settings.CAPTURE_IP_ADDRESS])
):
    max_priority = 0

    for interface_counter, interface_options in interfaces_options.items():
        priority = 0

        if Settings.CAPTURE_INTERFACE_NAME is not None:
            if Settings.CAPTURE_INTERFACE_NAME == interface_options["interface"]:
                priority += 1
        if Settings.CAPTURE_MAC_ADDRESS == interface_options["mac_address"]:
            priority += 1
        if Settings.CAPTURE_IP_ADDRESS == interface_options["ip_address"]:
            priority += 1

        if priority == max_priority: # If multiple matches on the same priority are found we search for the next bigger priority else we prompt the user.
            user_interface_selection = None
        elif priority > max_priority:
            max_priority = priority
            user_interface_selection = interface_counter

if not user_interface_selection:
    print(table)

    while True:
        try:
            user_interface_selection = int(input(f"\nSelect your desired capture network interface ({Fore.YELLOW}1{Fore.RESET}-{Fore.YELLOW}{len(interfaces_options)}{Fore.RESET}): {Fore.YELLOW}"))
        except ValueError:
            print(f"{Fore.RED}ERROR{Fore.RESET}: You didn't provide a number.")
        else:
            if (
                user_interface_selection >= 1
                and user_interface_selection <= len(interfaces_options)
            ):
                print(end=Fore.RESET)
                break
            print(f"{Fore.RED}ERROR{Fore.RESET}: The number you provided is not matching with the available network interfaces.")

cls()
title(f"Initializing addresses and establishing connection to your PC / Console - {TITLE}")
print(f"\nInitializing addresses and establishing connection to your PC / Console ...\n")
need_rewrite_settings = False
fixed__capture_mac_address = interfaces_options[user_interface_selection]["mac_address"] if interfaces_options[user_interface_selection]["mac_address"] != "N/A" else None
fixed__capture_ip_address = interfaces_options[user_interface_selection]["ip_address"] if interfaces_options[user_interface_selection]["ip_address"] != "N/A" else None

if Settings.CAPTURE_INTERFACE_NAME is None:
    Settings.CAPTURE_INTERFACE_NAME = interfaces_options[user_interface_selection]["interface"]
    need_rewrite_settings = True
elif not Settings.CAPTURE_INTERFACE_NAME == interfaces_options[user_interface_selection]["interface"]:
    Settings.CAPTURE_INTERFACE_NAME = interfaces_options[user_interface_selection]["interface"]
    need_rewrite_settings = True

if not Settings.CAPTURE_MAC_ADDRESS == fixed__capture_mac_address:
    Settings.CAPTURE_MAC_ADDRESS = fixed__capture_mac_address
    need_rewrite_settings = True

if not Settings.CAPTURE_IP_ADDRESS == fixed__capture_ip_address:
    Settings.CAPTURE_IP_ADDRESS = fixed__capture_ip_address
    need_rewrite_settings = True

if need_rewrite_settings:
    Settings.reconstruct_settings()

capture_filter: list[str] = ["ip", "udp"]
display_filter: list[str] = []
excluded_protocols: list[str] = []

if Settings.CAPTURE_IP_ADDRESS:
    capture_filter.append(f"((src host {Settings.CAPTURE_IP_ADDRESS} and (not (dst net 10.0.0.0/8 or 100.64.0.0/10 or 172.16.0.0/12 or 192.168.0.0/16 or 224.0.0.0/4))) or (dst host {Settings.CAPTURE_IP_ADDRESS} and (not (src net 10.0.0.0/8 or 100.64.0.0/10 or 172.16.0.0/12 or 192.168.0.0/16 or 224.0.0.0/4))))")

if not Settings.CAPTURE_VPN_MODE:
    capture_filter.append("not (broadcast or multicast)")
capture_filter.append("not (portrange 0-1023 or port 5353)")

if Settings.CAPTURE_PROGRAM_PRESET:
    if Settings.CAPTURE_PROGRAM_PRESET == "GTA5":
        capture_filter.append("(len >= 71 and len <= 1032)")
    elif Settings.CAPTURE_PROGRAM_PRESET == "Minecraft":
        capture_filter.append("(len >= 49 and len <= 1498)")

    # If the <CAPTURE_PROGRAM_PRESET> setting is set, automatically blocks RTCP connections.
    # In case RTCP can be useful to get someone IP, I decided not to block them without using a <CAPTURE_PROGRAM_PRESET>.
    # RTCP is known to be for example the Discord's server IP while you are in a call there.
    # The "not rtcp" Display Filter have been heavily tested and I can confirm that it's indeed working correctly.
    # I know that eventually you will see their corresponding IPs time to time but I can guarantee that it does the job it is supposed to do.
    # It filters RTCP but some connections are STILL made out of it, but those are not RTCP ¯\_(ツ)_/¯.
    # And that's exactly why the "Discord" (`class ThirdPartyServers`) IP ranges Capture Filters are useful for.
    excluded_protocols.append("rtcp")

if Settings.CAPTURE_BLOCK_THIRD_PARTY_SERVERS:
    ip_ranges = [ip_range for server in ThirdPartyServers for ip_range in server.value]
    capture_filter.append(f"not (net {' or '.join(ip_ranges)})")

    # Here I'm trying to exclude various UDP protocols that are usefless for the srcipt.
    # But there can be a lot more, those are just a couples I could find on my own usage.
    excluded_protocols.extend(["ssdp", "raknet", "dtls", "nbns", "pcp", "bt-dht", "uaudp", "classicstun", "dhcp", "mdns", "llmnr"])

if excluded_protocols:
    display_filter.append(
        f"not ({' or '.join(excluded_protocols)})"
    )

if Settings.CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER:
    capture_filter.insert(0, Settings.CAPTURE_PREPEND_CUSTOM_CAPTURE_FILTER)

if Settings.CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER:
    display_filter.insert(0, Settings.CAPTURE_PREPEND_CUSTOM_DISPLAY_FILTER)

CAPTURE_FILTER = " and ".join(capture_filter) if capture_filter else None
DISPLAY_FILTER = " and ".join(display_filter) if display_filter else None

while True:
    try:
        capture = PacketCapture(
            interface = Settings.CAPTURE_INTERFACE_NAME,
            capture_filter = CAPTURE_FILTER,
            display_filter = DISPLAY_FILTER,
            tshark_path = Settings.CAPTURE_TSHARK_PATH
        )
    except TSharkNotFoundException:
        errorlevel = show_error__tshark_not_detected()
        if errorlevel == 2:
            terminate_script("EXIT")
    else:
        break

if not capture.tshark_path == Settings.CAPTURE_TSHARK_PATH:
    Settings.CAPTURE_TSHARK_PATH = capture.tshark_path
    Settings.reconstruct_settings()

userip_logging_file_write_lock = threading.Lock()
gui_closed__event = threading.Event()

def process_userip_task(player: Player, connection_type: Literal["connected", "disconnected"]):
    with Threads_ExceptionHandler():
        def suspend_process_for_duration_or_mode(process_pid: int, duration_or_mode: Union[int, float, Literal["Auto", "Manual"]]):
            """
            Suspends the specified process for a given duration or until a specified condition is met.

            ### Parameters:
            * process_pid: The process ID of the process to be suspended.
            * duration_or_mode:
                - If an integer or float, it specifies the duration (in seconds) to suspend the process.
                - If set to \"Manual\", the process will be suspended indefinitely until the user manually resume it.
                - If set to \"Auto\", the process will be suspended until the player is flagged as \"disconnected\", after which it is resumed automatically.
            """
            process = psutil.Process(process_pid)
            process.suspend()

            if isinstance(duration_or_mode, (int, float)):
                time.sleep(duration_or_mode)
                process.resume()
                return

            if duration_or_mode == "Manual":
                return
            elif duration_or_mode == "Auto":
                while not player.datetime.left:
                    time.sleep(0.1)
                process.resume()
                return

        from Modules.consts import USERIP_LOGGING_PATH, TTS_PATH

        # We wants to run this as fast as possible so it's on top of the function.
        if connection_type == "connected":
            if player.userip.settings.PROTECTION:
                if player.userip.settings.PROTECTION == "Suspend_Process":
                    if process_pid := get_pid_by_path(player.userip.settings.PROTECTION_PROCESS_PATH):
                        threading.Thread(target=suspend_process_for_duration_or_mode, args=(process_pid, player.userip.settings.PROTECTION_SUSPEND_PROCESS_MODE), daemon=True).start()
                elif player.userip.settings.PROTECTION in ["Exit_Process", "Restart_Process"]:
                    if isinstance(player.userip.settings.PROTECTION_PROCESS_PATH, Path):
                        if process_pid := get_pid_by_path(player.userip.settings.PROTECTION_PROCESS_PATH):
                            terminate_process_tree(process_pid)

                            if player.userip.settings.PROTECTION == "Restart_Process" and isinstance(player.userip.settings.PROTECTION_RESTART_PROCESS_PATH, Path):
                                os.startfile(str(player.userip.settings.PROTECTION_RESTART_PROCESS_PATH.absolute()))
                elif player.userip.settings.PROTECTION == "Shutdown_PC":
                    subprocess.Popen(["shutdown", "/s"])
                elif player.userip.settings.PROTECTION == "Restart_PC":
                    subprocess.Popen(["shutdown", "/r"])

        if player.userip.settings.VOICE_NOTIFICATIONS:
            if player.userip.settings.VOICE_NOTIFICATIONS == "Male":
                voice_name = "Liam"
            elif player.userip.settings.VOICE_NOTIFICATIONS == "Female":
                voice_name = "Jane"
            file_path = Path(f"{TTS_PATH}/{voice_name} ({connection_type}).wav")

            if not file_path.exists():
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(file_path.absolute()))
            if not file_path.is_file():
                raise InvalidFileError(str(file_path.absolute()))

            winsound.PlaySound(file_path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

        if connection_type == "connected":
            while not player.datetime.left and (datetime.now() - player.datetime.last_seen) < timedelta(seconds=10):
                if player.userip.usernames and player.iplookup.maxmind.is_initialized:
                    break
                time.sleep(0.1)
            else:
                return

            with userip_logging_file_write_lock:
                with USERIP_LOGGING_PATH.open("a", encoding="utf-8") as f:
                    newline = "\n" if is_file_need_newline_ending(USERIP_LOGGING_PATH) else ""
                    f.write(
                        f"{newline}"
                        f"User{plural(len(player.userip.usernames))}:{', '.join(player.userip.usernames)} | "
                        f"IP:{player.ip} | Ports:{', '.join(map(str, reversed(player.ports.list)))} | "
                        f"Time:{player.userip.detection.date_time} | Country:{player.iplookup.maxmind.compiled.country} | "
                        f"Detection Type: {player.userip.detection.type} | "
                        f"Database:{player.userip.database_name}\n"
                    )

            if player.userip.settings.NOTIFICATIONS:
                while not player.datetime.left and (datetime.now() - player.datetime.last_seen) < timedelta(seconds=10):
                    if player.iplookup.ipapi.is_initialized:
                        break
                    time.sleep(0.1)
                else:
                    return

                msgbox_title = TITLE
                msgbox_message = textwrap.indent(textwrap.dedent(f"""
                    #### UserIP detected at {player.userip.detection.time} ####
                    User{plural(len(player.userip.usernames))}: {', '.join(player.userip.usernames)}
                    IP: {player.ip}
                    Port{plural(len(player.ports.list))}: {', '.join(map(str, reversed(player.ports.list)))}
                    Country Code: {player.iplookup.maxmind.compiled.country_code}
                    Detection Type: {player.userip.detection.type}
                    Database: {player.userip.database_name}
                    ############# IP Lookup ##############
                    Continent: {player.iplookup.ipapi.compiled.continent}
                    Country: {player.iplookup.maxmind.compiled.country}
                    Region: {player.iplookup.ipapi.compiled.region}
                    City: {player.iplookup.maxmind.compiled.city}
                    Organization: {player.iplookup.ipapi.compiled.org}
                    ISP: {player.iplookup.ipapi.compiled.isp}
                    ASN / ISP: {player.iplookup.maxmind.compiled.asn}
                    ASN: {player.iplookup.ipapi.compiled.as_name}
                    Mobile (cellular) connection: {player.iplookup.ipapi.compiled.mobile}
                    Proxy, VPN or Tor exit address: {player.iplookup.ipapi.compiled.proxy}
                    Hosting, colocated or data center: {player.iplookup.ipapi.compiled.hosting}
                """.removeprefix("\n").removesuffix("\n")), "    ")
                msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.SystemModal | MsgBox.Style.MsgBoxSetForeground
                threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()

def iplookup_core():
    with Threads_ExceptionHandler():
        def throttle_until():
            requests_remaining = int(response.headers["X-Rl"])
            throttle_until = int(response.headers["X-Ttl"])

            if requests_remaining <= 1:
                time.sleep(throttle_until)
            else:
                time.sleep(throttle_until / requests_remaining)  # We sleep x seconds (just in case) to avoid triggering a "429" status code.


        # Following values taken from https://ip-api.com/docs/api:batch the 03/04/2024.
        MAX_REQUESTS = 15
        MAX_THROTTLE_TIME = 60
        MAX_BATCH_IP_API_IPS = 100
        FIELDS_TO_LOOKUP = "continent,continentCode,country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,offset,currency,isp,org,as,asname,mobile,proxy,hosting,query"

        while not gui_closed__event.is_set():
            if ScriptControl.has_crashed():
                return

            players_connected_to_lookup: list[str] = []
            players_disconnected_to_lookup: list[str] = []
            removed_disconnected_ip = None

            for player in PlayersRegistry.iterate_players_from_registry():
                if player.iplookup.ipapi.is_initialized:
                    continue

                if player.datetime.left:
                    players_disconnected_to_lookup.append(player.ip)
                else:
                    players_connected_to_lookup.append(player.ip)

                if (len(players_connected_to_lookup) + len(players_disconnected_to_lookup)) == MAX_BATCH_IP_API_IPS:
                    if players_disconnected_to_lookup:
                        removed_disconnected_ip = players_disconnected_to_lookup.pop(-1)
                    else:
                        break

            ips_to_lookup = players_connected_to_lookup + players_disconnected_to_lookup
            if len(ips_to_lookup) < MAX_BATCH_IP_API_IPS:
                if (
                    len(ips_to_lookup) == (MAX_BATCH_IP_API_IPS - 1)
                    and removed_disconnected_ip
                ):
                    ips_to_lookup.append(removed_disconnected_ip)
                else:
                    remaining_space = MAX_BATCH_IP_API_IPS - len(ips_to_lookup)
                    items_to_add = IPLookup.get_pending_ips_slice(0, remaining_space)
                    ips_to_lookup.extend(items_to_add)

            # Ensure the final list is no longer than 100 elements
            ips_to_lookup = ips_to_lookup[:MAX_BATCH_IP_API_IPS]
            if len(ips_to_lookup) == 0:
                time.sleep(1) # If there are no new players to lookup, sleep for 1 second to reduce CPU usage.
                continue

            try:
                response = s.post(
                    f"http://ip-api.com/batch?fields={FIELDS_TO_LOOKUP}",
                    headers={"Content-Type": "application/json"},
                    json=ips_to_lookup,
                    timeout=3
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                continue

            if response.status_code != 200:
                throttle_until()
                continue

            iplookup_results = response.json()

            if not isinstance(iplookup_results, list):
                raise TypeError(f'Expected "list" object, got "{type(iplookup_results)}"')

            for iplookup in iplookup_results:
                if not isinstance(iplookup, dict):
                    raise TypeError(f'Expected "dict" object, got "{type(iplookup)}"')

                player_ip_looked_up = iplookup.get("query", None)
                if not isinstance(player_ip_looked_up, str):
                    raise TypeError(f'Expected "str" object, got "{type(player_ip_looked_up)}"')

                ip_api_instance = IPAPI()
                ip_api_instance.is_initialized = True

                continent = iplookup.get("continent", "N/A")
                if continent != "N/A" and not isinstance(continent, str):
                    raise TypeError(f'Expected "str" object, got "{type(continent)}" ({player_ip_looked_up})')
                ip_api_instance.continent = continent

                continent_code = iplookup.get("continentCode", "N/A")
                if continent_code != "N/A" and not isinstance(continent_code, str):
                    raise TypeError(f'Expected "str" object, got "{type(continent_code)}" ({player_ip_looked_up})')
                ip_api_instance.continent_code = continent_code

                country = iplookup.get("country", "N/A")
                if country != "N/A" and not isinstance(country, str):
                    raise TypeError(f'Expected "str" object, got "{type(country)}" ({player_ip_looked_up})')
                ip_api_instance.country = country

                country_code = iplookup.get("countryCode", "N/A")
                if country_code != "N/A" and not isinstance(country_code, str):
                    raise TypeError(f'Expected "str" object, got "{type(country_code)}" ({player_ip_looked_up})')
                ip_api_instance.country_code = country_code

                region = iplookup.get("regionName", "N/A")
                if region != "N/A" and not isinstance(region, str):
                    raise TypeError(f'Expected "str" object, got "{type(region)}" ({player_ip_looked_up})')
                ip_api_instance.region = region

                region_code = iplookup.get("region", "N/A")
                if region_code != "N/A" and not isinstance(region_code, str):
                    raise TypeError(f'Expected "str" object, got "{type(region_code)}" ({player_ip_looked_up})')
                ip_api_instance.region_code = region_code

                city = iplookup.get("city", "N/A")
                if city != "N/A" and not isinstance(city, str):
                    raise TypeError(f'Expected "str" object, got "{type(city)}" ({player_ip_looked_up})')
                ip_api_instance.city = city

                district = iplookup.get("district", "N/A")
                if district != "N/A" and not isinstance(district, str):
                    raise TypeError(f'Expected "str" object, got "{type(district)}" ({player_ip_looked_up})')
                ip_api_instance.district = district

                zip_code = iplookup.get("zip", "N/A")
                if zip_code != "N/A" and not isinstance(zip_code, str):
                    raise TypeError(f'Expected "str" object, got "{type(zip_code)}" ({player_ip_looked_up})')
                ip_api_instance.zip_code = zip_code

                lat = iplookup.get("lat", "N/A")
                if lat != "N/A" and not isinstance(lat, (float, int)):
                    raise TypeError(f'Expected "float | int" object, got "{type(lat)}" ({player_ip_looked_up})')
                ip_api_instance.lat = lat

                lon = iplookup.get("lon", "N/A")
                if lon != "N/A" and not isinstance(lon, (float, int)):
                    raise TypeError(f'Expected "float | int" object, got "{type(lon)}" ({player_ip_looked_up})')
                ip_api_instance.lon = lon

                time_zone = iplookup.get("timezone", "N/A")
                if time_zone != "N/A" and not isinstance(time_zone, str):
                    raise TypeError(f'Expected "str" object, got "{type(time_zone)}" ({player_ip_looked_up})')
                ip_api_instance.time_zone = time_zone

                offset = iplookup.get("offset", "N/A")
                if offset != "N/A" and not isinstance(offset, int):
                    raise TypeError(f'Expected "int" object, got "{type(offset)}" ({player_ip_looked_up})')
                ip_api_instance.offset = offset

                currency = iplookup.get("currency", "N/A")
                if currency != "N/A" and not isinstance(currency, str):
                    raise TypeError(f'Expected "str" object, got "{type(currency)}" ({player_ip_looked_up})')
                ip_api_instance.currency = currency

                isp = iplookup.get("isp", "N/A")
                if isp != "N/A" and not isinstance(isp, str):
                    raise TypeError(f'Expected "str" object, got "{type(isp)}" ({player_ip_looked_up})')
                ip_api_instance.isp = isp

                org = iplookup.get("org", "N/A")
                if org != "N/A" and not isinstance(org, str):
                    raise TypeError(f'Expected "str" object, got "{type(org)}" ({player_ip_looked_up})')
                ip_api_instance.org = org

                _as = iplookup.get("as", "N/A")
                if _as != "N/A" and not isinstance(_as, str):
                    raise TypeError(f'Expected "str" object, got "{type(_as)}" ({player_ip_looked_up})')
                ip_api_instance._as = _as

                as_name = iplookup.get("asname", "N/A")
                if as_name != "N/A" and not isinstance(as_name, str):
                    raise TypeError(f'Expected "str" object, got "{type(as_name)}" ({player_ip_looked_up})')
                ip_api_instance.as_name = as_name

                mobile = iplookup.get("mobile", "N/A")
                if mobile != "N/A" and not isinstance(mobile, bool):
                    raise TypeError(f'Expected "bool" object, got "{type(mobile)}" ({player_ip_looked_up})')
                ip_api_instance.mobile = mobile

                proxy = iplookup.get("proxy", "N/A")
                if proxy != "N/A" and not isinstance(proxy, bool):
                    raise TypeError(f'Expected "bool" object, got "{type(proxy)}" ({player_ip_looked_up})')
                ip_api_instance.proxy = proxy

                hosting = iplookup.get("hosting", "N/A")
                if hosting != "N/A" and not isinstance(hosting, bool):
                    raise TypeError(f'Expected "bool" object, got "{type(hosting)}" ({player_ip_looked_up})')
                ip_api_instance.hosting = hosting

                ip_api_instance.compile()

                with IPLookup.lock:
                    IPLookup.remove_pending_ip(player_ip_looked_up)
                    IPLookup.update_results(player_ip_looked_up, ip_api_instance)

                player_to_update = PlayersRegistry.get_player(player_ip_looked_up)
                if isinstance(player_to_update, Player):
                    player_to_update.iplookup.ipapi = ip_api_instance

            throttle_until()

def capture_core():
    with Threads_ExceptionHandler():
        def packet_callback(packet: Packet):
            global tshark_restarted_times, global_pps_counter

            packet_datetime = packet.frame.datetime

            packet_latency = datetime.now() - packet_datetime
            tshark_packets_latencies.append((packet_datetime, packet_latency))
            if packet_latency >= timedelta(seconds=Settings.CAPTURE_OVERFLOW_TIMER):
                tshark_restarted_times += 1
                raise PacketCaptureOverflow("Packet capture time exceeded 3 seconds.")

            if Settings.CAPTURE_IP_ADDRESS:
                if packet.ip.src == Settings.CAPTURE_IP_ADDRESS:
                    target_ip = packet.ip.dst
                    target_port = packet.udp.dstport
                elif packet.ip.dst == Settings.CAPTURE_IP_ADDRESS:
                    target_ip = packet.ip.src
                    target_port = packet.udp.srcport
                else:
                    stdout_crash_text = textwrap.dedent(f"""
                        ERROR:
                            Developer didn't expect this scenario to be possible.

                        INFOS:
                            Neither source nor destination IP address matches the specified <CAPTURE_IP_ADDRESS>.

                        DEBUG:
                            Settings.CAPTURE_IP_ADDRESS: {Settings.CAPTURE_IP_ADDRESS}
                            packet.ip.src: {packet.ip.src}
                            packet.ip.dst: {packet.ip.dst}
                    """.removeprefix("\n").removesuffix("\n"))
                    terminate_script("EXIT", stdout_crash_text, stdout_crash_text)
            else:
                is_src_private_ip = is_private_device_ipv4(packet.ip.src)
                is_dst_private_ip = is_private_device_ipv4(packet.ip.dst)

                if is_src_private_ip and is_dst_private_ip:
                    return
                elif is_src_private_ip:
                    target_ip = packet.ip.dst
                    target_port = packet.udp.dstport
                elif is_dst_private_ip:
                    target_ip = packet.ip.src
                    target_port = packet.udp.srcport
                else:
                    stdout_crash_text = textwrap.dedent(f"""
                        ERROR:
                            Developer didn't expect this scenario to be possible.

                        INFOS:
                            Neither source nor destination is a private IP address.

                        DEBUG:
                            packet.ip.src: {packet.ip.src}
                            packet.ip.dst: {packet.ip.dst}
                    """.removeprefix("\n").removesuffix("\n"))
                    terminate_script("EXIT", stdout_crash_text, stdout_crash_text)

            if target_port is None:
                stdout_crash_text = textwrap.dedent(f"""
                    ERROR:
                        Developer didn't expect this scenario to be possible.

                    INFOS:
                        A player port was not found.
                        This situation already happened to me,
                        but at this time I had not the `target_ip` info
                        from the packet, so it was useless.

                        Note for the future:
                        If `target_ip` is a false positive (not a player),
                        always `continue` on a packet with no port.

                    DEBUG:
                        target_ip: {target_ip}
                        target_port: {target_port}
                """.removeprefix("\n").removesuffix("\n"))
                terminate_script("EXIT", stdout_crash_text, stdout_crash_text)

            global_pps_counter += 1

            player = PlayersRegistry.get_player(target_ip)
            if player is None:
                player = PlayersRegistry.add_player(
                    Player(target_ip, target_port, packet_datetime)
                )

            if Settings.USERIP_ENABLED:
                if target_ip in UserIP_Databases.ips_set and not player.userip.detection.as_processed_userip_task:
                    player.userip.detection.as_processed_userip_task = True
                    player.userip.detection.type = "Static IP"
                    player.userip.detection.time = packet_datetime.strftime("%H:%M:%S")
                    player.userip.detection.date_time = packet_datetime.strftime("%Y-%m-%d_%H:%M:%S")
                    if UserIP_Databases.update_player_userip_data(player):
                        threading.Thread(target=process_userip_task, args=(player, "connected"), daemon=True).start()

            if player.is_player_just_registered:
                player.is_player_just_registered = False
                return

            # No matter what:
            player.datetime.last_seen = packet_datetime
            player.total_packets += 1
            player.pps.counter += 1

            if player.datetime.left: # player left, rejoined now.
                player.datetime.left = None
                player.datetime.last_rejoin = packet_datetime
                player.rejoins += 1
                player.packets = 1

                if Settings.STDOUT_RESET_PORTS_ON_REJOINS:
                    player.ports.reset(target_port)
                    return
            else:
                player.packets += 1

            # player connected, has not been reset
            if target_port not in player.ports.list:
                player.ports.list.append(target_port)
            player.ports.last = target_port

        while not gui_closed__event.is_set():
            try:
                capture.apply_on_packets(callback=packet_callback)
            except TSharkCrashException: # IDK why it's not working :/
                if gui_closed__event.is_set():
                    return
                raise
            except PacketCaptureOverflow:
                continue

tshark_packets_latencies: list[tuple[datetime, timedelta]] = []

class GUIrenderingData:
    FIELDS_TO_HIDE: list[str] = []
    GUI_CONNECTED_PLAYERS_TABLE__FIELD_NAMES: list[str] = []
    GUI_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES: list[str] = []
    is_rendering_core_ready: bool = False
    rpc_message: str = ""
    avg_latency_rounded: float = 0.0
    global_pps_rate: int = 0
    pps_color: str = '<span style="color: green;">'
    latency_color: str = '<span style="color: green;">'
    color_tshark_restarted_time: str = '<span style="color: green;">'
    session_connected: list[Player] = []
    session_disconnected: list[Player] = []

def rendering_core():
    with Threads_ExceptionHandler():
        from Modules.consts import ANSI_ESCAPE, HEADER_TEXT_SEPARATOR

        def print_in_header(text: str):
            from Modules.consts import HEADER_TEXT_MAX_LENGTH

            def calculate_padding_width(total_width: int, *lengths: int):
                """
                Calculate the padding width based on the total width and the lengths of provided strings.

                Args:
                    total_width: Total width available for padding
                    *lengths: Integrers for which lengths are used to calculate padding width

                Returns:
                    padding_width: Calculated padding width
                """
                # Calculate the total length of all strings
                total_length = sum(length for length in lengths)

                # Calculate the padding width
                padding_width = max(0, (total_width - total_length) // 2)

                return padding_width

            padding_width = calculate_padding_width(HEADER_TEXT_MAX_LENGTH, len(ANSI_ESCAPE.sub("", text)))
            return f"{' ' * padding_width}{text}"

        def compile_header_text():
            def format_network_interface_message():
                """Generates a formatted message for the network interface being scanned."""
                global displayed_capture_ip_address, is_arp_enabled

                is_arp_enabled = "Enabled" if interfaces_options[user_interface_selection]["is_arp"] else "Disabled"
                displayed_capture_ip_address = Settings.CAPTURE_IP_ADDRESS if Settings.CAPTURE_IP_ADDRESS else "N/A"

                return print_in_header(f"Scanning on interface:{Fore.YELLOW}{capture.interface}{Fore.RESET} at IP:{Fore.YELLOW}{displayed_capture_ip_address}{Fore.RESET} (ARP:{Fore.YELLOW}{is_arp_enabled}{Fore.RESET})")

            from Modules.consts import HEADER_TEXT_MIDDLE_SEPARATOR

            header = [""]
            if Settings.STDOUT_SHOW_ADVERTISING_HEADER:
                header.append(HEADER_TEXT_SEPARATOR)
                header.append(f"{UNDERLINE}Advertising{UNDERLINE_RESET}:")
                header.append("  * https://github.com/BUZZARDGTA")
                header.append("")
                header.append(f"{UNDERLINE}Contact Details{UNDERLINE_RESET}:")
                header.append("    You can contact me from Email: BUZZARDGTA@protonmail.com, Discord: waitingforharukatoaddme or Telegram: https://t.me/waitingforharukatoaddme")
                header.append("")
            header.append(HEADER_TEXT_SEPARATOR)
            header.append(print_in_header(f"Welcome in {TITLE} {VERSION}"))
            header.append(print_in_header("The best FREE and Open-Source packet sniffer aka IP grabber, works WITHOUT mods."))
            header.append(HEADER_TEXT_MIDDLE_SEPARATOR)
            header.append(format_network_interface_message())
            return "\n".join(header)

        def generate_field_names():
            def add_down_arrow_char_to_sorted_table_field(field_names: list[str], target_field: str):
                updated_field_names = [
                    field + " \u2193" if field == target_field else field
                    for field in field_names
                ]
                return updated_field_names

            stdout_connected_players_table__field_names = [
                field_name
                for field_name in Settings.stdout_all_connected_fields
                if (Settings.USERIP_ENABLED or field_name != "Usernames") and field_name not in FIELDS_TO_HIDE_IN_STDOUT
            ]
            stdout_disconnected_players_table__field_names = [
                field_name
                for field_name in Settings.stdout_all_disconnected_fields
                if (Settings.USERIP_ENABLED or field_name != "Usernames") and field_name not in FIELDS_TO_HIDE_IN_STDOUT
            ]
            logging_connected_players_table__field_names = [
                field_name
                for field_name in Settings.stdout_all_connected_fields
                if (Settings.USERIP_ENABLED or field_name != "Usernames")
            ]
            logging_disconnected_players_table__field_names = [
                field_name
                for field_name in Settings.stdout_all_disconnected_fields
                if (Settings.USERIP_ENABLED or field_name != "Usernames")
            ]

            return (
                add_down_arrow_char_to_sorted_table_field(stdout_connected_players_table__field_names, Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY),
                add_down_arrow_char_to_sorted_table_field(stdout_disconnected_players_table__field_names, Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY),
                add_down_arrow_char_to_sorted_table_field(logging_connected_players_table__field_names, Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY),
                add_down_arrow_char_to_sorted_table_field(logging_disconnected_players_table__field_names, Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY),
                stdout_connected_players_table__field_names,
                stdout_disconnected_players_table__field_names
            )

        def parse_userip_ini_file(ini_path: Path, unresolved_ip_invalid: set[str]):
            def process_ini_line_output(line: str):
                return line.strip()

            from Modules.consts import RE_SETTINGS_INI_PARSER_PATTERN, USERIP_INI_SETTINGS_LIST, RE_USERIP_INI_PARSER_PATTERN

            if not ini_path.exists():
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT),
                                        str(ini_path.absolute()))
            if not ini_path.is_file():
                raise InvalidFileError(str(ini_path.absolute()))

            settings: dict[str, Union[bool, str, int, float]] = {}
            userip: dict[str, list[str]] = {}
            current_section = None
            matched_settings = []
            ini_data = ini_path.read_text("utf-8")
            corrected_ini_data_lines = []

            for line in map(process_ini_line_output, ini_data.splitlines(keepends=True)):
                corrected_ini_data_lines.append(line)

                if line.startswith("[") and line.endswith("]"):
                    # we basically adding a newline if the previous line is not a newline for eyes visiblitly or idk how we say that
                    if corrected_ini_data_lines and len(corrected_ini_data_lines) > 1:
                        if not corrected_ini_data_lines[-2] == "":
                            corrected_ini_data_lines.insert(-1, "")  # Insert an empty string before the last line
                    current_section = line[1:-1]
                    continue

                if current_section is None:
                    continue

                elif current_section == "Settings":
                    match = RE_SETTINGS_INI_PARSER_PATTERN.search(line)
                    if not match:
                        # If it's a newline or a comment we don't really care about rewritting at this point.
                        if not line.startswith((";", "#")) or line == "":
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue

                    setting = match.group("key")
                    if setting is None:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue
                    if not isinstance(setting, str):
                        raise TypeError(f'Expected "str" object, got "{type(setting)}"')
                    value = match.group("value")
                    if value is None:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue
                    if not isinstance(value, str):
                        raise TypeError(f'Expected "str" object, got "{type(value)}"')

                    setting = setting.strip()
                    if not setting:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue
                    value = value.strip()
                    if not value:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue

                    if not setting in USERIP_INI_SETTINGS_LIST:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue

                    if setting in settings:
                        if corrected_ini_data_lines:
                            corrected_ini_data_lines = corrected_ini_data_lines[:-1]
                        continue
                    else:
                        matched_settings.append(setting)
                        need_rewrite_current_setting = False
                        is_setting_corrupted = False

                        if setting == "ENABLED":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_bool(value)
                            except InvalidBooleanValueError:
                                is_setting_corrupted = True
                        elif setting == "COLOR":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_nonetype(value)
                            except InvalidNoneTypeValueError:
                                case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(value, ["BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE"])
                                if case_insensitive_match:
                                    settings[setting] = normalized_match
                                    if not case_sensitive_match:
                                        need_rewrite_current_setting = True
                                else:
                                    is_setting_corrupted = True
                        elif setting == "LOG":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_bool(value)
                            except InvalidBooleanValueError:
                                is_setting_corrupted = True
                        elif setting == "NOTIFICATIONS":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_bool(value)
                            except InvalidBooleanValueError:
                                is_setting_corrupted = True
                        elif setting == "VOICE_NOTIFICATIONS":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_bool(value, only_match_against=False)
                            except InvalidBooleanValueError:
                                case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(value, ["Male", "Female"])
                                if case_insensitive_match:
                                    settings[setting] = normalized_match
                                    if not case_sensitive_match:
                                        need_rewrite_current_setting = True
                                else:
                                    is_setting_corrupted = True
                        elif setting == "PROTECTION":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_bool(value, only_match_against=False)
                            except InvalidBooleanValueError:
                                case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(value, ["Suspend_Process", "Exit_Process", "Restart_Process", "Shutdown_PC", "Restart_PC"])
                                if case_insensitive_match:
                                    settings[setting] = normalized_match
                                    if not case_sensitive_match:
                                        need_rewrite_current_setting = True
                                else:
                                    is_setting_corrupted = True
                        elif setting == "PROTECTION_PROCESS_PATH":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_nonetype(value)
                            except InvalidNoneTypeValueError:
                                stripped_value = value.strip("\"'")
                                if not value == stripped_value:
                                    is_setting_corrupted = True
                                settings[setting] = Path(stripped_value.replace("\\", "/"))
                        elif setting == "PROTECTION_RESTART_PROCESS_PATH":
                            try:
                                settings[setting], need_rewrite_current_setting = custom_str_to_nonetype(value)
                            except InvalidNoneTypeValueError:
                                stripped_value = value.strip("\"'")
                                if not value == stripped_value:
                                    is_setting_corrupted = True
                                settings[setting] = Path(stripped_value.replace("\\", "/"))
                        elif setting == "PROTECTION_SUSPEND_PROCESS_MODE":
                                case_insensitive_match, case_sensitive_match, normalized_match = check_case_insensitive_and_exact_match(value, ["Auto", "Manual"])
                                if case_insensitive_match:
                                    settings[setting] = normalized_match
                                    if not case_sensitive_match:
                                        need_rewrite_current_setting = True
                                else:
                                    try:
                                        if "." in value:
                                            PROTECTION_SUSPEND_PROCESS_MODE = float(value)
                                        else:
                                            PROTECTION_SUSPEND_PROCESS_MODE = int(value)
                                    except (ValueError, TypeError):
                                        is_setting_corrupted = True
                                    else:
                                        if PROTECTION_SUSPEND_PROCESS_MODE >= 0:
                                            settings[setting] = PROTECTION_SUSPEND_PROCESS_MODE
                                        else:
                                            is_setting_corrupted = True

                        if is_setting_corrupted:
                            if not ini_path in UserIP_Databases.notified_settings_corrupted:
                                UserIP_Databases.notified_settings_corrupted.add(ini_path)
                                msgbox_title = TITLE
                                msgbox_message = textwrap.indent(textwrap.dedent(f"""
                                    ERROR:
                                        Corrupted UserIP Database File (Settings)

                                    INFOS:
                                        UserIP database file:
                                        \"{ini_path}\"
                                        has an invalid settings value:

                                        {setting}={value}

                                        For more information on formatting, please refer to the
                                        documentation:
                                        https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer?tab=readme-ov-file#userip_ini_databases_tutorial
                                """.removeprefix("\n").removesuffix("\n")), "    ")
                                msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
                                threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()
                            return None, None

                        if need_rewrite_current_setting:
                            corrected_ini_data_lines[-1] = f"{setting}={settings[setting]}"

                elif current_section == "UserIP":
                    match = RE_USERIP_INI_PARSER_PATTERN.search(line)
                    if not match:
                        continue
                    username = match.group("username")
                    if username is None:
                        continue
                    if not isinstance(username, str):
                        raise TypeError(f'Expected "str" object, got "{type(username)}"')
                    ip = match.group("ip")
                    if ip is None:
                        continue
                    if not isinstance(ip, str):
                        raise TypeError(f'Expected "str" object, got "{type(ip)}"')

                    username = username.strip()
                    if not username:
                        continue
                    ip = ip.strip()
                    if not ip:
                        continue

                    if not is_ipv4_address(ip):
                        unresolved_ip_invalid.add(f"{ini_path.name}={username}={ip}")
                        if not f"{ini_path.name}={username}={ip}" in UserIP_Databases.notified_ip_invalid:
                            msgbox_title = TITLE
                            msgbox_message = textwrap.indent(textwrap.dedent(f"""
                                ERROR:
                                    UserIP databases invalid IP address

                                INFOS:
                                    The IP address from an entry is invalid (not an IP address).

                                DEBUG:
                                    \"{ini_path}\":
                                    {username}={ip}
                            """.removeprefix("\n").removesuffix("\n")), "    ")
                            msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.SystemModal | MsgBox.Style.MsgBoxSetForeground
                            threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()
                            UserIP_Databases.notified_ip_invalid.add(f"{ini_path.name}={username}={ip}")
                        continue

                    if username in userip:
                        if ip not in userip[username]:
                            userip[username].append(ip)
                    else:
                        userip[username] = [ip]

            list_of_missing_settings = [setting for setting in USERIP_INI_SETTINGS_LIST if setting not in matched_settings]
            number_of_settings_missing = len(list_of_missing_settings)

            if number_of_settings_missing > 0:
                if not ini_path in UserIP_Databases.notified_settings_corrupted:
                    UserIP_Databases.notified_settings_corrupted.add(ini_path)
                    msgbox_title = TITLE
                    msgbox_message = textwrap.indent(textwrap.dedent(f"""
                        ERROR:
                            Missing setting{plural(number_of_settings_missing)} in UserIP Database File

                        INFOS:
                            {number_of_settings_missing} missing setting{plural(number_of_settings_missing)} in UserIP database file:
                            \"{ini_path}\"

                            {"\n                ".join(f"<{setting.upper()}>" for setting in list_of_missing_settings)}

                            For more information on formatting, please refer to the
                            documentation:
                            https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer?tab=readme-ov-file#userip_ini_databases_tutorial
                    """.removeprefix("\n").removesuffix("\n")), "    ")
                    msgbox_style = MsgBox.Style.OKOnly | MsgBox.Style.Exclamation | MsgBox.Style.MsgBoxSetForeground
                    threading.Thread(target=MsgBox.show, args=(msgbox_title, msgbox_message, msgbox_style), daemon=True).start()
                return None, None
            else:
                if ini_path in UserIP_Databases.notified_settings_corrupted:
                    UserIP_Databases.notified_settings_corrupted.remove(ini_path)

            # Basically always have a newline ending
            if len(corrected_ini_data_lines) > 1:
                if not corrected_ini_data_lines[-1] == "":
                    corrected_ini_data_lines.append("")

            fixed_ini_data = "\n".join(corrected_ini_data_lines)

            if not ini_data == fixed_ini_data:
                ini_path.write_text(fixed_ini_data, encoding="utf-8")

            return UserIP_Settings(
                settings["ENABLED"],
                settings["COLOR"],
                settings["LOG"],
                settings["NOTIFICATIONS"],
                settings["VOICE_NOTIFICATIONS"],
                settings["PROTECTION"],
                settings["PROTECTION_PROCESS_PATH"],
                settings["PROTECTION_RESTART_PROCESS_PATH"],
                settings["PROTECTION_SUSPEND_PROCESS_MODE"]
            ), userip

        def update_userip_databases(last_userip_parse_time: Optional[float]):
            from Modules.consts import USERIP_DATABASES_PATH

            DEFAULT_USERIP_FILE_HEADER = textwrap.dedent("""
                ;;-----------------------------------------------------------------------------
                ;; Session Sniffer User IP default database file
                ;;-----------------------------------------------------------------------------
                ;; Lines starting with \";\" or \"#\" symbols are commented lines.
                ;;
                ;; For detailed explanations of each setting, please refer to the following documentation:
                ;; https://github.com/BUZZARDGTA/GTA-V-Session-Sniffer/?tab=readme-ov-file#editing-settings
                ;;-----------------------------------------------------------------------------
                [Settings]
            """.removeprefix("\n").removesuffix("\n"))

            DEFAULT_USERIP_FILES_SETTINGS = {
                USERIP_DATABASES_PATH / "Blacklist.ini": """
                    ENABLED=True
                    COLOR=RED
                    LOG=True
                    NOTIFICATIONS=True
                    VOICE_NOTIFICATIONS=Male
                    PROTECTION=False
                    PROTECTION_PROCESS_PATH=None
                    PROTECTION_RESTART_PROCESS_PATH=None
                    PROTECTION_SUSPEND_PROCESS_MODE=Auto
                """,
                USERIP_DATABASES_PATH / "Enemylist.ini": """
                    ENABLED=True
                    COLOR=YELLOW
                    LOG=True
                    NOTIFICATIONS=True
                    VOICE_NOTIFICATIONS=Male
                    PROTECTION=False
                    PROTECTION_PROCESS_PATH=None
                    PROTECTION_RESTART_PROCESS_PATH=None
                    PROTECTION_SUSPEND_PROCESS_MODE=Auto
                """,
                USERIP_DATABASES_PATH / "Friendlist.ini": """
                    ENABLED=True
                    COLOR=GREEN
                    LOG=True
                    NOTIFICATIONS=False
                    VOICE_NOTIFICATIONS=Female
                    PROTECTION=False
                    PROTECTION_PROCESS_PATH=None
                    PROTECTION_RESTART_PROCESS_PATH=None
                    PROTECTION_SUSPEND_PROCESS_MODE=Auto
                """,
                USERIP_DATABASES_PATH / "Randomlist.ini": """
                    ENABLED=True
                    COLOR=BLACK
                    LOG=True
                    NOTIFICATIONS=False
                    VOICE_NOTIFICATIONS=Female
                    PROTECTION=False
                    PROTECTION_PROCESS_PATH=None
                    PROTECTION_RESTART_PROCESS_PATH=None
                    PROTECTION_SUSPEND_PROCESS_MODE=Auto
                """,
                USERIP_DATABASES_PATH / "Searchlist.ini": """
                    ENABLED=True
                    COLOR=BLUE
                    LOG=True
                    NOTIFICATIONS=False
                    VOICE_NOTIFICATIONS=Female
                    PROTECTION=False
                    PROTECTION_PROCESS_PATH=None
                    PROTECTION_RESTART_PROCESS_PATH=None
                    PROTECTION_SUSPEND_PROCESS_MODE=Auto
                """
            }

            DEFAULT_USERIP_FILE_FOOTER = textwrap.dedent("""
                [UserIP]
                # Add users below in this format: username=IP
                # Examples:
                # username1=192.168.1.1
                # username2=127.0.0.1
                # username3=255.255.255.255
            """.removeprefix("\n"))

            USERIP_DATABASES_PATH.mkdir(parents=True, exist_ok=True)

            for userip_path, settings in DEFAULT_USERIP_FILES_SETTINGS.items():
                if not userip_path.exists():
                    file_content = f"{DEFAULT_USERIP_FILE_HEADER}\n\n{settings}\n\n{DEFAULT_USERIP_FILE_FOOTER}"
                    userip_path.write_text(file_content, encoding="utf-8")

            # Remove deleted files from notified settings conflicts
            # TODO:
            # I should also warn again on another error, but it'd probably require a DICT then.
            # I have things more important to code atm.
            for path in set(UserIP_Databases.notified_settings_corrupted):
                if not path.exists() or not path.is_file():
                    UserIP_Databases.notified_settings_corrupted.remove(path)

            UserIP_Databases.reset()

            unresolved_ip_invalid: set[str] = set()

            for userip_path in USERIP_DATABASES_PATH.glob("*.ini"):
                parsed_settings, parsed_data = parse_userip_ini_file(userip_path, unresolved_ip_invalid)
                if parsed_settings is None or parsed_data is None:
                    continue
                UserIP_Databases.add(userip_path.stem, parsed_settings, parsed_data)

            resolved_ip_invalids = UserIP_Databases.notified_ip_invalid - unresolved_ip_invalid
            for resolved_database_entry in resolved_ip_invalids:
                UserIP_Databases.notified_ip_invalid.remove(resolved_database_entry)

            UserIP_Databases.build()

            last_userip_parse_time = time.perf_counter()
            return last_userip_parse_time

        def get_country_info(ip_address: str):
            country_name = "N/A"
            country_code = "N/A"

            if geoip2_enabled:
                try:
                    response = geolite2_country_reader.country(ip_address)
                except geoip2.errors.AddressNotFoundError:
                    pass
                else:
                    country_name = str(response.country.name)
                    country_code = str(response.country.iso_code)

            return country_name, country_code

        def get_city_info(ip_address: str):
            city = "N/A"

            if geoip2_enabled:
                try:
                    response = geolite2_city_reader.city(ip_address)
                except geoip2.errors.AddressNotFoundError:
                    pass
                else:
                    city = str(response.city.name)

            return city

        def get_asn_info(ip_address: str):
            asn = "N/A"

            if geoip2_enabled:
                try:
                    response = geolite2_asn_reader.asn(ip_address)
                except geoip2.errors.AddressNotFoundError:
                    pass
                else:
                    asn = str(response.autonomous_system_organization)

            return asn

        def get_minimum_padding(var: Union[str, float, int, bool], max_padding: int, padding: int):
            current_padding = len(str(var))

            if current_padding <= padding:
                if current_padding > max_padding:
                    max_padding = current_padding

            return max_padding

        def sort_session_table(session_list: list[Player], sorted_column_name: str, sorted_key: str) -> list[Player]:
            """
            Sorts a list of players based on the given column name.

            Args:
                session_list: The list of players to sort.
                sorted_column_name: The column name to sort by.
                sorted_key: The compiled default sorted key attribute to sort by.

            Returns:
                list[Player]: The sorted list of players.
            """
            import ipaddress

            must_reverse_sort = False
            if sorted_column_name in ["T. Packets", "Packets", "PPS"]:
                must_reverse_sort = True

            if sorted_column_name == "IP Address":
                # Sort players by their IP addresses using ipaddress module
                return sorted(
                    session_list,
                    key=lambda player: ipaddress.ip_address(player.ip),
                    reverse=must_reverse_sort
                )
            else:
                # Handle sorting for other columns
                return sorted(
                    session_list,
                    key=attrgetter(sorted_key),
                    reverse=must_reverse_sort
                )

        def format_player_stdout_datetime(datetime_object: datetime):
            formatted_elapsed = None

            if Settings.STDOUT_DATE_FIELDS_SHOW_ELAPSED:
                elapsed_time = datetime.now() - datetime_object

                hours, remainder = divmod(elapsed_time.total_seconds(), 3600)
                minutes, remainder = divmod(remainder, 60)
                seconds, milliseconds = divmod(remainder * 1000, 1000)

                elapsed_parts: list[str] = []
                if hours >= 1:
                    elapsed_parts.append(f"{int(hours):02}h")
                if elapsed_parts or minutes >= 1:
                    elapsed_parts.append(f"{int(minutes):02}m")
                if elapsed_parts or seconds >= 1:
                    elapsed_parts.append(f"{int(seconds):02}s")
                if not elapsed_parts and milliseconds > 0:
                    elapsed_parts.append(f"{int(milliseconds):03}ms")

                formatted_elapsed = " ".join(elapsed_parts)

                if Settings.STDOUT_DATE_FIELDS_SHOW_DATE is False and Settings.STDOUT_DATE_FIELDS_SHOW_TIME is False:
                    return formatted_elapsed

            parts = []
            if Settings.STDOUT_DATE_FIELDS_SHOW_DATE:
                parts.append(datetime_object.strftime("%m/%d/%Y"))
            if Settings.STDOUT_DATE_FIELDS_SHOW_TIME:
                parts.append(datetime_object.strftime("%H:%M:%S.%f")[:-3])

            formatted_stdout_datetime = " ".join(parts)
            if not formatted_stdout_datetime:
                raise ValueError("Invalid settings: Both date and time are disabled.")

            if formatted_elapsed:
                formatted_stdout_datetime += f" ({formatted_elapsed})"

            return formatted_stdout_datetime

        def format_player_logging_datetime(datetime_object: datetime):
            return f"{datetime_object.strftime('%m/%d/%Y %H:%M:%S.%f')[:-3]}"

        def format_player_logging_usernames(player_usernames: list[str]):
            return f"{', '.join(player_usernames) if player_usernames else 'N/A'}"

        def format_player_logging_ip(player_ip: str):
            if (
                SessionHost.player
                and SessionHost.player.ip == player_ip
            ):
                return f"{player_ip} 👑"
            return player_ip

        def format_player_logging_intermediate_ports(player_ports: Player_Ports):
            player_ports.intermediate = [port for port in reversed(player_ports.list) if port not in {player_ports.first, player_ports.last}]
            if player_ports.intermediate:
                return ", ".join(map(str, player_ports.intermediate))
            else:
                return ""

        from Modules.consts import TWO_TAKE_ONE__PLUGIN__LOG_PATH, STAND__PLUGIN__LOG_PATH, CHERAX__PLUGIN__LOG_PATH, RE_MODMENU_LOGS_USER_PATTERN

        global iplookup_core__thread, global_pps_counter, tshark_packets_latencies

        SESSION_CONNECTED_SORTED_KEY = Settings.stdout_fields_mapping[Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY]
        SESSION_DISCONNECTED_SORTED_KEY = Settings.stdout_fields_mapping[Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY]
        FIELDS_TO_HIDE_IN_STDOUT = GUIrenderingData.FIELDS_TO_HIDE = set(Settings.STDOUT_FIELDS_TO_HIDE)
        HEADER_TEXT = compile_header_text()

        (
            STDOUT_CONNECTED_PLAYERS_TABLE__FIELD_NAMES,
            STDOUT_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES,
            LOGGING_CONNECTED_PLAYERS_TABLE__FIELD_NAMES,
            LOGGING_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES,
            GUIrenderingData.GUI_CONNECTED_PLAYERS_TABLE__FIELD_NAMES,
            GUIrenderingData.GUI_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES,
        ) = generate_field_names()

        global_pps_t1 = time.perf_counter()
        global_pps_rate = 0
        last_userip_parse_time = None
        last_mod_menus_logs_parse_time = None

        if Settings.DISCORD_PRESENCE:
            from Modules.discord.rpc import DiscordRPCManager

            discord_rpc_manager = DiscordRPCManager()

        modmenu__plugins__ip_to_usernames: dict[str, list[str]] = {}
        # NOTE: The log file content is read only once because the plugin is no longer supported.
        if TWO_TAKE_ONE__PLUGIN__LOG_PATH.exists() and TWO_TAKE_ONE__PLUGIN__LOG_PATH.is_file():
            with TWO_TAKE_ONE__PLUGIN__LOG_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    match = RE_MODMENU_LOGS_USER_PATTERN.match(line)
                    if match:
                        username = match.group("username")
                        if not isinstance(username, str):
                            continue

                        ip = match.group("ip")
                        if not isinstance(ip, str):
                            continue

                        if ip not in modmenu__plugins__ip_to_usernames:
                            modmenu__plugins__ip_to_usernames[ip] = []
                        if not username in modmenu__plugins__ip_to_usernames[ip]:
                            modmenu__plugins__ip_to_usernames[ip].append(username)

        while not gui_closed__event.is_set():
            if ScriptControl.has_crashed():
                return

            session_connected__padding_country_name = 0
            session_connected__padding_continent_name = 0
            session_disconnected__padding_country_name = 0
            session_disconnected__padding_continent_name = 0
            session_connected: list[Player] = []
            session_disconnected: list[Player] = []

            if last_mod_menus_logs_parse_time is None or time.perf_counter() - last_mod_menus_logs_parse_time >= 1.0:
                last_mod_menus_logs_parse_time = time.perf_counter()

                for log_path in [STAND__PLUGIN__LOG_PATH, CHERAX__PLUGIN__LOG_PATH]:
                    if log_path.exists() and log_path.is_file():
                        with log_path.open("r", encoding="utf-8") as f:
                            for line in f:
                                match = RE_MODMENU_LOGS_USER_PATTERN.match(line)
                                if match:
                                    username = match.group("username")
                                    if not isinstance(username, str):
                                        continue

                                    ip = match.group("ip")
                                    if not isinstance(ip, str):
                                        continue

                                    if ip not in modmenu__plugins__ip_to_usernames:
                                        modmenu__plugins__ip_to_usernames[ip] = []
                                    if not username in modmenu__plugins__ip_to_usernames[ip]:
                                        modmenu__plugins__ip_to_usernames[ip].append(username)

            if Settings.USERIP_ENABLED:
                if last_userip_parse_time is None or time.perf_counter() - last_userip_parse_time >= 1.0:
                    last_userip_parse_time = update_userip_databases(last_userip_parse_time)

            for player in PlayersRegistry.iterate_players_from_registry():
                if Settings.USERIP_ENABLED:
                    if player.ip in UserIP_Databases.ips_set:
                        UserIP_Databases.update_player_userip_data(player)
                    else:
                        player.userip.reset()

                if modmenu__plugins__ip_to_usernames and player.ip in modmenu__plugins__ip_to_usernames:
                    for username in modmenu__plugins__ip_to_usernames[player.ip]:
                        if username not in player.mod_menus.usernames:
                            player.mod_menus.usernames.append(username)

                player.usernames = concat_lists_no_duplicates(player.mod_menus.usernames, player.userip.usernames)

                if (
                    not player.datetime.left
                    and (datetime.now() - player.datetime.last_seen).total_seconds() >= Settings.STDOUT_DISCONNECTED_PLAYERS_TIMER
                ):
                    player.datetime.left = player.datetime.last_seen
                    if Settings.USERIP_ENABLED:
                        if player.userip.detection.time:
                            player.userip.detection.as_processed_userip_task = False
                            threading.Thread(target=process_userip_task, args=(player, "disconnected"), daemon=True).start()

                if not player.iplookup.maxmind.is_initialized:
                    player.iplookup.maxmind.country, player.iplookup.maxmind.country_code = get_country_info(player.ip)
                    player.iplookup.maxmind.city = get_city_info(player.ip)
                    player.iplookup.maxmind.asn = get_asn_info(player.ip)

                    player.iplookup.maxmind.compile()
                    player.iplookup.maxmind.is_initialized = True

                if player.datetime.left:
                    session_disconnected.append(player)
                else:
                    session_connected__padding_country_name = get_minimum_padding(player.iplookup.maxmind.compiled.country, session_connected__padding_country_name, 27)
                    session_connected__padding_continent_name = get_minimum_padding(player.iplookup.ipapi.compiled.continent, session_connected__padding_continent_name, 13)

                    if (player_timedelta := (datetime.now() - player.pps.t1)).total_seconds() >= 1.0:
                        player.pps.rate = round(player.pps.counter / player_timedelta.total_seconds())
                        player.pps.counter = 0
                        player.pps.t1 = datetime.now()
                        player.pps.is_first_calculation = False

                    session_connected.append(player)

            GUIrenderingData.session_connected = session_connected
            GUIrenderingData.session_disconnected = session_disconnected

            if Settings.CAPTURE_PROGRAM_PRESET == "GTA5":
                if SessionHost.player:
                    if SessionHost.player.datetime.left:
                        SessionHost.player = None
                # TODO: We should also potentially needs to check that not more then 1s passed before each disconnected
                if SessionHost.players_pending_for_disconnection and all(player.datetime.left for player in SessionHost.players_pending_for_disconnection):
                    SessionHost.player = None
                    SessionHost.search_player = True
                    SessionHost.players_pending_for_disconnection = []

                if len(session_connected) == 0:
                    SessionHost.player = None
                    SessionHost.search_player = True
                    SessionHost.players_pending_for_disconnection = []
                elif len(session_connected) >= 1 and all(not player.pps.is_first_calculation and player.pps.rate == 0 for player in session_connected):
                    SessionHost.players_pending_for_disconnection = session_connected
                else:
                    if SessionHost.search_player:
                        SessionHost.get_host_player(session_connected)

            session_connected_sorted = sort_session_table(
                session_connected,
                Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY,
                SESSION_CONNECTED_SORTED_KEY
            )
            session_disconnected_sorted = sort_session_table(
                session_disconnected,
                Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY,
                SESSION_DISCONNECTED_SORTED_KEY
            )

            if Settings.STDOUT_DISCONNECTED_PLAYERS_COUNTER > 0:
                session_disconnected_sorted = take(Settings.STDOUT_DISCONNECTED_PLAYERS_COUNTER, session_disconnected_sorted)

            for player in session_disconnected_sorted:
                session_disconnected__padding_country_name = get_minimum_padding(player.iplookup.maxmind.compiled.country, session_disconnected__padding_country_name, 27)
                session_disconnected__padding_continent_name = get_minimum_padding(player.iplookup.ipapi.compiled.continent, session_disconnected__padding_continent_name, 13)

            current_time = datetime.now()
            one_second_ago = current_time - timedelta(seconds=1)

            # Filter packets received in the last second
            recent_packets = [(pkt_time, pkt_latency) for pkt_time, pkt_latency in tshark_packets_latencies if pkt_time >= one_second_ago]

            # Update latencies list to only keep recent packets
            tshark_packets_latencies[:] = recent_packets

            # Calculate average latency
            if recent_packets:
                total_latency_seconds = sum(pkt_latency.total_seconds() for _, pkt_latency in recent_packets)
                avg_latency_seconds = total_latency_seconds / len(recent_packets)
                GUIrenderingData.avg_latency_rounded = round(avg_latency_seconds, 1)
            else:
                avg_latency_seconds = 0.0
                GUIrenderingData.avg_latency_rounded = 0.0

            # Determine latency color
            if avg_latency_seconds >= 0.90 * Settings.CAPTURE_OVERFLOW_TIMER:
                latency_color = Fore.RED
                GUIrenderingData.latency_color = '<span style="color: red;">'
            elif avg_latency_seconds >= 0.75 * Settings.CAPTURE_OVERFLOW_TIMER:
                latency_color = Fore.YELLOW
                GUIrenderingData.latency_color = '<span style="color: yellow;">'
            else:
                latency_color = Fore.GREEN
                GUIrenderingData.latency_color = '<span style="color: green;">'

            seconds_elapsed = time.perf_counter() - global_pps_t1
            if seconds_elapsed >= 1:
                global_pps_rate = GUIrenderingData.global_pps_rate = round(global_pps_counter / seconds_elapsed)
                global_pps_counter = 0
                global_pps_t1 = time.perf_counter()

            # For reference, in a GTA Online session, the packets per second (PPS) typically range from 0 (solo session) to 1500 (public session, 32 players).
            # If the packet rate exceeds these ranges, we flag them with yellow or red color to indicate potential issues (such as scanning unwanted packets outside of the GTA game).
            # Also these values averagely indicates the max performances my script can run at during my testings. Luckely it's just enough to process GTA V game.
            if global_pps_rate >= 3000:
                GUIrenderingData.pps_color = '<span style="color: red;">'
            elif global_pps_rate >= 1500:
                GUIrenderingData.pps_color = '<span style="color: yellow;">'
            else:
                GUIrenderingData.pps_color = '<span style="color: green;">'

            GUIrenderingData.color_tshark_restarted_time = '<span style="color: green;">' if tshark_restarted_times == 0 else '<span style="color: red;">'
            num_of_userip_files = len(UserIP_Databases.userip_databases)
            if Settings.DISCORD_PRESENCE:
                GUIrenderingData.rpc_message = f' RPC: <span style="color: green;">Connected</span>' if discord_rpc_manager.is_connected else f' RPC: <span style="color: yellow;">Waiting for Discord</span>'
            else:
                GUIrenderingData.rpc_message = ""

            # TODO:
            #printer.cache_print(print_in_header(f"Packets latency per sec:{latency_color}{avg_latency_rounded}{Fore.RESET}/{Fore.GREEN}{Settings.CAPTURE_OVERFLOW_TIMER}{Fore.RESET} (tshark restart{plural(tshark_restarted_times)}:{color_tshark_restarted_time}{tshark_restarted_times}{Fore.RESET}) PPS:{pps_color}{global_pps_rate}{Fore.RESET}{rpc_message}"))
            #if number_of_ip_invalid_in_userip_files := len(UserIP_Databases.notified_ip_invalid):
            #    printer.cache_print(print_in_header(f"Number of invalid IP{plural(number_of_ip_invalid_in_userip_files)} in UserIP file{plural(num_of_userip_files)}: {Fore.RED}{number_of_ip_invalid_in_userip_files}{Fore.RESET}"))
            #if number_of_conflicting_ip_in_userip_files := len(UserIP_Databases.notified_ip_conflicts):
            #    printer.cache_print(print_in_header(f"Number of conflicting IP{plural(number_of_conflicting_ip_in_userip_files)} in UserIP file{plural(num_of_userip_files)}: {Fore.RED}{number_of_conflicting_ip_in_userip_files}{Fore.RESET}"))
            #if number_of_corrupted_userip_settings_files := len(UserIP_Databases.notified_settings_corrupted):
            #    printer.cache_print(print_in_header(f"Number of corrupted setting(s) in UserIP file{plural(num_of_userip_files)}: {Fore.RED}{number_of_corrupted_userip_settings_files}{Fore.RESET}"))
            #printer.cache_print(HEADER_TEXT_SEPARATOR)

            if Settings.STDOUT_SESSIONS_LOGGING:
                from Modules.consts import SESSIONS_LOGGING_PATH

                logging_connected_players_table = PrettyTable()
                logging_connected_players_table.set_style(TableStyle.SINGLE_BORDER)
                logging_connected_players_table.title = f"Player{plural(len(session_connected_sorted))} connected in your session ({len(session_connected_sorted)}):"
                logging_connected_players_table.field_names = LOGGING_CONNECTED_PLAYERS_TABLE__FIELD_NAMES
                logging_connected_players_table.align = "l"
                for player in session_connected_sorted:
                    row = []
                    row.append(f"{format_player_logging_datetime(player.datetime.first_seen)}")
                    row.append(f"{format_player_logging_datetime(player.datetime.last_rejoin)}")
                    row.append(f"{format_player_logging_usernames(player.usernames)}")
                    row.append(f"{player.rejoins}")
                    row.append(f"{player.total_packets}")
                    row.append(f"{player.packets}")
                    row.append(f"{player.pps.rate}")
                    row.append(f"{format_player_logging_ip(player.ip)}")
                    row.append(f"{player.ports.last}")
                    row.append(f"{format_player_logging_intermediate_ports(player.ports)}")
                    row.append(f"{player.ports.first}")
                    row.append(f"{player.iplookup.ipapi.compiled.continent:<{session_connected__padding_continent_name}} ({player.iplookup.ipapi.compiled.continent_code})")
                    row.append(f"{player.iplookup.maxmind.compiled.country:<{session_connected__padding_country_name}} ({player.iplookup.maxmind.compiled.country_code})")
                    row.append(f"{player.iplookup.ipapi.compiled.region}")
                    row.append(f"{player.iplookup.ipapi.compiled.region_code}")
                    row.append(f"{player.iplookup.maxmind.compiled.city}")
                    row.append(f"{player.iplookup.ipapi.compiled.district}")
                    row.append(f"{player.iplookup.ipapi.compiled.zip_code}")
                    row.append(f"{player.iplookup.ipapi.compiled.lat}")
                    row.append(f"{player.iplookup.ipapi.compiled.lon}")
                    row.append(f"{player.iplookup.ipapi.compiled.time_zone}")
                    row.append(f"{player.iplookup.ipapi.compiled.offset}")
                    row.append(f"{player.iplookup.ipapi.compiled.currency}")
                    row.append(f"{player.iplookup.ipapi.compiled.org}")
                    row.append(f"{player.iplookup.ipapi.compiled.isp}")
                    row.append(f"{player.iplookup.maxmind.compiled.asn}")
                    row.append(f"{player.iplookup.ipapi.compiled._as}")
                    row.append(f"{player.iplookup.ipapi.compiled.as_name}")
                    row.append(f"{player.iplookup.ipapi.compiled.mobile}")
                    row.append(f"{player.iplookup.ipapi.compiled.proxy}")
                    row.append(f"{player.iplookup.ipapi.compiled.hosting}")
                    logging_connected_players_table.add_row(row)

                logging_disconnected_players_table = PrettyTable()
                logging_disconnected_players_table.set_style(TableStyle.SINGLE_BORDER)
                logging_disconnected_players_table.title = f"Player{plural(len(session_disconnected))} who've left your session ({len(session_disconnected)}):"
                logging_disconnected_players_table.field_names = LOGGING_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES
                logging_disconnected_players_table.align = "l"
                for player in session_disconnected:
                    row = []
                    row.append(f"{format_player_logging_datetime(player.datetime.first_seen)}")
                    row.append(f"{format_player_logging_datetime(player.datetime.last_rejoin)}")
                    row.append(f"{format_player_logging_datetime(player.datetime.last_seen)}")
                    row.append(f"{format_player_logging_usernames(player.usernames)}")
                    row.append(f"{player.rejoins}")
                    row.append(f"{player.total_packets}")
                    row.append(f"{player.packets}")
                    row.append(f"{player.ip}")
                    row.append(f"{player.ports.last}")
                    row.append(f"{format_player_logging_intermediate_ports(player.ports)}")
                    row.append(f"{player.ports.first}")
                    row.append(f"{player.iplookup.ipapi.compiled.continent:<{session_disconnected__padding_continent_name}} ({player.iplookup.ipapi.compiled.continent_code})")
                    row.append(f"{player.iplookup.maxmind.compiled.country:<{session_disconnected__padding_country_name}} ({player.iplookup.maxmind.compiled.country_code})")
                    row.append(f"{player.iplookup.ipapi.compiled.region}")
                    row.append(f"{player.iplookup.ipapi.compiled.region_code}")
                    row.append(f"{player.iplookup.maxmind.compiled.city}")
                    row.append(f"{player.iplookup.ipapi.compiled.district}")
                    row.append(f"{player.iplookup.ipapi.compiled.zip_code}")
                    row.append(f"{player.iplookup.ipapi.compiled.lat}")
                    row.append(f"{player.iplookup.ipapi.compiled.lon}")
                    row.append(f"{player.iplookup.ipapi.compiled.time_zone}")
                    row.append(f"{player.iplookup.ipapi.compiled.offset}")
                    row.append(f"{player.iplookup.ipapi.compiled.currency}")
                    row.append(f"{player.iplookup.ipapi.compiled.org}")
                    row.append(f"{player.iplookup.ipapi.compiled.isp}")
                    row.append(f"{player.iplookup.maxmind.compiled.asn}")
                    row.append(f"{player.iplookup.ipapi.compiled._as}")
                    row.append(f"{player.iplookup.ipapi.compiled.as_name}")
                    row.append(f"{player.iplookup.ipapi.compiled.mobile}")
                    row.append(f"{player.iplookup.ipapi.compiled.proxy}")
                    row.append(f"{player.iplookup.ipapi.compiled.hosting}")
                    logging_disconnected_players_table.add_row(row)

                # Check if the directories exist, if not create them
                if not SESSIONS_LOGGING_PATH.parent.exists():
                    SESSIONS_LOGGING_PATH.parent.mkdir(parents=True)  # Create the directories if they don't exist

                # Check if the file exists, if not create it
                if not SESSIONS_LOGGING_PATH.exists():
                    SESSIONS_LOGGING_PATH.touch()  # Create the file if it doesn't exist

                with SESSIONS_LOGGING_PATH.open("w", encoding="utf-8") as f:
                    stdout_without_vt100 = ANSI_ESCAPE.sub("", logging_connected_players_table.get_string() + "\n" + logging_disconnected_players_table.get_string())
                    f.write(stdout_without_vt100)

            if Settings.DISCORD_PRESENCE:
                if discord_rpc_manager.last_update_time is None or time.perf_counter() - discord_rpc_manager.last_update_time >= 3.0:
                    discord_rpc_manager.update(f"{len(session_connected_sorted)} player{plural(len(session_connected_sorted))} connected in the session.")

            GUIrenderingData.is_rendering_core_ready = True

            time.sleep(0.1)

cls()
title(TITLE)

tshark_restarted_times = 0
global_pps_counter = 0

rendering_core__thread = threading.Thread(target=rendering_core, daemon=True)
rendering_core__thread.start()

iplookup_core__thread = threading.Thread(target=iplookup_core, daemon=True)
iplookup_core__thread.start()

capture_core__thread = threading.Thread(target=capture_core, daemon=True)
capture_core__thread.start()

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QMainWindow, QSizePolicy, QLabel, QHeaderView, QFrame, QSpacerItem, QAbstractItemView
from PyQt6.QtGui import QBrush, QColor, QFont, QCloseEvent, QFontMetrics

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        def add_sort_indicator_by_field_name(table_widget: QTableWidget, field_name: str, ascending = True):
            """
            Sorts the table by the specified field name (header label) and updates the display.

            Args:
                table_widget (QTableWidget): The table widget to sort.
                field_name (str): The name of the column (header label) to sort by.
                ascending (bool): Whether to sort in ascending order. Defaults to True.
            """
            # Find the column index for the given field name
            column_count = table_widget.columnCount()
            column_index = None
            for col in range(column_count):
                header_item = table_widget.horizontalHeaderItem(col)
                if header_item and header_item.text() == field_name:
                    column_index = col
                    break

            if column_index is not None:
                # Set the sort indicator for the column
                sort_order = Qt.SortOrder.AscendingOrder if ascending else Qt.SortOrder.DescendingOrder
                table_widget.horizontalHeader().setSortIndicator(column_index, sort_order)

        self.setWindowTitle(TITLE)
        self.setGeometry(100, 100, 800, 600)  # Initial window size

        # Central widget setup
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create layout
        layout = QVBoxLayout(central_widget)

        # Header text
        self.header_text = QLabel(self.get_header_text())
        self.header_text.setTextFormat(Qt.TextFormat.RichText)
        self.header_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_text.setWordWrap(True)
        self.header_text.setFont(QFont("Courier", 10, QFont.Weight.Bold))  # Optional: Courier-like font for CLI feel
        layout.addWidget(self.header_text)

        # Add a larger vertical spacer (increased height and expanded)
        #layout.addSpacerItem(QSpacerItem(0, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # Custom header for the Session Connected table with matching background as first column
        self.session_connected_header = QLabel(f"Players connected in your session ({len(GUIrenderingData.session_connected)}):")
        self.session_connected_header.setTextFormat(Qt.TextFormat.RichText)
        self.session_connected_header.setStyleSheet("background-color: green; color: white; font-size: 16px; font-weight: bold; padding: 5px;")
        self.session_connected_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_connected_header.setFont(QFont("Courier", 9, QFont.Weight.Bold))

        layout.addWidget(self.session_connected_header)

        # Session Connected table
        self.session_connected = QTableWidget()
        self.session_connected.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_connected.setAlternatingRowColors(True)
        self.session_connected.setColumnCount(len(GUIrenderingData.GUI_CONNECTED_PLAYERS_TABLE__FIELD_NAMES))
        self.session_connected.setHorizontalHeaderLabels(GUIrenderingData.GUI_CONNECTED_PLAYERS_TABLE__FIELD_NAMES)
        self.session_connected.verticalHeader().setVisible(False)  # Hide row index
        self.session_connected.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # Allow user resizing columns
        self.session_connected.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.session_connected.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.session_connected.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Disable editing for both tables
        # Disable editing for both tables (by setting flags for each item)
        #self.set_table_non_editable(self.session_connected)
        #self.set_table_non_editable(self.session_disconnected)

        # Set default sort column and order
        add_sort_indicator_by_field_name(self.session_connected, Settings.STDOUT_FIELD_CONNECTED_PLAYERS_SORTED_BY)
        self.session_connected.setSortingEnabled(False)
        self.session_connected.horizontalHeader().setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        self.session_connected.horizontalHeader().setSortIndicatorShown(True)

        self.initial_adjust_column_widths(self.session_connected)

        # Add Session Connected table to the layout
        layout.addWidget(self.session_connected)

        # Add a larger vertical spacer (increased height and expanded)
        #layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # Add a horizontal line separator
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)  # Optional shadow effect
        layout.addWidget(separator)

        # Add a larger vertical spacer (increased height and expanded)
        #layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        # Custom header for the Session Disconnected table with matching background as first column
        self.session_disconnected_header = QLabel(f"Players who've left your session ({len(GUIrenderingData.session_disconnected)}):")
        self.session_disconnected_header.setTextFormat(Qt.TextFormat.RichText)
        self.session_disconnected_header.setStyleSheet("background-color: red; color: white; font-size: 16px; font-weight: bold; padding: 5px;")
        self.session_disconnected_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_disconnected_header.setFont(QFont("Courier", 9, QFont.Weight.Bold))
        layout.addWidget(self.session_disconnected_header)

        # Session Disconnected table
        self.session_disconnected = QTableWidget()
        self.session_disconnected.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_disconnected.setAlternatingRowColors(True)
        self.session_disconnected.setColumnCount(len(GUIrenderingData.GUI_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES))
        self.session_disconnected.setHorizontalHeaderLabels(GUIrenderingData.GUI_DISCONNECTED_PLAYERS_TABLE__FIELD_NAMES)
        self.session_disconnected.verticalHeader().setVisible(False)  # Hide row index
        self.session_disconnected.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # Allow user resizing columns
        self.session_disconnected.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.session_disconnected.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.session_disconnected.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Set default sort column and order
        add_sort_indicator_by_field_name(self.session_disconnected, Settings.STDOUT_FIELD_DISCONNECTED_PLAYERS_SORTED_BY)
        self.session_disconnected.setSortingEnabled(False)
        self.session_disconnected.horizontalHeader().setSortIndicator(2, Qt.SortOrder.AscendingOrder)
        self.session_disconnected.horizontalHeader().setSortIndicatorShown(True)

        self.initial_adjust_column_widths(self.session_disconnected)

        # Add Session Disconnected table to the layout
        layout.addWidget(self.session_disconnected)

        # Add a larger vertical spacer (increased height and expanded)
        #spacer = QSpacerItem(0, 20, QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        #layout.addItem(spacer)

        # Assign the layout to the central widget
        central_widget.setLayout(layout)

        # Worker thread
        self.worker_thread = WorkerThread(self)
        self.worker_thread.update_header_text_signal.connect(self.update_header_text)
        self.worker_thread.update_session_connected_table_signal.connect(self.update_session_connected_table)
        self.worker_thread.update_session_disconnected_table_signal.connect(self.update_session_disconnected_table)
        self.worker_thread.start()

    def get_sorted_column(self, table: QTableWidget):
        # Get the index of the currently sorted column
        sorted_column_index = table.horizontalHeader().sortIndicatorSection()

        # Get the sort order (ascending or descending)
        sort_order = table.horizontalHeader().sortIndicatorOrder()

        # Get the name of the sorted column
        sorted_column_name = table.horizontalHeaderItem(sorted_column_index).text()

        return sorted_column_name, sort_order

    def get_header_text(self):
        return textwrap.dedent(f"""
            ─────────────────────────────────────────────────────────────────────────────────────────────────<br>
            Welcome in {TITLE} {VERSION}<br>
            The best FREE and Open─Source packet sniffer aka IP grabber, works WITHOUT mods.<br>
            ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─   ─<br>
            Scanning on interface:<span style="color: yellow;">{capture.interface}</span> at IP:<span style="color: yellow;">{displayed_capture_ip_address}</span> (ARP:<span style="color: yellow;">{is_arp_enabled}</span>)<br>
            Packets latency per sec:{GUIrenderingData.latency_color}{GUIrenderingData.avg_latency_rounded}</span>/<span style="color: green;">{Settings.CAPTURE_OVERFLOW_TIMER}</span> (tshark restart{plural(tshark_restarted_times)}:{GUIrenderingData.color_tshark_restarted_time}{tshark_restarted_times}</span>) PPS:{GUIrenderingData.pps_color}{GUIrenderingData.global_pps_rate}</span>{GUIrenderingData.rpc_message}<br>
            ─────────────────────────────────────────────────────────────────────────────────────────────────
        """.removeprefix("\n").removesuffix("\n"))

    def update_header_text(self, text: str):
        self.header_text.setText(text)

    def update_session_connected_table(self, data: list[list[str]]):
        self.session_connected_header.setText(f"Players connected in your session ({len(GUIrenderingData.session_connected)}):")
        self.populate_table(self.session_connected, data, QColor('green'))
        self.adjust_column_widths(self.session_connected)

    def update_session_disconnected_table(self, data: list[list[str]]):
        self.session_disconnected_header.setText(f"Players who've left your session ({len(GUIrenderingData.session_disconnected)}):")
        self.populate_table(self.session_disconnected, data, QColor('red'))
        self.adjust_column_widths(self.session_disconnected)

    def initial_adjust_column_widths(self, table_widget: QTableWidget, margin: int = 30):
        """
        Adjusts column widths based on the header text.
        Each column will be resized to the width of its header text plus a margin for error.

        Args:
            table_widget: The table widget to adjust columns for.
            margin: The additional margin (in pixels) to add to the calculated width.
        """
        for column_index in range(table_widget.columnCount()):
            header_item = table_widget.horizontalHeaderItem(column_index)

            # Get the width of the header text using QFontMetrics
            header_width = QFontMetrics(header_item.font()).boundingRect(header_item.text()).width()

            # Set the column width to the header text width plus the margin
            table_widget.setColumnWidth(column_index, header_width + margin)

    def adjust_column_widths(self, table_widget: QTableWidget, margin: int = 20):
        """
        Adjusts column widths based on the header text and the content of the rows.\n
        Each column will be resized to the width of its header text plus the maximum width of the row text,\n
        plus a margin for error. If the column contains a sort indicator, an additional margin is added.\n
        If the calculated width exceeds a reasonable threshold, it will be resized to a smaller value with the margin.

        Args:
            table_widget: The table widget to adjust columns for.
            margin: The additional margin (in pixels) to add to the calculated width.
        """
        for column_index in range(table_widget.columnCount()):
            # Get the header item and calculate the header text width
            header_item = table_widget.horizontalHeaderItem(column_index)
            header_width = QFontMetrics(header_item.font()).boundingRect(header_item.text()).width()

            # Initialize a variable to track the maximum row content width for this column
            max_row_width = header_width

            # Iterate through each row in the column and calculate the maximum width needed for the row content
            for row_index in range(table_widget.rowCount()):
                if item := table_widget.item(row_index, column_index):  # Check if the cell has content
                    row_width = QFontMetrics(item.font()).boundingRect(item.text()).width()
                    max_row_width = max(max_row_width, row_width)

            # Calculate the total width needed: max(header_width, max_row_width) + margin
            required_width = header_width + margin

            # Check if the column contains a sort indicator, and add extra margin if it does
            if column_index == table_widget.horizontalHeader().sortIndicatorSection():
                required_width += 16  # Add extra margin for sort indicator

            # Get the current column width
            current_width = table_widget.columnWidth(column_index)

            # Resize to the calculated width if it's larger than the current width
            if required_width > current_width:
                table_widget.setColumnWidth(column_index, required_width)

            # Ensure the column width doesn't exceed the max resize limit
            if table_widget.columnWidth(column_index) > max_row_width + margin + 16:
                table_widget.setColumnWidth(column_index, max_row_width + margin + 16)


    #def adjust_column_widths(self, table: QTableWidget):
    #    """Adjust the column widths to fit the max content length."""
    #    for column in range(table.columnCount()):
    #        table.resizeColumnToContents(column)
    #        # Optionally, you can set a minimum width to prevent columns from being too small
    #        table.horizontalHeader().setMinimumSectionSize(10)  # Set a min width for the columns if needed

    def populate_table(self, table: QTableWidget, data: list[list[str]], default_text_color: QColor):
        """
        Populate a QTableWidget with data and set the text color.
        If a `colorama.Fore` color is detected in the text, it overrides the default color.

        Args:
            table (QTableWidget): The table to populate.
            data (list[list[str]]): 2D list containing table content.
            default_text_color (QColor): Default text color if no color is specified.
        """
        from Modules.consts import ANSI_ESCAPE, COLORAMA_FORE_TO_QCOLOR

        table.setRowCount(len(data))
        for row_idx, row_data in enumerate(data):
            for col_idx, col_data in enumerate(row_data):
                item = QTableWidgetItem()
                detected_color = None

                # Check if the cell contains a `colorama.Fore` code
                match = ANSI_ESCAPE.match(col_data)
                if match:
                    escape_sequence = match.group(0)
                    detected_color = COLORAMA_FORE_TO_QCOLOR.get(escape_sequence, default_text_color) # re.compile(r'\x1b\[3\dm')
                    plain_text = re.sub(ANSI_ESCAPE, '', col_data)  # Remove colorama codes
                    item.setText(plain_text)
                else:
                    item.setText(col_data)  # Use raw text if no color is detected

                # Set text color
                item.setForeground(QBrush(detected_color if detected_color else default_text_color))
                table.setItem(row_idx, col_idx, item)

    def closeEvent(self, event: QCloseEvent):
        gui_closed__event.set()  # Signal the thread to stop
        self.worker_thread.quit()  # Stop the QThread
        self.worker_thread.wait()  # Wait for the thread to finish
        event.accept()  # Accept the close event

        time.sleep(1) # Gives a second for Windows processes to closes properly
        terminate_script("EXIT")

class WorkerThread(QThread):
    update_header_text_signal = pyqtSignal(str)
    update_session_connected_table_signal = pyqtSignal(list)
    update_session_disconnected_table_signal = pyqtSignal(list)

    def __init__(self, main_window_instance: MainWindow):
        super().__init__()
        self.main_window = main_window_instance  # Store the MainWindow instance

    def run(self):
        def sort_session_table(session_list: list[Player], sorted_column_name: str, sort_order: str) -> list[Player]:
            """
            Sorts a list of players based on the given column name and sort order.

            Args:
                session_list: The list of players to sort.
                sorted_column_name: The column name to sort by.
                sort_order: The sort order ("ascending" or "descending").

            Returns:
                list[Player]: The sorted list of players.
            """
            import ipaddress

            def sort_order_to_reverse(sort_order: Qt.SortOrder):
                """
                Converts a Qt.SortOrder to a reverse parameter for sorted().

                Args:
                    sort_order: The sort order from Qt (Ascending or Descending).
                """
                return sort_order == Qt.SortOrder.DescendingOrder


            if sorted_column_name == "IP Address":
                return sorted(
                    session_list,
                    key=lambda player: ipaddress.ip_address(player.ip),
                    reverse=sort_order_to_reverse(sort_order)
                )
            elif sorted_column_name in ["First Seen", "Last Rejoin", "Last Seen"]:
                if sort_order == Qt.SortOrder.DescendingOrder:
                    should_reverse = True
                else:
                    should_reverse = False

                return sorted(
                    session_list,
                    key=attrgetter(Settings.stdout_fields_mapping[sorted_column_name]),
                    reverse=should_reverse
                )
            else:
                # Handle sorting for other columns
                return sorted(
                    session_list,
                    key=attrgetter(Settings.stdout_fields_mapping[sorted_column_name]),
                    reverse=sort_order_to_reverse(sort_order)
                )

        def format_player_stdout_datetime(datetime_object: datetime):
            formatted_elapsed = None

            if Settings.STDOUT_DATE_FIELDS_SHOW_ELAPSED:
                elapsed_time = datetime.now() - datetime_object

                hours, remainder = divmod(elapsed_time.total_seconds(), 3600)
                minutes, remainder = divmod(remainder, 60)
                seconds, milliseconds = divmod(remainder * 1000, 1000)

                elapsed_parts: list[str] = []
                if hours >= 1:
                    elapsed_parts.append(f"{int(hours):02}h")
                if elapsed_parts or minutes >= 1:
                    elapsed_parts.append(f"{int(minutes):02}m")
                if elapsed_parts or seconds >= 1:
                    elapsed_parts.append(f"{int(seconds):02}s")
                if not elapsed_parts and milliseconds > 0:
                    elapsed_parts.append(f"{int(milliseconds):03}ms")

                formatted_elapsed = " ".join(elapsed_parts)

                if Settings.STDOUT_DATE_FIELDS_SHOW_DATE is False and Settings.STDOUT_DATE_FIELDS_SHOW_TIME is False:
                    return formatted_elapsed

            parts = []
            if Settings.STDOUT_DATE_FIELDS_SHOW_DATE:
                parts.append(datetime_object.strftime("%m/%d/%Y"))
            if Settings.STDOUT_DATE_FIELDS_SHOW_TIME:
                parts.append(datetime_object.strftime("%H:%M:%S.%f")[:-3])

            formatted_stdout_datetime = " ".join(parts)
            if not formatted_stdout_datetime:
                raise ValueError("Invalid settings: Both date and time are disabled.")

            if formatted_elapsed:
                formatted_stdout_datetime += f" ({formatted_elapsed})"

            return formatted_stdout_datetime

        def format_player_usernames(player_usernames: list[str]):
            return f"{player_color}{', '.join(player_usernames) if player_usernames else 'N/A'}{player_reset}"

        def format_player_pps(player_color: str, is_pps_first_calculation: bool, pps_rate: int):
            if pps_rate == 0:
                if is_pps_first_calculation:
                    pps_color = player_color
                else:
                    pps_color = Fore.RED
            elif pps_rate == 1:
                pps_color = Fore.YELLOW
            else:
                pps_color = player_color

            return f"{pps_color}{pps_rate}{Fore.RESET}"

        def format_player_ip(player_ip: str):
            if (
                SessionHost.player
                and SessionHost.player.ip == player_ip
            ):
                return f"{player_ip} 👑"
            return player_ip

        def format_player_intermediate_ports(player_ports: Player_Ports):
            player_ports.intermediate = [port for port in reversed(player_ports.list) if port not in {player_ports.first, player_ports.last}]
            if player_ports.intermediate:
                return ", ".join(map(str, player_ports.intermediate))
            else:
                return ""

        while not gui_closed__event.is_set():
            updated_session_connected_table = []
            updated_session_disconnected_table = []

            session_connected_sorted_column_name, session_connected_sort_order = self.main_window.get_sorted_column(self.main_window.session_connected)
            session_disconnected_sorted_column_name, session_disconnected_sort_order = self.main_window.get_sorted_column(self.main_window.session_disconnected)

            session_connected_sorted = sort_session_table(
                GUIrenderingData.session_connected,
                session_connected_sorted_column_name,
                session_connected_sort_order
            )
            session_disconnected_sorted = sort_session_table(
                GUIrenderingData.session_disconnected,
                session_disconnected_sorted_column_name,
                session_disconnected_sort_order
            )

            for player in session_connected_sorted:
                if (
                    Settings.USERIP_ENABLED
                    and player.userip.usernames
                ):
                    player_color = Fore.WHITE + getattr(Back, player.userip.settings.COLOR) + Style.BRIGHT
                    player_reset = Fore.RESET + Back.RESET + Style.RESET_ALL
                else:
                    player_color = Fore.GREEN
                    player_reset = Fore.RESET

                row = []
                row.append(f"{player_color}{format_player_stdout_datetime(player.datetime.first_seen)}{player_reset}")
                row.append(f"{player_color}{format_player_stdout_datetime(player.datetime.last_rejoin)}{player_reset}")
                if Settings.USERIP_ENABLED:
                    row.append(f"{player_color}{format_player_usernames(player.usernames)}{player_reset}")
                row.append(f"{player_color}{player.rejoins}{player_reset}")
                row.append(f"{player_color}{player.total_packets}{player_reset}")
                row.append(f"{player_color}{player.packets}{player_reset}")
                row.append(f"{format_player_pps(player_color, player.pps.is_first_calculation, player.pps.rate)}")
                row.append(f"{player_color}{format_player_ip(player.ip)}{player_reset}")
                if "Last Port" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.ports.last}{player_reset}")
                if "Intermediate Ports" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{format_player_intermediate_ports(player.ports)}{player_reset}")
                if "First Port" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.ports.first}{player_reset}")
                if "Continent" not in GUIrenderingData.FIELDS_TO_HIDE:
                    if Settings.STDOUT_FIELD_SHOW_CONTINENT_CODE:
                        row.append(f"{player_color}{player.iplookup.ipapi.compiled.continent} ({player.iplookup.ipapi.compiled.continent_code}){player_reset}")
                    else:
                        row.append(f"{player_color}{player.iplookup.ipapi.compiled.continent}{player_reset}")
                if "Country" not in GUIrenderingData.FIELDS_TO_HIDE:
                    if Settings.STDOUT_FIELD_SHOW_COUNTRY_CODE:
                        row.append(f"{player_color}{player.iplookup.maxmind.compiled.country} ({player.iplookup.maxmind.compiled.country_code}){player_reset}")
                    else:
                        row.append(f"{player_color}{player.iplookup.maxmind.compiled.country}{player_reset}")
                if "Region" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.region}{player_reset}")
                if "R. Code" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.region_code}{player_reset}")
                if "City" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.maxmind.compiled.city}{player_reset}")
                if "District" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.district}{player_reset}")
                if "ZIP Code" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.zip_code}{player_reset}")
                if "Lat" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.lat}{player_reset}")
                if "Lon" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.lon}{player_reset}")
                if "Time Zone" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.time_zone}{player_reset}")
                if "Offset" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.offset}{player_reset}")
                if "Currency" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.currency}{player_reset}")
                if "Organization" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.org}{player_reset}")
                if "ISP" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.isp}{player_reset}")
                if "ASN / ISP" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.maxmind.compiled.asn}{player_reset}")
                if "AS" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled._as}{player_reset}")
                if "ASN" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.as_name}{player_reset}")
                if "Mobile" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.mobile}{player_reset}")
                if "VPN" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.proxy}{player_reset}")
                if "Hosting" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.hosting}{player_reset}")

                updated_session_connected_table.append(row)

            for player in session_disconnected_sorted:
                if (
                    Settings.USERIP_ENABLED
                    and player.userip.usernames
                ):
                    player_color = Fore.WHITE + getattr(Back, player.userip.settings.COLOR) + Style.BRIGHT
                    player_reset = Fore.RESET + Back.RESET + Style.RESET_ALL
                else:
                    player_color = Fore.RED
                    player_reset = Fore.RESET

                row = []
                row.append(f"{player_color}{format_player_stdout_datetime(player.datetime.first_seen)}{player_reset}")
                row.append(f"{player_color}{format_player_stdout_datetime(player.datetime.last_rejoin)}{player_reset}")
                row.append(f"{player_color}{format_player_stdout_datetime(player.datetime.last_seen)}{player_reset}")
                if Settings.USERIP_ENABLED:
                    row.append(f"{player_color}{format_player_usernames(player.usernames)}{player_reset}")
                row.append(f"{player_color}{player.rejoins}{player_reset}")
                row.append(f"{player_color}{player.total_packets}{player_reset}")
                row.append(f"{player_color}{player.packets}{player_reset}")
                row.append(f"{player_color}{player.ip}{player_reset}")
                if "Last Port" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.ports.last}{player_reset}")
                if "Intermediate Ports" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{format_player_intermediate_ports(player.ports)}{player_reset}")
                if "First Port" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.ports.first}{player_reset}")
                if "Continent" not in GUIrenderingData.FIELDS_TO_HIDE:
                    if Settings.STDOUT_FIELD_SHOW_CONTINENT_CODE:
                        row.append(f"{player_color}{player.iplookup.ipapi.compiled.continent} ({player.iplookup.ipapi.compiled.continent_code}){player_reset}")
                    else:
                        row.append(f"{player_color}{player.iplookup.ipapi.compiled.continent}{player_reset}")
                if "Country" not in GUIrenderingData.FIELDS_TO_HIDE:
                    if Settings.STDOUT_FIELD_SHOW_COUNTRY_CODE:
                        row.append(f"{player_color}{player.iplookup.maxmind.compiled.country} ({player.iplookup.maxmind.compiled.country_code}){player_reset}")
                    else:
                        row.append(f"{player_color}{player.iplookup.maxmind.compiled.country}{player_reset}")
                if "Region" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.region}{player_reset}")
                if "R. Code" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.region_code}{player_reset}")
                if "City" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.maxmind.compiled.city}{player_reset}")
                if "District" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.district}{player_reset}")
                if "ZIP Code" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.zip_code}{player_reset}")
                if "Lat" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.lat}{player_reset}")
                if "Lon" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.lon}{player_reset}")
                if "Time Zone" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.time_zone}{player_reset}")
                if "Offset" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.offset}{player_reset}")
                if "Currency" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.currency}{player_reset}")
                if "Organization" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.org}{player_reset}")
                if "ISP" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.isp}{player_reset}")
                if "ASN / ISP" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.maxmind.compiled.asn}{player_reset}")
                if "AS" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled._as}{player_reset}")
                if "ASN" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.as_name}{player_reset}")
                if "Mobile" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.mobile}{player_reset}")
                if "VPN" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.proxy}{player_reset}")
                if "Hosting" not in GUIrenderingData.FIELDS_TO_HIDE:
                    row.append(f"{player_color}{player.iplookup.ipapi.compiled.hosting}{player_reset}")

                updated_session_disconnected_table.append(row)

            header_text = self.main_window.get_header_text()  # Call the method on the instance
            self.update_header_text_signal.emit(header_text)
            self.update_session_connected_table_signal.emit(updated_session_connected_table)
            self.update_session_disconnected_table_signal.emit(updated_session_disconnected_table)

            time.sleep(0.1)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    while not GUIrenderingData.is_rendering_core_ready:
        time.sleep(0.1)

    window = MainWindow()
    window.show()

    app.exec()