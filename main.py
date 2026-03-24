#!/usr/bin/python3
import logging
import os

import uvicorn


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    uvicorn.run(
        "app.web:app",
        host=host,
        port=port,
        reload=False,
        log_level="warning",
    )
