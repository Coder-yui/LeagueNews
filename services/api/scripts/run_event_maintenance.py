from app.services.maintenance import run_daily_maintenance


if __name__ == "__main__":
    affected = run_daily_maintenance(force=True)
    print(f"expired_unconfirmed={len(affected)}")
