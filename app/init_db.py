from app.db import init_collections
from app.core.log import get_logger

logger = get_logger("init_db")


def main():
    logger.info("Initializing collections...")
    result = init_collections()
    logger.info("Init complete", extra={"ctx": result})


if __name__ == "__main__":
    main()
