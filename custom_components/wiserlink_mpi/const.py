"""Constants for WiserLink MPI."""

DOMAIN = "wiserlink_mpi"
PLATFORMS = ["binary_sensor", "sensor"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_LOAD_NAME_PREFIX = "load_name_"
CONF_METER_ENABLED_PREFIX = "meter_enabled_"
CONF_METER_UNIT_PREFIX = "meter_unit_"
CONF_GAS_DRIFT_CONTROL = "gas_drift_control"
CONF_GAS_TARGET_TIME = "gas_target_time"
CONF_GAS_TOLERANCE_MINUTES = "gas_tolerance_minutes"
CONF_GAS_CONTROL_TIME = "gas_control_time"

METER_UNIT_AUTO = "auto"
METER_UNIT_KWH = "kwh"
METER_UNIT_WH = "wh"
METER_UNIT_M3 = "m3"

# Legacy options kept for backward compatibility with releases <= 0.7.1.
CONF_ENABLE_GAS = "enable_gas"
CONF_ENABLE_WATER = "enable_water"

DEFAULT_SCAN_INTERVAL = 5
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_PORT = 80
DEFAULT_GAS_DRIFT_CONTROL = False
DEFAULT_GAS_TARGET_TIME = "23:45:00"
DEFAULT_GAS_TOLERANCE_MINUTES = 15
DEFAULT_GAS_CONTROL_TIME = "23:30:00"

USAGE_METER_PATH = "/vesta/UsageMeter"
SERVICE_SEND_COMMAND = "send_command"
SERVICE_CONFIGURE_MPR = "configure_mpr"
SERVICE_DELETE_MPR = "delete_mpr"
SERVICE_REBOOT_MIP = "reboot_mip"

SEM_IDENTIFICATION_PATH = "/vesta/SemIdentification"
MPR_INSTANCES_PATH = "/vesta/MpeEndpoint/instances"
