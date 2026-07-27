import asyncio

from app.services.collection_scheduler import scheduler_loop


def main() -> None:
    asyncio.run(scheduler_loop())


if __name__ == "__main__":
    main()
