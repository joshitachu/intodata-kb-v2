"""Sample Python ETL script."""

import os
import psycopg2


def extract_data():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "mydb"),
    )
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM customers WHERE active = true")
    return cursor.fetchall()


def transform_data(rows):
    return [
        {"name": r[1].upper(), "email": r[2].lower()}
        for r in rows
    ]


def load_data(records):
    print(f"Loading {len(records)} records")


if __name__ == "__main__":
    data = extract_data()
    transformed = transform_data(data)
    load_data(transformed)
