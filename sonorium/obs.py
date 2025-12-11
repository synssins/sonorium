from fmtr.tools import logging, debug, Constants

from sonorium.paths import paths
from sonorium.version import __version__

debug.trace()

logger = logging.get_logger(
    name=paths.name_ns,
    stream=Constants.DEVELOPMENT,
    version=__version__,
)
