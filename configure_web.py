"""Generate browser-safe configuration. Never falls back to SUPABASE_KEY."""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def public_config():
    load_dotenv(ROOT / '.env')
    raw_url = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    url = raw_url.removesuffix('/rest/v1')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
        raise ValueError('Set SUPABASE_URL to your HTTPS Supabase project URL.')
    key = (os.getenv('SUPABASE_PUBLISHABLE_KEY') or os.getenv('SUPABASE_ANON_KEY') or '').strip()
    if not key:
        raise ValueError('Add SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY to .env. SUPABASE_KEY is intentionally not used.')
    if not key.startswith('sb_publishable_'):
        try:
            encoded = key.split('.')[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            if payload.get('role') != 'anon':
                raise ValueError()
        except (ValueError, IndexError, KeyError):
            raise ValueError('Browser configuration requires a publishable key or an anon-role JWT; secret/service-role keys are rejected.') from None
    return {'supabaseUrl': url, 'supabaseKey': key}


if __name__ == '__main__':
    try:
        config = public_config()
    except ValueError as error:
        raise SystemExit(str(error))
    (ROOT / 'web' / 'config.js').write_text(
        '// Public browser configuration. No server secrets.\nwindow.NUTRITION_CONFIG = '
        + json.dumps(config, indent=2) + ';\n', encoding='utf-8')
    print('Configured web/config.js with public settings. No secret values printed.')
