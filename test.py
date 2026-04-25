import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Ask the API for just 1 row of the new view
response = supabase.table('game_recency_stats').select('*').limit(1).execute()

print("API Response Data:")
print(response.data)