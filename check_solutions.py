import os
import sys
from dotenv import load_dotenv

sys.path.append("/home/rohit/Downloads/KodeinKGP/aeropt/Aeropt-Agent")
load_dotenv("/home/rohit/Downloads/KodeinKGP/aeropt/Aeropt-Agent/.env")

from db.supabase_client import db

res = (
    db.client.table("content")
    .select("content_id, core_text, solution, has_image_solution")
    .eq("content_type", "practice")
    .limit(10)
    .execute()
)
if res.data:
    for row in res.data:
        print(f"ID: {row['content_id']}")
        print(f"Solution: {repr(row['solution'])[:100]}")
        print(f"Has Solution Image: {row['has_image_solution']}")
        print("-" * 40)
else:
    print("No data found!")
