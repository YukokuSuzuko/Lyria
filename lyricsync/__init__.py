"""Lyria: explicit lyric structure, visual evidence and translation mapping."""
__version__ = "v0.28"

# Keep the numeric release parseable; editions have an explicit UI identity.
__edition__ = ""
__display_version__ = __version__ + (" " + __edition__ if __edition__ else "")
