"""assign IANA time zones to existing regional profiles

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE users SET timezone = CASE country_code
          WHEN 'RU' THEN 'Europe/Moscow' WHEN 'BY' THEN 'Europe/Minsk'
          WHEN 'KZ' THEN 'Asia/Almaty' WHEN 'UA' THEN 'Europe/Kyiv'
          WHEN 'AM' THEN 'Asia/Yerevan' WHEN 'AZ' THEN 'Asia/Baku'
          WHEN 'GE' THEN 'Asia/Tbilisi' WHEN 'KG' THEN 'Asia/Bishkek'
          WHEN 'MD' THEN 'Europe/Chisinau' WHEN 'TJ' THEN 'Asia/Dushanbe'
          WHEN 'TM' THEN 'Asia/Ashgabat' WHEN 'UZ' THEN 'Asia/Tashkent'
          WHEN 'PL' THEN 'Europe/Warsaw' WHEN 'DE' THEN 'Europe/Berlin'
          WHEN 'FR' THEN 'Europe/Paris' WHEN 'IT' THEN 'Europe/Rome'
          WHEN 'ES' THEN 'Europe/Madrid' WHEN 'CZ' THEN 'Europe/Prague'
          WHEN 'SK' THEN 'Europe/Bratislava' WHEN 'AT' THEN 'Europe/Vienna'
          WHEN 'BE' THEN 'Europe/Brussels' WHEN 'NL' THEN 'Europe/Amsterdam'
          WHEN 'SE' THEN 'Europe/Stockholm' WHEN 'NO' THEN 'Europe/Oslo'
          WHEN 'FI' THEN 'Europe/Helsinki' WHEN 'DK' THEN 'Europe/Copenhagen'
          WHEN 'HU' THEN 'Europe/Budapest' WHEN 'RO' THEN 'Europe/Bucharest'
          WHEN 'BG' THEN 'Europe/Sofia' WHEN 'LT' THEN 'Europe/Vilnius'
          WHEN 'LV' THEN 'Europe/Riga' WHEN 'EE' THEN 'Europe/Tallinn'
          ELSE timezone END
    """)


def downgrade() -> None:
    pass
