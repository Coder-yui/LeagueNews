import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.services.collection_scheduler import scheduler_loop as collection_scheduler_loop
from app.services.daily_report_scheduler import daily_report_scheduler_loop


logger = logging.getLogger(__name__)


async def supervise(name: str, loop_factory: Callable[[], Awaitable[None]]) -> None:
    while True:
        try:
            await loop_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s stopped unexpectedly; restarting", name)
            await asyncio.sleep(5)


async def scheduler_main() -> None:
    await asyncio.gather(
        supervise("collection scheduler", collection_scheduler_loop),
        supervise("daily report scheduler", daily_report_scheduler_loop),
    )


def main() -> None:
    asyncio.run(scheduler_main())


if __name__ == "__main__":
    main()
