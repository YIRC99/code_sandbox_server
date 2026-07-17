import asyncio
import sys

import uvicorn

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


if __name__ == "__main__":
    uvicorn.run(
        "src.sandbox.main:app",
        host="127.0.0.1",
        port=32004,
        reload=True,
        reload_dirs=["src"],
    )
