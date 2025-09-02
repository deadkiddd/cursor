from supabase import create_client, Client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_SECRET = os.getenv("SUPABASE_API_SECRET")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_API_SECRET)
