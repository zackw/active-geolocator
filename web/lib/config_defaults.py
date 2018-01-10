"""
This module provides defaults for all configuration parameters.
"""

# Directories
BASE_DIR    = None # defaults to instance directory
REPORT_DIR  = 'reports'
GPG_HOME    = 'gnupg2'

# Read-only data
ALL_LANDMARKS   = 'all-landmarks.csv'
CONTINENT_MARKS = 'continent-marks.csv'
GEOIP_DB        = '/var/lib/GeoIP/GeoLite2-City.mmdb'

# Misc
ENCRYPT_TO  = None # set this to a GnuPG keyid to enable encryption
