"""Constants for WiserLink MPI."""

DOMAIN = "wiserlink_mpi"
PLATFORMS = ["binary_sensor", "sensor"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_ENABLE_GAS = "enable_gas"
CONF_ENABLE_WATER = "enable_water"
CONF_LOAD_NAME_PREFIX = "load_name_"
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_USERNAME = "admin"
DEFAULT_PORT = 80

USAGE_METER_PATH = "/vesta/UsageMeter"
SERVICE_SEND_COMMAND = "send_command"

DEFAULT_METER_NAMES = {
    0: "Chargeurs voitures",
    1: "Domotique",
    2: "Cuisine",
    3: "Buanderie",
    5: "Climatisation",
    9: "Téléinformation",
    10: "Gaz",
    11: "Eau de ville",
}
