import asyncio

from app.services.automatic_pipeline import worker_loop


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
