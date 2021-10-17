"""Support for Pioneer Network Receivers."""
import logging
import telnetlib

import voluptuous as vol

from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerEntity
from homeassistant.components.media_player.const import (
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
    SUPPORT_SELECT_SOUND_MODE,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_TIMEOUT,
    STATE_OFF,
    STATE_ON,
)
import homeassistant.helpers.config_validation as cv

from .sound_modes import SOUND_MODES

_LOGGER = logging.getLogger(__name__)

CONF_SOURCES = "sources"
CONF_SOUND_MODES = "sound_modes"

DEFAULT_NAME = "Pioneer AVR"
DEFAULT_PORT = 23  # telnet default. Some Pioneer AVRs use 8102
DEFAULT_TIMEOUT = None
DEFAULT_SOURCES = {}
DEFAULT_SOUND_MODES = []

SUPPORT_PIONEER = (
    SUPPORT_PAUSE
    | SUPPORT_VOLUME_SET
    | SUPPORT_VOLUME_STEP
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_PLAY
    | SUPPORT_SELECT_SOUND_MODE
)

MAX_VOLUME = 185
MAX_SOURCE_NUMBERS = 60


def sound_mode_name_to_number(value):
    if not isinstance(value, (list, dict)):
        raise vol.Invalid("sound_modes should be a list or dict")

    if isinstance(value, dict):
        return value

    sound_mode_name_to_number = {}
    for name in value:
        if name not in SOUND_MODES:
            raise vol.Invalid(f'given sound mode "{name}" does not exist')
        sound_mode_name_to_number[name] = SOUND_MODES[name]

    return sound_mode_name_to_number


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.socket_timeout,
        vol.Optional(CONF_SOURCES, default=DEFAULT_SOURCES): {cv.string: cv.string},
        vol.Optional(CONF_SOUND_MODES, default=DEFAULT_SOUND_MODES): sound_mode_name_to_number,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Pioneer platform."""
    pioneer = PioneerDevice(
        config[CONF_NAME],
        config[CONF_HOST],
        config[CONF_PORT],
        config[CONF_TIMEOUT],
        config[CONF_SOURCES],
        config[CONF_SOUND_MODES],
    )

    if pioneer.update():
        add_entities([pioneer])


class PioneerDevice(MediaPlayerEntity):
    """Representation of a Pioneer device."""

    def __init__(self, name, host, port, timeout, sources, sound_modes):
        """Initialize the Pioneer device."""
        self._name = name
        self._host = host
        self._port = port
        self._timeout = timeout
        self._pwstate = "PWR1"
        self._volume = 0
        self._muted = False
        self._selected_source = None
        self._source_name_to_number = sources
        self._source_number_to_name = {v: k for k, v in sources.items()}
        self._selected_sound_mode = None
        self._sound_mode_name_to_number = sound_modes
        self._sound_mode_number_to_name = {v: k for k, v in sound_modes.items()}

    @classmethod
    def telnet_request(cls, telnet, command, expected_prefix):
        """Execute `command` and return the response."""
        try:
            telnet.write(command.encode("ASCII") + b"\r")
        except telnetlib.socket.timeout:
            _LOGGER.debug("Pioneer command %s timed out", command)
            return None

        # The receiver will randomly send state change updates, make sure
        # we get the response we are looking for
        for _ in range(3):
            result = telnet.read_until(b"\r\n", timeout=0.2).decode("ASCII").strip()
            if result.startswith(expected_prefix):
                return result

        return None

    def telnet_command(self, command):
        """Establish a telnet connection and sends command."""
        try:
            try:
                telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
            except OSError:
                _LOGGER.warning("Pioneer %s refused connection", self._name)
                return
            telnet.write(command.encode("ASCII") + b"\r")
            telnet.read_very_eager()  # skip response
            telnet.close()
        except telnetlib.socket.timeout:
            _LOGGER.debug("Pioneer %s command %s timed out", self._name, command)

    def update(self):
        """Get the latest details from the device."""
        try:
            telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
        except OSError:
            _LOGGER.warning("Pioneer %s refused connection", self._name)
            return False

        pwstate = self.telnet_request(telnet, "?P", "PWR")
        if pwstate:
            self._pwstate = pwstate

        volume_str = self.telnet_request(telnet, "?V", "VOL")
        self._volume = int(volume_str[3:]) / MAX_VOLUME if volume_str else None

        muted_value = self.telnet_request(telnet, "?M", "MUT")
        self._muted = (muted_value == "MUT0") if muted_value else None

        sound_mode_value = self.telnet_request(telnet, "?S", "SR")
        if sound_mode_value is not None:
            self._selected_sound_mode = self._sound_mode_number_to_name[sound_mode_value.removeprefix("SR")]
        else:
            self._selected_sound_mode

        # Build the source name dictionaries if necessary
        if not self._source_name_to_number:
            for i in range(MAX_SOURCE_NUMBERS):
                result = self.telnet_request(telnet, f"?RGB{str(i).zfill(2)}", "RGB")

                if not result:
                    continue

                source_name = result[6:]
                source_number = str(i).zfill(2)

                self._source_name_to_number[source_name] = source_number
                self._source_number_to_name[source_number] = source_name

        source_number = self.telnet_request(telnet, "?F", "FN")

        if source_number:
            self._selected_source = self._source_number_to_name.get(source_number[2:])
        else:
            self._selected_source = None

        telnet.close()
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwstate == "PWR2":
            return STATE_OFF
        if self._pwstate == "PWR1":
            return STATE_OFF
        if self._pwstate == "PWR0":
            return STATE_ON

        return None

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_PIONEER

    @property
    def source(self):
        """Return the current input source."""
        return self._selected_source

    @property
    def source_list(self):
        """List of available input sources."""
        return list(self._source_name_to_number)

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._selected_source

    @property
    def sound_mode(self):
        """The current sound mode of the media player"""
        return self._selected_sound_mode

    @property
    def sound_mode_list(self):
        """List of available sound modes."""
        return list(self._sound_mode_name_to_number)

    def turn_off(self):
        """Turn off media player."""
        self.telnet_command("PF")

    def volume_up(self):
        """Volume up media player."""
        self.telnet_command("VU")

    def volume_down(self):
        """Volume down media player."""
        self.telnet_command("VD")

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        # 60dB max
        self.telnet_command(f"{round(volume * MAX_VOLUME):03}VL")

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        self.telnet_command("MO" if mute else "MF")

    def turn_on(self):
        """Turn the media player on."""
        self.telnet_command("PO")

    def select_source(self, source):
        """Select input source."""
        self.telnet_command(f"{self._source_name_to_number.get(source)}FN")

    def select_sound_mode(self, sound_mode):
        """Select sound mode."""
        self.telnet_command(f"{self._sound_mode_name_to_number[sound_mode]}SR")
