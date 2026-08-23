"""Constants for WiserLink MPI."""

DOMAIN = "wiserlink_mpi"
PLATFORMS = ["binary_sensor", "sensor"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_LOAD_NAME_PREFIX = "load_name_"
CONF_METER_ENABLED_PREFIX = "meter_enabled_"

# Legacy options kept for backward compatibility with releases <= 0.7.1.
CONF_ENABLE_GAS = "enable_gas"
CONF_ENABLE_WATER = "enable_water"

DEFAULT_SCAN_INTERVAL = 5
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_PORT = 80

USAGE_METER_PATH = "/vesta/UsageMeter"
SERVICE_SEND_COMMAND = "send_command"
SERVICE_CONFIGURE_MPR = "configure_mpr"
SERVICE_DELETE_MPR = "delete_mpr"

SEM_IDENTIFICATION_PATH = "/vesta/SemIdentification"
MPR_INSTANCES_PATH = "/vesta/MpeEndpoint/instances"
