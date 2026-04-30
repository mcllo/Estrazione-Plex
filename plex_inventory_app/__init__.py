__version__ = "0.1.0"

# Keep Plex API network calls from waiting too long when Plex.tv or a
# saved token is slow/unreachable.  The app already runs these calls in a
# QThread, but a bounded timeout avoids leaving the loading state active for
# an unexpectedly long time while fetching servers/resources.
try:
    import plexapi.config as _plexapi_config

    _plexapi_config.TIMEOUT = 6
except Exception:
    pass
