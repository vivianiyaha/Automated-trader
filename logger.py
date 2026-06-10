def log_trade_csv(data: dict) -> None:
    """Append a trade event to the CSV log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    file_exists = os.path.isfile(CSV_LOG_FILE)
    with open(CSV_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)
