import logging
import sys

class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        level = record.levelname
        color = self.COLORS.get(level)
        # 只在 TTY 輸出時加色，避免 redirect 亂碼
        if color and sys.stderr.isatty():
            return f"{color}{message}{self.RESET}"
        return message


def setup_logger(name: str = "vast_search", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        logger.setLevel(level)
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler()
    fmt = "[%(asctime)s] [%(levelname)s] %(message)s"
    handler.setFormatter(ColoredFormatter(fmt))
    logger.addHandler(handler)
    return logger
