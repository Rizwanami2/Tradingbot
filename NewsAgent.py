"""
DEPRECATED
==========
This file is kept only for backward compatibility.

Please use the improved module instead:

    from news_agent import NewsAgent

The improved version includes better calendar filtering, caching,
and shock detection.
"""

# Re-export from the new module so old imports still work
try:
    from news_agent import NewsAgent, Dir, Vote
except ImportError:
    raise ImportError(
        "NewsAgent.py is deprecated. Please use: from news_agent import NewsAgent"
    )
