"""
This module provides defaults for all configuration parameters.
"""

# Directories
BASE_DIR    = None # defaults to instance directory
REPORT_DIR  = 'reports'
GPG_HOME    = 'gnupg2'

# Geolocation database
GEOIP_DB            = '/var/lib/GeoIP/GeoLite2-City.mmdb'

# psycopg2.connect kwargs to connect to the landmark database
LANDMARK_DB         = { 'dbname': 'landmark_db' }

# connection pool configuration for psycopg2
LANDMARK_DB_MINCONN = 1
LANDMARK_DB_MAXCONN = 10
LANDMARK_DB_MAXIDLE = 600  # per-connection max idle time in seconds

# Misc
ENCRYPT_TO  = None # set this to a GnuPG keyid to enable encryption
