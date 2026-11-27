"""Lyria: explicit lyric structure, visual evidence and translation mapping."""
__version__ = "v0.3.4"

# Keep the numeric release parseable; editions have an explicit UI identity.
__edition__ = "测试版"
__display_version__ = __version__ + (" " + __edition__ if __edition__ else "")
